"""
Multi-Threaded TCP Whiteboard & Chat Server.

Computer Networks Principle:
Implements a concurrent TCP Server using the Thread-per-Client architecture.
Key Network Mechanics:
  1. Passive Socket Creation: socket(), setsockopt(SO_REUSEADDR), bind(), listen().
  2. TCP Stream Framing: Uses length-prefixed protocol to guarantee reliable message boundaries.
  3. TCP_NODELAY: Explicitly disables Nagle's algorithm on every client socket to eliminate
     OS-level 40ms transmission delays for real-time stroke coordinates.
  4. Concurrent State Coordination: Authoritative server synchronizes all drawing,
     undo/redo actions, chat messages, and participant presence.
"""

import logging
import socket
import threading
import time
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
    create_clear_msg,
    create_heartbeat_msg,
    create_join_ack_msg,
    create_join_reject_msg,
    create_load_state_msg,
    create_redo_msg,
    create_sync_state_msg,
    create_undo_msg,
    create_user_joined_msg,
    create_user_left_msg,
    deserialize_message,
    serialize_message,
)
from state.action import WhiteboardAction
from state.whiteboard_state import WhiteboardState

from .discovery import DiscoveryBeaconServer

logger = logging.getLogger("WhiteboardServer")


class ClientHandler(threading.Thread):
    """
    Dedicated worker thread managing TCP communication with a single connected client.
    """

    def __init__(self, server: "WhiteboardServer", sock: socket.socket, addr: tuple):
        super().__init__(daemon=True, name=f"ClientHandler-{addr[0]}:{addr[1]}")
        self.server = server
        self.sock = sock
        self.addr = addr
        self.user_id: Optional[str] = None
        self.username: Optional[str] = None
        self.user_color: str = "#000000"
        self._running = True
        self.last_seen = time.time()
        self._send_lock = threading.Lock()

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
        """Processes the mandatory initial JOIN message."""
        try:
            payload_bytes = read_framed_message(self.sock)
            message = deserialize_message(payload_bytes)

            if message.msg_type != MessageType.JOIN.value:
                reject = create_join_reject_msg("First message must be JOIN handshake.")
                self.send_message(reject)
                return False

            requested_username = message.payload.get("username", "Anonymous").strip()
            if not requested_username:
                requested_username = f"User_{self.addr[1]}"

            client_id = message.payload.get("client_id")

            # Register with server
            approved, user_id, assigned_color = self.server.register_client(
                self, requested_username, client_id
            )
            if not approved:
                reject = create_join_reject_msg("Username already taken or server is full.")
                self.send_message(reject)
                return False

            self.user_id = user_id
            self.username = requested_username
            self.user_color = assigned_color

            # Send JOIN_ACK with current state and active participants
            ack_msg = create_join_ack_msg(
                user_id=self.user_id,
                username=self.username,
                color=self.user_color,
                active_users=self.server.get_active_users_list(),
                current_actions=self.server.state.to_dict_list(),
            )
            self.send_message(ack_msg)

            # Broadcast new arrival to everyone else
            join_broadcast = create_user_joined_msg(self.user_id, self.username, self.user_color)
            self.server.broadcast(join_broadcast, exclude_user_id=self.user_id)

            logger.info(f"Client '{self.username}' ({self.user_id}) joined successfully.")
            return True

        except Exception as e:
            logger.warning(f"Handshake failed for {self.addr}: {e}")
            return False

    def _dispatch_message(self, message: ProtocolMessage) -> None:
        """Routes incoming messages based on MessageType."""
        msg_type = message.msg_type
        payload = message.payload

        if msg_type == MessageType.DRAW_SEGMENT.value:
            # Ephemeral stroke segment: broadcast directly to all other peers for low latency
            self.server.broadcast(message, exclude_user_id=self.user_id)

        elif msg_type in (MessageType.DRAW_STROKE.value, MessageType.SHAPE.value, MessageType.ERASE.value):
            action_data = payload.get("action")
            if action_data:
                action = WhiteboardAction.from_dict(action_data)
                # Enforce authoritative user_id
                action.user_id = self.user_id
                action.username = self.username
                self.server.state.add_action(action)
                # Broadcast committed action
                self.server.broadcast(message, exclude_user_id=self.user_id)

        elif msg_type == MessageType.CLEAR.value:
            clear_action = self.server.state.clear(self.user_id, self.username)
            broadcast_msg = create_clear_msg(self.user_id, self.username)
            self.server.broadcast(broadcast_msg)

        elif msg_type == MessageType.UNDO.value:
            target_action_id = payload.get("action_id")
            undone_action = self.server.state.undo_action(
                user_id=self.user_id, target_action_id=target_action_id
            )
            if undone_action:
                broadcast_msg = create_undo_msg(
                    user_id=self.user_id, action_id=undone_action.action_id
                )
                self.server.broadcast(broadcast_msg)

        elif msg_type == MessageType.REDO.value:
            target_action_id = payload.get("action_id")
            redone_action = self.server.state.redo_action(
                user_id=self.user_id, target_action_id=target_action_id
            )
            if redone_action:
                broadcast_msg = create_redo_msg(
                    user_id=self.user_id, action_id=redone_action.action_id
                )
                self.server.broadcast(broadcast_msg)

        elif msg_type == MessageType.CHAT.value:
            # Stamp author info and broadcast
            message.payload["user_id"] = self.user_id
            message.payload["username"] = self.username
            message.payload["color"] = self.user_color
            self.server.broadcast(message)

        elif msg_type == MessageType.HEARTBEAT.value:
            pong = create_heartbeat_msg(self.server.server_id, is_reply=True)
            self.send_message(pong)

        elif msg_type == MessageType.SYNC_REQUEST.value:
            sync_msg = create_sync_state_msg(self.server.state.to_dict_list())
            self.send_message(sync_msg)

        elif msg_type == MessageType.LOAD_STATE.value:
            actions_list = payload.get("actions", [])
            self.server.state.load_from_dict_list(actions_list)
            load_broadcast = create_load_state_msg(actions_list, loaded_by=self.username)
            self.server.broadcast(load_broadcast)

        elif msg_type == MessageType.LEAVE.value:
            self._running = False

    def send_message(self, message: ProtocolMessage) -> bool:
        """Sends a framed message to this client."""
        with self._send_lock:
            if not self._running:
                return False
            try:
                raw_bytes = serialize_message(message)
                send_framed_message(self.sock, raw_bytes)
                return True
            except Exception as e:
                logger.debug(f"Failed to send to {self.username or self.addr}: {e}")
                self._running = False
                return False

    def close(self) -> None:
        self._running = False
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
            self.server.unregister_client(self.user_id, self.username)


