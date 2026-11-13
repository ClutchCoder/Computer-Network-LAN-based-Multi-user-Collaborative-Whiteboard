"""
Application Layer Protocol Messages for Whiteboard & Chat.

Computer Networks Principle:
Application Layer (OSI Layer 7 / TCP-IP Application Layer) defines the semantics,
syntax, and timing of exchanged messages. All messages are structured as JSON
documents with standardized fields:
  - msg_type: MessageType identifier
  - msg_id: Unique UUID string for tracing and deduplication
  - timestamp: Unix epoch timestamp (seconds)
  - payload: Typed payload dictionary specific to msg_type
"""

import json
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional


class MessageType(str, Enum):
    # Session Management
    JOIN = "JOIN"
    JOIN_ACK = "JOIN_ACK"
    JOIN_REJECT = "JOIN_REJECT"
    LEAVE = "LEAVE"
    USER_JOINED = "USER_JOINED"
    USER_LEFT = "USER_LEFT"
    USER_LIST = "USER_LIST"

    # Whiteboard Drawing Operations
    DRAW_SEGMENT = "DRAW_SEGMENT"       # Live freehand segment streaming
    DRAW_STROKE = "DRAW_STROKE"         # Completed freehand stroke commit
    SHAPE = "SHAPE"                     # Geometric shapes: line, rect, circle, arrow
    ERASE = "ERASE"                     # Eraser stroke commit
    CLEAR = "CLEAR"                     # Wipe whiteboard canvas

    # Undo / Redo Operations
    UNDO = "UNDO"                       # Undo request / broadcast
    REDO = "REDO"                       # Redo request / broadcast

    # State Synchronization & Persistence
    SYNC_REQUEST = "SYNC_REQUEST"       # Request full canvas history
    SYNC_STATE = "SYNC_STATE"           # Snapshot of full canvas state
    LOAD_STATE = "LOAD_STATE"           # Replace canvas with loaded session

    # Group Communication
    CHAT = "CHAT"                       # Chat message broadcast

    # Health & Keepalive
    HEARTBEAT = "HEARTBEAT"             # Ping / Pong keep-alive


class ProtocolMessage:
    """
    Standard protocol envelope representation.
    """

    def __init__(
        self,
        msg_type: MessageType or str,
        payload: Dict[str, Any],
        msg_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ):
        if isinstance(msg_type, MessageType):
            self.msg_type = msg_type.value
        else:
            self.msg_type = str(msg_type)

        self.payload = payload if payload is not None else {}
        self.msg_id = msg_id or uuid.uuid4().hex
        self.timestamp = timestamp if timestamp is not None else time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "msg_type": self.msg_type,
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProtocolMessage":
        if "msg_type" not in data:
            raise ValueError("Invalid message format: missing 'msg_type'")
        return cls(
            msg_type=data["msg_type"],
            payload=data.get("payload", {}),
            msg_id=data.get("msg_id"),
            timestamp=data.get("timestamp"),
        )

    def __repr__(self) -> str:
        return f"<ProtocolMessage type={self.msg_type} id={self.msg_id[:8]} payload_keys={list(self.payload.keys())}>"


def serialize_message(message: ProtocolMessage) -> bytes:
    """Serializes a ProtocolMessage into UTF-8 JSON bytes."""
    data = message.to_dict()
    json_str = json.dumps(data, separators=(",", ":"))
    return json_str.encode("utf-8")


def deserialize_message(raw_bytes: bytes) -> ProtocolMessage:
    """Deserializes UTF-8 JSON bytes into a ProtocolMessage object."""
    json_str = raw_bytes.decode("utf-8")
    data = json.loads(json_str)
    return ProtocolMessage.from_dict(data)


# =====================================================================
# Factory Helper Functions for Creating Typed Protocol Messages
# =====================================================================

def create_join_msg(username: str, client_id: str) -> ProtocolMessage:
    return ProtocolMessage(
        MessageType.JOIN,
        {
            "username": username,
            "client_id": client_id,
            "version": "1.0",
        },
    )


