"""
Whiteboard Room & Session Management.
Supports multiple isolated collaborative whiteboard rooms with room codes,
passcode authentication, authoritative sequence numbers, and persistent chat logs.
"""

import random
import string
import threading
import time
from typing import Any, Dict, List, Optional

from .action import WhiteboardAction
from .whiteboard_state import WhiteboardState


class WhiteboardRoom:
    """
    An isolated collaborative session identified by a unique room code.
    Maintains its own authoritative canvas state, participant list,
    chat history, host identification, and monotonically increasing sequence numbering.
    """

    def __init__(self, room_code: str, name: Optional[str] = None, passcode: Optional[str] = None):
        self.room_code = room_code.upper().strip()
        self.name = name or f"Room {self.room_code}"
        self.passcode = passcode.strip() if passcode else None
        self.created_at = time.time()

        # Section 7: Host Identification
        self.host_id: Optional[str] = None
        self.host_username: Optional[str] = None

        self.state = WhiteboardState()
        self.chat_history: List[Dict[str, Any]] = []
        self._clients: Dict[str, Any] = {}  # user_id -> ClientHandler
        self._seq_num = 0
        self._lock = threading.RLock()

    @property
    def current_seq_num(self) -> int:
        with self._lock:
            return self._seq_num

    def next_seq_num(self) -> int:
        """Atomically increments and returns the monotonically increasing sequence number."""
        with self._lock:
            self._seq_num += 1
            return self._seq_num

    def verify_passcode(self, candidate: Optional[str]) -> bool:
        """Verifies room access password if set."""
        if not self.passcode:
            return True
        return self.passcode == (candidate or "").strip()

    def register_client(self, user_id: str, handler: Any) -> None:
        """Registers a client. The first participant to enter becomes the room host."""
        with self._lock:
            self._clients[user_id] = handler
            if not self.host_id or self.host_id not in self._clients:
                self.host_id = user_id
                self.host_username = getattr(handler, "username", "Host")

    def unregister_client(self, user_id: str) -> Optional[str]:
        """
        Unregisters a client.
        If the departing client was the host, migrates host authority to the next active peer.
        Returns the new host_id if host migration occurred, else None.
        """
        with self._lock:
            self._clients.pop(user_id, None)
            if self.host_id == user_id:
                if self._clients:
                    new_host_id = next(iter(self._clients.keys()))
                    self.host_id = new_host_id
                    new_handler = self._clients[new_host_id]
                    self.host_username = getattr(new_handler, "username", "Host")
                    return self.host_id
                else:
                    self.host_id = None
                    self.host_username = None
            return None

    def is_host(self, user_id: str) -> bool:
        """Checks if the specified user is the authoritative host of this room."""
        with self._lock:
            return bool(self.host_id and self.host_id == user_id)

    def get_client(self, user_id: str) -> Optional[Any]:
        with self._lock:
            return self._clients.get(user_id)

    def get_active_users_list(self) -> List[Dict[str, Any]]:
        with self._lock:
            users = []
            for uid, handler in self._clients.items():
                users.append({
                    "user_id": uid,
                    "username": getattr(handler, "username", "Anonymous"),
                    "color": getattr(handler, "user_color", "#000000"),
                    "addr": f"{handler.addr[0]}:{handler.addr[1]}" if hasattr(handler, "addr") else "unknown",
                    "is_host": (uid == self.host_id),
                })
            return users

    def broadcast(self, message: Any, exclude_user_id: Optional[str] = None) -> None:
        """
        Transmits message to all connected clients in this room.
        Computer Networks Principle:
          Safe Concurrent Broadcasting:
          1. Atomically snapshots recipients under self._lock.
          2. Releases self._lock BEFORE delegating to per-client send queues.
          3. Sockets are never written to while holding state locks, eliminating
             deadlocks and Head-of-Line blocking from slow network consumers.
        """
        with self._lock:
            recipients = [
                handler for uid, handler in self._clients.items()
                if not (exclude_user_id and uid == exclude_user_id)
            ]

        for handler in recipients:
            try:
                handler.send_message(message)
            except Exception:
                pass

    def add_chat_message(self, chat_dict: Dict[str, Any], max_history: int = 500) -> None:
        """Appends a chat message to persistent room history."""
        with self._lock:
            self.chat_history.append(chat_dict)
            if len(self.chat_history) > max_history:
                self.chat_history.pop(0)

    def get_chat_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.chat_history)

    def to_metadata(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "room_code": self.room_code,
                "name": self.name,
                "has_passcode": bool(self.passcode),
                "participants_count": len(self._clients),
                "action_count": len(self.state.get_active_actions()),
                "host_id": self.host_id,
                "host_username": self.host_username,
                "created_at": self.created_at,
            }


class RoomManager:
    """
    Thread-safe registry for all active Whiteboard Rooms on the server.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._rooms: Dict[str, WhiteboardRoom] = {}
        # Ensure default main room is always available
        self.get_or_create_room("MAIN", name="General Collaboration Room")

    def generate_unique_room_code(self, prefix: str = "ROOM") -> str:
        """Generates a cryptographically strong unique room code not currently active."""
        with self._lock:
            for _ in range(100):
                suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
                candidate = f"{prefix}_{suffix}"
                if candidate not in self._rooms:
                    return candidate
            return f"{prefix}_{int(time.time())}"

    def create_room(
        self,
        room_code: Optional[str] = None,
        name: Optional[str] = None,
        passcode: Optional[str] = None,
    ) -> WhiteboardRoom:
        """Explicitly creates and registers a new room session with a unique code."""
        with self._lock:
            code = (room_code or self.generate_unique_room_code()).upper().strip()
            if code not in self._rooms:
                self._rooms[code] = WhiteboardRoom(
                    room_code=code,
                    name=name or f"Room {code}",
                    passcode=passcode,
                )
            return self._rooms[code]

    def get_or_create_room(
        self, room_code: str, name: Optional[str] = None, passcode: Optional[str] = None
    ) -> WhiteboardRoom:
        normalized_code = (room_code or "MAIN").upper().strip()
        with self._lock:
            if normalized_code not in self._rooms:
                self._rooms[normalized_code] = WhiteboardRoom(
                    room_code=normalized_code,
                    name=name or f"Room {normalized_code}",
                    passcode=passcode,
                )
            return self._rooms[normalized_code]

    def get_room(self, room_code: str) -> Optional[WhiteboardRoom]:
        normalized_code = (room_code or "MAIN").upper().strip()
        with self._lock:
            return self._rooms.get(normalized_code)

    def list_rooms(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [room.to_metadata() for room in self._rooms.values()]