class WhiteboardServer:
    """
    Central Authoritative Whiteboard and Chat TCP Server.
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

        self.state = WhiteboardState()
        self._clients: Dict[str, ClientHandler] = {}
        self._clients_lock = threading.RLock()

        self._color_index = 0
        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._accept_thread: Optional[threading.Thread] = None
        self._discovery_beacon: Optional[DiscoveryBeaconServer] = None

        # Statistics
        self.start_time = 0.0
        self.total_connections_served = 0

    def start(self) -> None:
        """Binds TCP socket, starts accept loop and discovery beacon."""
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

    def register_client(
        self, handler: ClientHandler, username: str, client_id: Optional[str]
    ) -> Tuple[bool, str, str]:
        """Registers an authenticated client and assigns a distinct color."""
        with self._clients_lock:
            assigned_id = client_id or f"user_{len(self._clients) + 1}_{int(time.time() * 1000) % 10000}"

            # Pick next color in cycle
            assigned_color = config.PRESET_COLORS[self._color_index % len(config.PRESET_COLORS)]
            self._color_index += 1

            self._clients[assigned_id] = handler
            return True, assigned_id, assigned_color

    def unregister_client(self, user_id: str, username: Optional[str]) -> None:
        """Removes a disconnected client and broadcasts departure."""
        with self._clients_lock:
            if user_id in self._clients:
                del self._clients[user_id]
                logger.info(f"User '{username or user_id}' deregistered. Active clients: {len(self._clients)}")

        # Broadcast departure notice to remaining participants
        left_msg = create_user_left_msg(user_id, username or "User")
        self.broadcast(left_msg)

    def broadcast(self, message: ProtocolMessage, exclude_user_id: Optional[str] = None) -> None:
        """Transmits message to all connected clients (except exclude_user_id if specified)."""
        with self._clients_lock:
            recipients = list(self._clients.items())

        for uid, handler in recipients:
            if exclude_user_id and uid == exclude_user_id:
                continue
            handler.send_message(message)

    def get_active_users_list(self) -> List[Dict[str, Any]]:
        """Returns metadata list of currently connected participants."""
        with self._clients_lock:
            return [
                {
                    "user_id": h.user_id,
                    "username": h.username,
                    "color": h.user_color,
                    "addr": f"{h.addr[0]}:{h.addr[1]}",
                }
                for h in self._clients.values()
                if h.user_id
            ]

    def stop(self) -> None:
        """Stops the server gracefully."""
        logger.info("Shutting down Whiteboard Server...")
        self._running = False

        if self._discovery_beacon:
            self._discovery_beacon.stop()

        with self._clients_lock:
            for handler in list(self._clients.values()):
                handler.close()
            self._clients.clear()

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

        if self._accept_thread:
            self._accept_thread.join(timeout=2.0)
        logger.info("Whiteboard Server stopped successfully.")