def create_join_ack_msg(
    user_id: str,
    username: str,
    color: str,
    active_users: List[Dict[str, Any]],
    current_actions: List[Dict[str, Any]],
) -> ProtocolMessage:
    return ProtocolMessage(
        MessageType.JOIN_ACK,
        {
            "user_id": user_id,
            "username": username,
            "color": color,
            "users": active_users,
            "actions": current_actions,
        },
    )


def create_join_reject_msg(reason: str) -> ProtocolMessage:
    return ProtocolMessage(MessageType.JOIN_REJECT, {"reason": reason})


def create_user_joined_msg(user_id: str, username: str, color: str) -> ProtocolMessage:
    return ProtocolMessage(
        MessageType.USER_JOINED,
        {"user_id": user_id, "username": username, "color": color},
    )


def create_user_left_msg(user_id: str, username: str, reason: str = "disconnected") -> ProtocolMessage:
    return ProtocolMessage(
        MessageType.USER_LEFT,
        {"user_id": user_id, "username": username, "reason": reason},
    )


def create_user_list_msg(users: List[Dict[str, Any]]) -> ProtocolMessage:
    return ProtocolMessage(MessageType.USER_LIST, {"users": users})


def create_draw_segment_msg(
    user_id: str,
    stroke_id: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: str,
    width: int,
) -> ProtocolMessage:
    """Live stroke segment transmitted while user drags mouse."""
    return ProtocolMessage(
        MessageType.DRAW_SEGMENT,
        {
            "user_id": user_id,
            "stroke_id": stroke_id,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "color": color,
            "width": width,
        },
    )


def create_draw_stroke_msg(action_dict: Dict[str, Any]) -> ProtocolMessage:
    """Complete finalized stroke action committed to history."""
    return ProtocolMessage(MessageType.DRAW_STROKE, {"action": action_dict})


def create_shape_msg(action_dict: Dict[str, Any]) -> ProtocolMessage:
    """Geometric shape action committed to history."""
    return ProtocolMessage(MessageType.SHAPE, {"action": action_dict})


def create_erase_msg(action_dict: Dict[str, Any]) -> ProtocolMessage:
    """Eraser stroke action committed to history."""
    return ProtocolMessage(MessageType.ERASE, {"action": action_dict})


def create_clear_msg(user_id: str, username: str) -> ProtocolMessage:
    return ProtocolMessage(MessageType.CLEAR, {"user_id": user_id, "username": username})


def create_undo_msg(user_id: str, action_id: Optional[str] = None) -> ProtocolMessage:
    return ProtocolMessage(MessageType.UNDO, {"user_id": user_id, "action_id": action_id})


def create_redo_msg(user_id: str, action_id: Optional[str] = None) -> ProtocolMessage:
    return ProtocolMessage(MessageType.REDO, {"user_id": user_id, "action_id": action_id})


def create_chat_msg(user_id: str, username: str, text: str, color: str = "#000000") -> ProtocolMessage:
    return ProtocolMessage(
        MessageType.CHAT,
        {
            "user_id": user_id,
            "username": username,
            "text": text,
            "color": color,
            "time": time.strftime("%H:%M:%S"),
        },
    )


def create_heartbeat_msg(sender_id: str, is_reply: bool = False) -> ProtocolMessage:
    return ProtocolMessage(
        MessageType.HEARTBEAT,
        {"sender_id": sender_id, "is_reply": is_reply},
    )


def create_sync_state_msg(actions: List[Dict[str, Any]]) -> ProtocolMessage:
    return ProtocolMessage(MessageType.SYNC_STATE, {"actions": actions})


def create_load_state_msg(actions: List[Dict[str, Any]], loaded_by: str) -> ProtocolMessage:
    return ProtocolMessage(
        MessageType.LOAD_STATE,
        {"actions": actions, "loaded_by": loaded_by},
    )
