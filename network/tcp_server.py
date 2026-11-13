"""
Multi-Threaded TCP Whiteboard & Chat Server with Authoritative Room Management.

Computer Networks Principle:
Implements a concurrent Authoritative TCP Server using Thread-per-Client architecture.
Key Network & Architectural Mechanics:
  1. Passive Listening Socket: socket(), setsockopt(SO_REUSEADDR), bind(), listen().
  2. Authoritative State Coordination: Server remains the single source of truth for all
     drawing actions, undo/redo states, and sequence numbers.
  3. Room Sessions & Access Control: Manages multiple isolated rooms identified by room codes,
     with optional passcode authentication.
  4. Monotonic Sequence Numbers: Assigns monotonically increasing integer sequence numbers
     to every accepted operation in a room to guarantee strict total order.
  5. Persistent Chat History: Preserves message log per room for late joiners and reconnected peers.
  6. Reconnection & State Recovery: Seamlessly re-associates returning clients and syncs canvas history.
  7. TCP_NODELAY: Explicitly disables Nagle's algorithm for sub-millisecond drawing latency.
"""

import logging
import queue
import socket
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

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
    create_canvas_state_msg,
    create_clear_canvas_msg,
    create_join_failure_msg,
    create_join_success_msg,
    create_load_state_msg,
    create_participant_joined_msg,
    create_participant_left_msg,
    create_ping_msg,
    create_pong_msg,
    create_redo_event_msg,
    create_room_list_msg,
    create_undo_event_msg,
    deserialize_message,
    serialize_message,
)
from state.action import WhiteboardAction
from state.room import RoomManager, WhiteboardRoom
from state.whiteboard_state import WhiteboardState

from .discovery import DiscoveryBeaconServer

logger = logging.getLogger("WhiteboardServer")


