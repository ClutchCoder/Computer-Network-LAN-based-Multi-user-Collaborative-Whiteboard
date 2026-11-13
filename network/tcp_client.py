"""
Multi-Threaded TCP Whiteboard Client with Room Coordination & Sequence Number Tracking.

Computer Networks Principle:
Implements asynchronous socket communication on the client-side:
  1. Active Connection: socket.socket(AF_INET, SOCK_STREAM), connect((host, port)).
  2. Room Authentication: Joins isolated sessions using room codes and passcodes.
  3. TCP_NODELAY: Explicitly disabled Nagle's buffering algorithm so small mouse movement
     packets are sent immediately without latency.
  4. Monotonic Sequence Tracking: Observes server-assigned sequence numbers on incoming
     broadcasts to verify strict total ordering.
  5. State Recovery & Reconnection: Reconnects seamlessly upon network interruptions and
     recovers latest whiteboard and chat state.
  6. Thread Separation & Queueing: Decouples socket reads from Tkinter GUI using a thread-safe
     FIFO queue, preventing cross-thread race conditions and UI lockups.
"""

import logging
import queue
import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import config
from protocol.framer import (
    ConnectionClosedError,
    FrameError,
    read_framed_message,
    send_framed_message,
)
from protocol.messages import (
    MessageType,
    ProtocolMessage,
    create_chat_msg,
    create_clear_msg,
    create_draw_segment_msg,
    create_draw_stroke_msg,
    create_erase_msg,
    create_heartbeat_msg,
    create_join_msg,
    create_load_state_msg,
    create_redo_msg,
    create_shape_msg,
    create_undo_msg,
    deserialize_message,
    serialize_message,
)
from state.action import WhiteboardAction

logger = logging.getLogger("WhiteboardClient")


