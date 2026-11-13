"""
Whiteboard Room & Session Management.
Supports multiple isolated collaborative whiteboard rooms with room codes,
passcode authentication, authoritative sequence numbers, and persistent chat logs.
"""

import threading
import time
from typing import Any, Dict, List, Optional

from .action import WhiteboardAction
from .whiteboard_state import WhiteboardState


class WhiteboardRoom:
    """
    An isolated collaborative session identified by a unique room code.
    Maintains its own authoritative canvas state, participant list,
    chat history, and monotonically increasing sequence numbering.
    """

    def __init__(self, room_code: str, name: Optional[str] = None, passcode: Optional[str] = None):
        self.room_code = room_code.upper().strip()
        self.name = name or f"Room {self.room_code}"
        self.passcode = passcode.strip() if passcode else None
        self.created_at = time.time()

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
        with self._lock:
            self._clients[user_id] = handler

    def unregister_client(self, user_id: str) -> None:
        with self._lock:
            self._clients.pop(user_id, None)

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
                })
            return users

    def broadcast(self, message: Any, exclude_user_id: Optional[str] = None) -> None:
        """Transmits message to all connected clients in this room."""
        with self._lock:
            recipients = list(self._clients.items())

        for uid, handler in recipients:
            if exclude_user_id and uid == exclude_user_id:
                continue
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