class ClientHandler(threading.Thread):
    """
    Dedicated worker thread managing TCP communication with a single connected client.
    Implements Section 6 Concurrent Client Management:
      1. One independent client-handling reader thread per connection.
      2. One companion outgoing writer thread draining a per-client queue.
      3. Thread-safe sequential socket writes with zero simultaneous-write race conditions.
      4. Safe non-blocking broadcast dispatching.
    """

    def __init__(self, server: "WhiteboardServer", sock: socket.socket, addr: tuple):
        super().__init__(daemon=True, name=f"ClientReader-{addr[0]}:{addr[1]}")
        self.server = server
        self.sock = sock
        self.addr = addr
        self.user_id: Optional[str] = None
        self.username: Optional[str] = None
        self.user_color: str = "#000000"
        self.room_code: str = "MAIN"
        self._running = True
        self.last_seen = time.time()

        # Section 6: Per-client outgoing message queue preventing simultaneous socket writes
        # and preventing slow clients from blocking broadcasts.
        self._send_queue: queue.Queue = queue.Queue(maxsize=getattr(config, "MAX_CLIENT_QUEUE_SIZE", 1000))
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name=f"ClientWriter-{addr[0]}:{addr[1]}",
        )
        self._writer_thread.start()

    def _writer_loop(self) -> None:
        """
        Independent per-client outgoing transmission worker.
        Computer Networks Principle:
          Drains the per-client outgoing message queue and conducts sequential socket writes.
          Guarantees that no two threads write to the same socket simultaneously,
          preventing race conditions, fragmented frame interleaving, and socket corruption.
          Crucially decouples broadcasting callers from network I/O, preventing slow consumers
          from causing Head-of-Line blocking or thread starvation across the server.
        """
        while self._running and self.server.is_running():
            try:
                item = self._send_queue.get(timeout=0.5)
                if item is None:
                    # Poison pill received during close()
                    break

                raw_bytes = serialize_message(item) if isinstance(item, ProtocolMessage) else item
                send_framed_message(self.sock, raw_bytes)
                self._send_queue.task_done()
            except queue.Empty:
                continue
            except (ConnectionClosedError, ConnectionResetError, BrokenPipeError, socket.error) as e:
                logger.debug(f"Socket write failed for client {self.username or self.addr}: {e}")
                self._running = False
                break
            except Exception as e:
                logger.error(f"Unexpected error in writer loop for {self.addr}: {e}")
                self._running = False
                break

    def run(self) -> None:
        logger.info(f"Accepted TCP connection from {self.addr}")
        try:
            # Perform initial handshake
            if not self._handle_handshake():
                return

            # Main client message loop
            while self._running and self.server.is_running():
                try:
                    payload_bytes = read_framed_message(self.sock)
                    self.last_seen = time.time()
                    if not payload_bytes:
                        continue

                    message = deserialize_message(payload_bytes)
                    self._dispatch_message(message)

                except socket.timeout:
                    # Inactivity timeout check
                    if time.time() - self.last_seen > config.HEARTBEAT_TIMEOUT:
                        logger.warning(f"Client {self.username or self.addr} timed out (heartbeat missing)")
                        break
                    continue
                except (ConnectionClosedError, ConnectionResetError, BrokenPipeError):
                    break
                except (FrameError, ValueError) as e:
                    logger.warning(f"Protocol error from {self.addr}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Unexpected error in client handler for {self.addr}: {e}", exc_info=True)
        finally:
            self._cleanup()

    def _handle_handshake(self) -> bool:
        """Processes the mandatory initial JOIN_ROOM handshake, room assignment, and authentication."""
        try:
            payload_bytes = read_framed_message(self.sock)
            message = deserialize_message(payload_bytes)

            if message.msg_type not in (MessageType.JOIN_ROOM.value, "JOIN_ROOM", "JOIN"):
                reject = create_join_failure_msg("First message must be JOIN_ROOM handshake.")
                self.send_message(reject)
                return False

            payload = message.payload
            requested_username = payload.get("username", "Anonymous").strip()
            if not requested_username:
                requested_username = f"User_{self.addr[1]}"

            client_id = payload.get("client_id")
            room_code = (payload.get("room_code") or payload.get("room_id") or message.room_id or "MAIN").upper().strip()
            passcode = payload.get("passcode", "")
            is_reconnect = payload.get("is_reconnect", False)

            # Retrieve or create room
            room = self.server.room_manager.get_or_create_room(room_code)

            # Verify room access / authentication
            if not room.verify_passcode(passcode):
                reject = create_join_failure_msg(f"Invalid passcode for room '{room_code}'. Access denied.", room_id=room_code)
                self.send_message(reject)
                return False

            # Register client on server and room
            approved, user_id, assigned_color = self.server.register_client_to_room(
                self, room, requested_username, client_id, is_reconnect=is_reconnect
            )
            if not approved:
                reject = create_join_failure_msg("Username already active or room is full.", room_id=room_code)
                self.send_message(reject)
                return False

            self.user_id = user_id
            self.username = requested_username
            self.user_color = assigned_color
            self.room_code = room.room_code

            # Send authoritative JOIN_SUCCESS containing room state, history, and current sequence number
            ack_msg = create_join_success_msg(
                user_id=self.user_id,
                username=self.username,
                color=self.user_color,
                active_users=room.get_active_users_list(),
                current_actions=room.state.to_dict_list(),
                room_id=self.room_code,
                chat_history=room.get_chat_history(),
                seq_num=room.current_seq_num,
            )
            self.send_message(ack_msg)

            # Broadcast new arrival to everyone else in this room
            join_broadcast = create_participant_joined_msg(self.user_id, self.room_code, self.username, self.user_color, seq_num=room.current_seq_num)
            room.broadcast(join_broadcast, exclude_user_id=self.user_id)

            logger.info(
                f"Client '{self.username}' ({self.user_id}) joined room '{self.room_code}' "
                f"[{'reconnect' if is_reconnect else 'new session'}]."
            )
            return True

        except Exception as e:
            logger.warning(f"Handshake failed for {self.addr}: {e}")
            return False

    def _validate_draw_payload(self, payload: Dict[str, Any]) -> Tuple[bool, str]:
        """Validates drawing parameters and point bounds before committing to state."""
        points = payload.get("points")
        if not points or not isinstance(points, list):
            return False, "Points array missing or invalid."
        if len(points) > 10000:
            return False, "Point count exceeds allowable limit (10,000)."
        width = payload.get("width", 3)
        if not isinstance(width, (int, float)) or width <= 0 or width > 100:
            return False, f"Invalid stroke width: {width}."
        color = payload.get("color", "#000000")
        if not isinstance(color, str) or len(color) > 30:
            return False, "Invalid color specification."
        for pt in points:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                return False, "Invalid coordinate point format."
            if not isinstance(pt[0], (int, float)) or not isinstance(pt[1], (int, float)):
                return False, "Coordinates must be numbers."
            if abs(pt[0]) > 50000 or abs(pt[1]) > 50000:
                return False, "Coordinates exceed allowable canvas bounds."
        return True, ""

    def _dispatch_message(self, message: ProtocolMessage) -> None:
        """Validates incoming client actions, assigns sequence numbers, updates authoritative state, and broadcasts."""
        msg_type = message.msg_type
        payload = message.payload

        if not self.user_id:
            logger.warning(f"Rejected unauthenticated message '{msg_type}' from {self.addr}")
            return

        # Room isolation enforcement: clients cannot affect rooms other than their joined room
        target_room = (message.room_code or message.room_id or self.room_code).upper().strip()
        if target_room != self.room_code:
            logger.warning(
                f"Cross-room attempt by {self.username}: targeted '{target_room}' but client is in '{self.room_code}'"
            )
            return

        room = self.server.room_manager.get_room(self.room_code)
        if not room:
            logger.warning(f"Message dropped: Room '{self.room_code}' not found.")
            return

        # Check for drawing events
        if msg_type in (MessageType.DRAW_EVENT.value, "DRAW_EVENT", "DRAW_SEGMENT", "DRAW_STROKE", "SHAPE"):
            is_live = payload.get("is_live", False) or (msg_type == "DRAW_SEGMENT")
            if is_live:
                valid, err = self._validate_draw_payload(payload)
                if not valid:
                    logger.warning(f"Invalid live draw payload from {self.username}: {err}")
                    return
                # Ephemeral stroke segment: broadcast directly to all other peers in room for low latency
                message.seq_num = room.current_seq_num
                message.room_code = self.room_code
                message.room_id = self.room_code
                room.broadcast(message, exclude_user_id=self.user_id)
            else:
                # Finalized drawing stroke or shape: commit to authoritative state
                action_data = payload.get("action")
                if not action_data:
                    action_data = {
                        "action_id": payload.get("stroke_id", uuid.uuid4().hex),
                        "action_type": payload.get("shape_type", "pen"),
                        "user_id": self.user_id,
                        "username": self.username,
                        "color": payload.get("color", "#000000"),
                        "width": payload.get("width", 3),
                        "points": payload.get("points", []),
                        "fill": payload.get("fill"),
                    }
                valid, err = self._validate_draw_payload(action_data)
                if not valid:
                    logger.warning(f"Invalid draw action rejected from {self.username}: {err}")
                    return

                action = WhiteboardAction.from_dict(action_data)
                action.user_id = self.user_id
                action.username = self.username

                # Concurrency: Atomic sequence assignment and state commit under room lock
                with room._lock:
                    if room.state.has_action(action.action_id):
                        logger.warning(f"Duplicate action '{action.action_id}' from {self.username} rejected.")
                        return
                    seq = room.next_seq_num()
                    room.state.add_action(action)

                message.seq_num = seq
                message.room_code = self.room_code
                message.room_id = self.room_code
                room.broadcast(message, exclude_user_id=self.user_id)

        elif msg_type in (MessageType.ERASE_EVENT.value, "ERASE_EVENT", "ERASE"):
            action_data = payload.get("action")
            if not action_data:
                action_data = {
                    "action_id": payload.get("stroke_id", uuid.uuid4().hex),
                    "action_type": "eraser",
                    "user_id": self.user_id,
                    "username": self.username,
                    "color": "#FFFFFF",
                    "width": payload.get("width", 10),
                    "points": payload.get("points", []),
                }
            valid, err = self._validate_draw_payload(action_data)
            if not valid:
                logger.warning(f"Invalid erase action rejected from {self.username}: {err}")
                return

            action = WhiteboardAction.from_dict(action_data)
            action.user_id = self.user_id
            action.username = self.username

            with room._lock:
                if room.state.has_action(action.action_id):
                    logger.warning(f"Duplicate eraser action '{action.action_id}' from {self.username} rejected.")
                    return
                seq = room.next_seq_num()
                room.state.add_action(action)

            message.seq_num = seq
            message.room_code = self.room_code
            message.room_id = self.room_code
            room.broadcast(message, exclude_user_id=self.user_id)

        elif msg_type in (MessageType.CLEAR_CANVAS.value, "CLEAR_CANVAS", "CLEAR"):
            with room._lock:
                seq = room.next_seq_num()
                clear_action = room.state.clear(self.user_id, self.username)
            broadcast_msg = create_clear_canvas_msg(self.user_id, self.room_code, self.username, seq_num=seq)
            room.broadcast(broadcast_msg)

        elif msg_type in (MessageType.UNDO_EVENT.value, "UNDO_EVENT", "UNDO"):
            target_action_id = payload.get("action_id")
            broadcast_msg = None
            with room._lock:
                undone_action = room.state.undo_action(
                    user_id=self.user_id, target_action_id=target_action_id
                )
                if undone_action:
                    seq = room.next_seq_num()
                    broadcast_msg = create_undo_event_msg(
                        sender_id=self.user_id, room_id=self.room_code, action_id=undone_action.action_id, seq_num=seq
                    )
                else:
                    logger.debug(f"Stale or invalid undo request from {self.username} ignored.")

            if broadcast_msg:
                room.broadcast(broadcast_msg)

        elif msg_type in (MessageType.REDO_EVENT.value, "REDO_EVENT", "REDO"):
            target_action_id = payload.get("action_id")
            broadcast_msg = None
            with room._lock:
                redone_action = room.state.redo_action(
                    user_id=self.user_id, target_action_id=target_action_id
                )
                if redone_action:
                    seq = room.next_seq_num()
                    broadcast_msg = create_redo_event_msg(
                        sender_id=self.user_id, room_id=self.room_code, action_id=redone_action.action_id, seq_num=seq
                    )
                else:
                    logger.debug(f"Stale or invalid redo request from {self.username} ignored.")

            if broadcast_msg:
                room.broadcast(broadcast_msg)

        elif msg_type in (MessageType.CHAT_MESSAGE.value, "CHAT_MESSAGE", "CHAT"):
            text = payload.get("text", "")
            if not isinstance(text, str) or not text.strip():
                logger.warning(f"Empty chat message from {self.username} rejected.")
                return
            if len(text) > 4000:
                payload["text"] = text[:4000]

            with room._lock:
                seq = room.next_seq_num()
                message.payload["user_id"] = self.user_id
                message.payload["sender_id"] = self.user_id
                message.payload["username"] = self.username
                message.payload["color"] = self.user_color
                message.seq_num = seq
                message.room_code = self.room_code
                message.room_id = self.room_code
                room.add_chat_message(message.payload)

            room.broadcast(message)

        elif msg_type in (MessageType.PING.value, "PING", "HEARTBEAT"):
            pong = create_pong_msg(self.server.server_id, self.room_code)
            pong.seq_num = room.current_seq_num
            self.send_message(pong)

        elif msg_type in (MessageType.PONG.value, "PONG"):
            pass

        elif msg_type in (MessageType.STATE_REQUEST.value, "STATE_REQUEST", "SYNC_REQUEST"):
            with room._lock:
                actions_snapshot = room.state.to_dict_list()
                history_snapshot = room.get_chat_history()
                seq = room.current_seq_num
            sync_msg = create_canvas_state_msg(
                room_id=self.room_code,
                actions=actions_snapshot,
                chat_history=history_snapshot,
                seq_num=seq,
            )
            self.send_message(sync_msg)

        elif msg_type == MessageType.LOAD_STATE.value:
            actions_list = payload.get("actions", [])
            with room._lock:
                seq = room.next_seq_num()
                room.state.load_from_dict_list(actions_list)
            load_broadcast = create_load_state_msg(actions_list, loaded_by=self.username, room_id=self.room_code)
            load_broadcast.seq_num = seq
            room.broadcast(load_broadcast)

        elif msg_type == MessageType.ROOM_LIST.value:
            rooms_info = self.server.room_manager.list_rooms()
            self.send_message(create_room_list_msg(rooms_info))

        elif msg_type in (MessageType.DISCONNECT.value, "DISCONNECT", "LEAVE"):
            self._running = False

    def send_message(self, message: ProtocolMessage, block: bool = False, timeout: float = 0.5) -> bool:
        """
        Enqueues an outgoing message onto this client's independent transmission queue.
        Computer Networks Principle:
          Decouples the broadcasting thread from socket writes. Thread-safe, non-blocking,
          and prevents slow network consumers from blocking the server or other clients.
        """
        if not self._running:
            return False
        try:
            if block:
                self._send_queue.put(message, block=True, timeout=timeout)
            else:
                self._send_queue.put_nowait(message)
            return True
        except queue.Full:
            msg_type = getattr(message, "msg_type", "")
            if msg_type in (MessageType.DRAW_SEGMENT.value, "DRAW_SEGMENT"):
                logger.debug(f"Dropping live draw segment for slow client {self.username or self.addr}")
                return False
            logger.warning(f"Send queue full for client {self.username or self.addr}. Terminating stalled client.")
            self._running = False
            return False

    def close(self) -> None:
        self._running = False
        try:
            self._send_queue.put_nowait(None)
        except Exception:
            pass
        if self._writer_thread and self._writer_thread.is_alive():
            if threading.current_thread() != self._writer_thread:
                self._writer_thread.join(timeout=0.2)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def _cleanup(self) -> None:
        self.close()
        if self.user_id:
            self.server.unregister_client_from_room(self.user_id, self.username, self.room_code)