class WhiteboardClient:
    """
    Manages client-side TCP networking, room coordination, message serialization,
    sequence number tracking, and inbound event queueing.
    """

    def __init__(self, username: str = "User"):
        self.username = username
        self.client_id = f"client_{uuid.uuid4().hex[:8]}"
        self.user_id: Optional[str] = None
        self.assigned_color: str = "#000000"

        # Room Session State
        self.room_code: str = "MAIN"
        self.room_passcode: str = ""
        self.last_seq_num: int = 0
        self.initial_chat_history: List[Dict[str, Any]] = []
        self.host_id: Optional[str] = None
        self.is_host: bool = False

        self.server_host: str = ""
        self.server_port: int = config.DEFAULT_SERVER_PORT

        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._send_lock = threading.Lock()

        # Thread-safe queue for messages consumed by GUI
        self.inbound_queue: queue.Queue = queue.Queue()

        self._receiver_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_disconnected: Optional[Callable[[], None]] = None

    def is_connected(self) -> bool:
        return self._connected

    def connect(
        self,
        host: str,
        port: int = config.DEFAULT_SERVER_PORT,
        room_code: str = "MAIN",
        passcode: str = "",
        timeout: float = 5.0,
        is_reconnect: bool = False,
    ) -> Tuple[bool, str, list, list]:
        """
        Establishes TCP connection to server, authenticates to the target room,
        and requests initial canvas and chat state.
        Returns a tuple: (success: bool, error_message: str, initial_users: list, initial_actions: list)
        """
        self.server_host = host
        self.server_port = port
        self.room_code = (room_code or "MAIN").upper().strip()
        self.room_passcode = passcode

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(timeout)

            # Disable Nagle's algorithm for interactive real-time responsiveness
            if config.ENABLE_TCP_NODELAY:
                self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            logger.info(f"Connecting to Whiteboard Server at {host}:{port} [Room: {self.room_code}]...")
            self._sock.connect((host, port))

            # Send JOIN handshake with room code and authentication
            join_msg = create_join_msg(
                username=self.username,
                client_id=self.client_id,
                room_code=self.room_code,
                passcode=self.room_passcode,
                is_reconnect=is_reconnect,
                last_seq_num=self.last_seq_num,
            )
            send_framed_message(self._sock, serialize_message(join_msg))

            # Await JOIN_ACK or JOIN_REJECT
            ack_raw = read_framed_message(self._sock)
            ack_msg = deserialize_message(ack_raw)

            if ack_msg.msg_type == MessageType.JOIN_REJECT.value:
                reason = ack_msg.payload.get("reason", "Connection rejected by server.")
                self.disconnect()
                return False, reason, [], []

            if ack_msg.msg_type != MessageType.JOIN_ACK.value:
                self.disconnect()
                return False, f"Unexpected response from server: {ack_msg.msg_type}", [], []

            # Handshake successful - unpack authoritative state
            payload = ack_msg.payload
            self.user_id = payload.get("user_id")
            self.assigned_color = payload.get("color", "#000000")
            self.room_code = payload.get("room_code", self.room_code)
            self.host_id = payload.get("host_id")
            self.is_host = payload.get("is_host", (self.user_id == self.host_id))
            self.last_seq_num = ack_msg.seq_num or payload.get("seq_num", 0)
            self.initial_chat_history = payload.get("chat_history", [])

            initial_users = payload.get("users", [])
            initial_actions = payload.get("actions", [])

            self._connected = True
            self._sock.settimeout(config.SOCKET_TIMEOUT)

            # Start background listener thread
            self._receiver_thread = threading.Thread(
                target=self._receiver_loop, daemon=True, name="Client-TCP-Receiver"
            )
            self._receiver_thread.start()

            # Start keep-alive heartbeat thread
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True, name="Client-Heartbeat"
            )
            self._heartbeat_thread.start()

            logger.info(
                f"Successfully joined room '{self.room_code}' as {self.username} (ID: {self.user_id}) | Seq: {self.last_seq_num}"
            )
            return True, "", initial_users, initial_actions

        except socket.timeout:
            self.disconnect()
            return False, f"Connection to {host}:{port} timed out.", [], []
        except ConnectionRefusedError:
            self.disconnect()
            return False, f"Connection refused at {host}:{port}. Is the server running?", [], []
        except Exception as e:
            self.disconnect()
            return False, f"Connection error: {e}", [], []

    def reconnect(self) -> Tuple[bool, str, list, list]:
        """Attempts to reconnect to the previous host and room, requesting state recovery."""
        if not self.server_host:
            return False, "No previous server address recorded.", [], []
        return self.connect(
            host=self.server_host,
            port=self.server_port,
            room_code=self.room_code,
            passcode=self.room_passcode,
            is_reconnect=True,
        )

    def _receiver_loop(self) -> None:
        """Background thread continuously reading framed messages from server."""
        while self._connected:
            try:
                raw_bytes = read_framed_message(self._sock)
                if not raw_bytes:
                    continue

                msg = deserialize_message(raw_bytes)

                # Track authoritative monotonically increasing sequence number
                if msg.seq_num is not None:
                    self.last_seq_num = max(self.last_seq_num, msg.seq_num)

                # Track host state and host migration
                if msg.msg_type in (MessageType.PARTICIPANT_JOINED.value, "PARTICIPANT_JOINED", "USER_JOINED"):
                    if msg.payload.get("host_id"):
                        self.host_id = msg.payload.get("host_id")
                        self.is_host = bool(self.user_id and self.user_id == self.host_id)
                elif msg.msg_type in (MessageType.PARTICIPANT_LEFT.value, "PARTICIPANT_LEFT", "USER_LEFT"):
                    new_host = msg.payload.get("new_host_id")
                    if new_host:
                        self.host_id = new_host
                        self.is_host = bool(self.user_id and self.user_id == self.host_id)

                # Filter out heartbeat replies from filling inbound queue
                if msg.msg_type == MessageType.HEARTBEAT.value:
                    continue

                # Push to inbound queue for thread-safe GUI processing
                self.inbound_queue.put(msg)

            except socket.timeout:
                continue
            except (ConnectionClosedError, ConnectionResetError, BrokenPipeError):
                logger.info("Connection closed by server.")
                break
            except Exception as e:
                if self._connected:
                    logger.error(f"Receiver error: {e}")
                break

        self._handle_disconnect()

    def _heartbeat_loop(self) -> None:
        """Periodically transmits heartbeats to verify socket health."""
        while self._connected:
            time.sleep(config.HEARTBEAT_INTERVAL)
            if not self._connected:
                break
            try:
                ping = create_heartbeat_msg(self.user_id or self.client_id)
                ping.room_code = self.room_code
                self._send_message(ping)
            except Exception:
                break

    def _send_message(self, message: ProtocolMessage) -> bool:
        """Thread-safe transmission of a protocol message."""
        with self._send_lock:
            if not self._connected or not self._sock:
                return False
            try:
                # Stamp active room code
                message.room_code = self.room_code
                raw = serialize_message(message)
                send_framed_message(self._sock, raw)
                return True
            except Exception as e:
                logger.warning(f"Error sending message: {e}")
                self._handle_disconnect()
                return False

    def _handle_disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

        # Push a synthesized local notification to the GUI queue
        self.inbound_queue.put(
            ProtocolMessage("DISCONNECTED", {"reason": "Lost connection to server."})
        )

        if self.on_disconnected:
            try:
                self.on_disconnected()
            except Exception:
                pass

    def disconnect(self) -> None:
        """Gracefully disconnects from the server."""
        if self._connected:
            self._connected = False
            try:
                leave_msg = ProtocolMessage(
                    MessageType.LEAVE,
                    {"user_id": self.user_id, "room_code": self.room_code},
                    room_code=self.room_code,
                )
                self._send_message(leave_msg)
            except Exception:
                pass
        try:
            if self._sock:
                self._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
                self._sock = None
        except Exception:
            pass
        logger.info("Disconnected from server.")

    # --- Convenience Outbound Methods ---

    def send_draw_segment(
        self, stroke_id: str, x1: float, y1: float, x2: float, y2: float, color: str, width: int
    ) -> None:
        msg = create_draw_segment_msg(
            self.user_id or self.client_id, stroke_id, x1, y1, x2, y2, color, width
        )
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_draw_points(
        self, stroke_id: str, points: List[List[float]], color: str, width: int
    ) -> None:
        """Transmits an incremental batch of mouse movement points."""
        from protocol.messages import create_draw_event_msg
        msg = create_draw_event_msg(
            sender_id=self.user_id or self.client_id,
            room_id=self.room_code,
            stroke_id=stroke_id,
            points=points,
            color=color,
            width=width,
            shape_type="pen",
            is_live=True,
        )
        self._send_message(msg)

    def send_stroke(self, action: WhiteboardAction) -> None:
        msg = create_draw_stroke_msg(action.to_dict())
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_shape(self, action: WhiteboardAction) -> None:
        msg = create_shape_msg(action.to_dict())
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_erase(self, action: WhiteboardAction) -> None:
        msg = create_erase_msg(action.to_dict())
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_clear(self) -> None:
        msg = create_clear_msg(self.user_id or self.client_id, self.username)
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_undo(self, action_id: Optional[str] = None) -> None:
        msg = create_undo_msg(self.user_id or self.client_id, action_id=action_id)
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_redo(self, action_id: Optional[str] = None) -> None:
        msg = create_redo_msg(self.user_id or self.client_id, action_id=action_id)
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_chat(self, text: str) -> None:
        clean_text = (text or "").strip()
        if not clean_text:
            return
        max_len = getattr(config, "MAX_CHAT_LENGTH", 2000)
        if len(clean_text) > max_len:
            clean_text = clean_text[:max_len]
        msg = create_chat_msg(
            self.user_id or self.client_id, self.username, clean_text, color=self.assigned_color
        )
        msg.room_code = self.room_code
        self._send_message(msg)

    def send_load_state(self, actions_list: List[Dict[str, Any]]) -> None:
        msg = create_load_state_msg(actions_list, loaded_by=self.username)
        msg.room_code = self.room_code
        self._send_message(msg)

    def request_sync(self) -> None:
        """Sends an explicit state recovery request to refresh full canvas and chat state."""
        msg = ProtocolMessage(
            MessageType.SYNC_REQUEST,
            {"last_seq_num": self.last_seq_num, "room_code": self.room_code},
            room_code=self.room_code,
        )
        self._send_message(msg)