class WhiteboardServer:
    """
    Central Authoritative Whiteboard, Room, and Chat TCP Server.
    """

    def __init__(
        self,
        host: str = config.DEFAULT_SERVER_HOST,
        port: int = config.DEFAULT_SERVER_PORT,
        enable_discovery: bool = True,
    ):
        self.host = host
        self.port = port
        self.enable_discovery = enable_discovery
        self.server_id = "server_master"

        # Room and Session Manager
        self.room_manager = RoomManager()
        # Session cache for reconnecting clients: client_id -> {user_id, color, username, room_code}
        self._session_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()

        self._color_index = 0
        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._accept_thread: Optional[threading.Thread] = None
        self._discovery_beacon: Optional[DiscoveryBeaconServer] = None

        # Statistics
        self.start_time = 0.0
        self.total_connections_served = 0

    @property
    def state(self) -> WhiteboardState:
        """Provides backward-compatible access to the default (MAIN) room's authoritative state."""
        return self.room_manager.get_or_create_room("MAIN").state

    def start(self) -> None:
        """Binds TCP listening socket, starts accept loop and discovery beacon."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        if config.ENABLE_SO_REUSEADDR:
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(128)
        self._server_sock.settimeout(1.0)  # Allows clean check of _running flag

        self._running = True
        self.start_time = time.time()

        lan_ip = config.get_lan_ip()
        logger.info(f"Whiteboard TCP Server started on {self.host}:{self.port} (LAN IP: {lan_ip})")

        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True, name="TCP-Accept-Loop")
        self._accept_thread.start()

        if self.enable_discovery:
            self._discovery_beacon = DiscoveryBeaconServer(tcp_port=self.port)
            self._discovery_beacon.start()

    def is_running(self) -> bool:
        return self._running

    def _accept_loop(self) -> None:
        while self._running:
            try:
                client_sock, client_addr = self._server_sock.accept()

                # Optimize client socket for low-latency interactive drawing
                if config.ENABLE_TCP_NODELAY:
                    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                client_sock.settimeout(config.SOCKET_TIMEOUT)

                handler = ClientHandler(self, client_sock, client_addr)
                handler.start()
                self.total_connections_served += 1

            except socket.timeout:
                continue
            except socket.error as e:
                if self._running:
                    logger.error(f"Socket accept error: {e}")
                break

    def register_client_to_room(
        self,
        handler: ClientHandler,
        room: WhiteboardRoom,
        username: str,
        client_id: Optional[str],
        is_reconnect: bool = False,
    ) -> Tuple[bool, str, str]:
        """Registers a client into a specific room, preserving state on reconnection."""
        with self._cache_lock:
            # Check if this client is reconnecting to an existing session
            if is_reconnect and client_id and client_id in self._session_cache:
                saved = self._session_cache[client_id]
                assigned_id = saved["user_id"]
                assigned_color = saved["color"]
            else:
                assigned_id = client_id or f"user_{int(time.time() * 1000) % 10000}"
                assigned_color = config.PRESET_COLORS[self._color_index % len(config.PRESET_COLORS)]
                self._color_index += 1

                if client_id:
                    self._session_cache[client_id] = {
                        "user_id": assigned_id,
                        "color": assigned_color,
                        "username": username,
                        "room_code": room.room_code,
                    }

            room.register_client(assigned_id, handler)
            return True, assigned_id, assigned_color

    def unregister_client_from_room(self, user_id: str, username: Optional[str], room_code: str) -> None:
        """Removes a disconnected client from the specified room and broadcasts departure."""
        room = self.room_manager.get_room(room_code)
        if room:
            room.unregister_client(user_id)
            logger.info(
                f"User '{username or user_id}' left room '{room_code}'. "
                f"Remaining in room: {len(room.get_active_users_list())}"
            )
            left_msg = create_participant_left_msg(user_id, room_code, username or "User", seq_num=room.current_seq_num)
            room.broadcast(left_msg)

    def broadcast(
        self,
        message: ProtocolMessage,
        room_code: str = "MAIN",
        exclude_user_id: Optional[str] = None,
    ) -> None:
        """Transmits message to all connected clients in the designated room."""
        room = self.room_manager.get_room(room_code)
        if room:
            room.broadcast(message, exclude_user_id=exclude_user_id)

    def get_active_users_list(self, room_code: str = "MAIN") -> List[Dict[str, Any]]:
        """Returns participant list for default or specified room."""
        room = self.room_manager.get_room(room_code)
        return room.get_active_users_list() if room else []

    def stop(self) -> None:
        """Stops the server gracefully, closing all client sockets across all rooms."""
        logger.info("Initiating graceful server shutdown...")
        self._running = False

        if self._discovery_beacon:
            self._discovery_beacon.stop()

        # Close all client connections in all rooms
        for room_meta in self.room_manager.list_rooms():
            room = self.room_manager.get_room(room_meta["room_code"])
            if room:
                with room._lock:
                    for handler in list(room._clients.values()):
                        handler.close()
                    room._clients.clear()

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

        if self._accept_thread:
            self._accept_thread.join(timeout=2.0)
        logger.info("Graceful server shutdown completed.")
