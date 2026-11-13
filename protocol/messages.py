"""
Application Layer Protocol Messages for Whiteboard & Chat.

Computer Networks Principle:
Application Layer (OSI Layer 7 / TCP-IP Application Layer) defines the semantics,
syntax, and timing of exchanged messages. All messages are structured as JSON
documents with standardized fields:
  - type: MessageType identifier
  - room_id: Alphanumeric room session code
  - sender_id: Unique user / client identifier
  - timestamp: Unix epoch timestamp (seconds)
  - seq_num: Monotonically increasing sequence number (assigned by server)
  - payload: Typed payload dictionary specific to message type
"""

import json
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class MessageType(str, Enum):
    # Session & Room Management
    JOIN_ROOM = "JOIN_ROOM"
    ROOM_CREATED = "ROOM_CREATED"
    JOIN_SUCCESS = "JOIN_SUCCESS"
    JOIN_FAILURE = "JOIN_FAILURE"

    # Whiteboard Drawing Operations
    DRAW_EVENT = "DRAW_EVENT"
    ERASE_EVENT = "ERASE_EVENT"
    UNDO_EVENT = "UNDO_EVENT"
    REDO_EVENT = "REDO_EVENT"
    CLEAR_CANVAS = "CLEAR_CANVAS"

    # State Synchronization & Persistence
    CANVAS_STATE = "CANVAS_STATE"
    STATE_REQUEST = "STATE_REQUEST"
    LOAD_STATE = "LOAD_STATE"

    # Group Communication
    CHAT_MESSAGE = "CHAT_MESSAGE"

    # Presence & Participant Management
    PARTICIPANT_JOINED = "PARTICIPANT_JOINED"
    PARTICIPANT_LEFT = "PARTICIPANT_LEFT"
    PARTICIPANT_LIST = "PARTICIPANT_LIST"
    ROOM_LIST = "ROOM_LIST"

    # Health, Diagnostics & Control
    PING = "PING"
    PONG = "PONG"
    ERROR = "ERROR"
    DISCONNECT = "DISCONNECT"

    # Legacy Backward-Compatibility Aliases
    JOIN = "JOIN_ROOM"
    JOIN_ACK = "JOIN_SUCCESS"
    JOIN_REJECT = "JOIN_FAILURE"
    DRAW_SEGMENT = "DRAW_EVENT"
    DRAW_STROKE = "DRAW_EVENT"
    SHAPE = "DRAW_EVENT"
    ERASE = "ERASE_EVENT"
    UNDO = "UNDO_EVENT"
    REDO = "REDO_EVENT"
    CLEAR = "CLEAR_CANVAS"
    SYNC_STATE = "CANVAS_STATE"
    SYNC_REQUEST = "STATE_REQUEST"
    CHAT = "CHAT_MESSAGE"
    USER_JOINED = "PARTICIPANT_JOINED"
    USER_LEFT = "PARTICIPANT_LEFT"
    USER_LIST = "PARTICIPANT_LIST"
    HEARTBEAT = "PING"
    LEAVE = "DISCONNECT"
    RECONNECT = "JOIN_ROOM"


class ProtocolMessage:
    """
    Standard protocol envelope representation adhering to the mandatory schema:
    {
      "type": str,
      "room_id": str,
      "sender_id": str,
      "timestamp": float,
      "seq_num": Optional[int],
      "payload": dict
    }
    """

    def __init__(
        self,
        msg_type: Union[MessageType, str],
        payload: Optional[Dict[str, Any]] = None,
        room_id: str = "MAIN",
        sender_id: str = "SYSTEM",
        timestamp: Optional[float] = None,
        seq_num: Optional[int] = None,
        msg_id: Optional[str] = None,
        # Backward compatibility kwargs
        room_code: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        if isinstance(msg_type, MessageType):
            self.msg_type = msg_type.value
        else:
            self.msg_type = str(msg_type)

        self.payload = payload if payload is not None else {}
        self.room_id = (room_code or room_id or "MAIN").upper().strip()
        self.sender_id = str(user_id or sender_id or "SYSTEM")
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.seq_num = seq_num
        self.msg_id = msg_id or uuid.uuid4().hex

    # Properties for dual-access compatibility ("type", "room_code", "user_id")
    @property
    def type(self) -> str:
        return self.msg_type

    @type.setter
    def type(self, val: str) -> None:
        self.msg_type = val

    @property
    def room_code(self) -> str:
        return self.room_id

    @room_code.setter
    def room_code(self, val: str) -> None:
        self.room_id = val

    @property
    def user_id(self) -> str:
        return self.sender_id

    @user_id.setter
    def user_id(self, val: str) -> None:
        self.sender_id = val

    def to_dict(self) -> Dict[str, Any]:
        """Serializes into standard JSON protocol dictionary."""
        d = {
            "type": self.msg_type,
            "room_id": self.room_id,
            "sender_id": self.sender_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }
        if self.seq_num is not None:
            d["seq_num"] = self.seq_num

        # Compatibility fields for legacy consumers
        d["msg_type"] = self.msg_type
        d["room_code"] = self.room_id
        d["user_id"] = self.sender_id
        d["msg_id"] = self.msg_id
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProtocolMessage":
        """Reconstructs a ProtocolMessage from dictionary."""
        msg_type = data.get("type") or data.get("msg_type")
        if not msg_type:
            raise ValueError("Invalid message format: missing 'type' field")

        room_id = data.get("room_id") or data.get("room_code") or "MAIN"
        sender_id = data.get("sender_id") or data.get("user_id") or "SYSTEM"
        timestamp = data.get("timestamp")
        seq_num = data.get("seq_num")
        msg_id = data.get("msg_id")
        payload = data.get("payload") or {}

        return cls(
            msg_type=msg_type,
            payload=payload,
            room_id=room_id,
            sender_id=sender_id,
            timestamp=timestamp,
            seq_num=seq_num,
            msg_id=msg_id,
        )

    def __repr__(self) -> str:
        seq_str = f" seq={self.seq_num}" if self.seq_num is not None else ""
        return f"<ProtocolMessage type={self.msg_type} room={self.room_id} sender={self.sender_id}{seq_str}>"


def serialize_message(message: ProtocolMessage) -> bytes:
    """Serializes a ProtocolMessage into UTF-8 JSON bytes."""
    return json.dumps(message.to_dict(), separators=(",", ":")).encode("utf-8")


def deserialize_message(raw_bytes: bytes) -> ProtocolMessage:
    """Deserializes UTF-8 JSON bytes into a ProtocolMessage object."""
    from .framer import decode_message
    return decode_message(raw_bytes)


# =============================================================================
# Standard Factory Helper Functions (Section 4 Mandatory Message Types)
# =============================================================================

def create_join_room_msg(
    username: str,
    client_id: str,
    room_id: str = "MAIN",
    passcode: str = "",
    is_reconnect: bool = False,
    last_seq_num: int = 0,
) -> ProtocolMessage:
    """Client -> Server: Request to join a specific whiteboard room."""
    return ProtocolMessage(
        msg_type=MessageType.JOIN_ROOM,
        room_id=room_id,
        sender_id=client_id,
        payload={
            "username": username,
            "client_id": client_id,
            "passcode": passcode,
            "is_reconnect": is_reconnect,
            "last_seq_num": last_seq_num,
            "version": "1.0",
        },
    )


def create_room_created_msg(
    room_id: str,
    room_name: str,
    created_by: str,
) -> ProtocolMessage:
    """Server -> Client: Notification that a new room has been instantiated."""
    return ProtocolMessage(
        msg_type=MessageType.ROOM_CREATED,
        room_id=room_id,
        sender_id="SERVER",
        payload={
            "room_id": room_id,
            "name": room_name,
            "created_by": created_by,
        },
    )


def create_join_success_msg(
    user_id: str,
    username: str,
    color: str,
    active_users: List[Dict[str, Any]],
    current_actions: List[Dict[str, Any]],
    room_id: str = "MAIN",
    chat_history: Optional[List[Dict[str, Any]]] = None,
    seq_num: int = 0,
) -> ProtocolMessage:
    """Server -> Client: Handshake approval with authoritative state sync."""
    return ProtocolMessage(
        msg_type=MessageType.JOIN_SUCCESS,
        room_id=room_id,
        sender_id="SERVER",
        seq_num=seq_num,
        payload={
            "user_id": user_id,
            "username": username,
            "color": color,
            "room_id": room_id,
            "room_code": room_id,
            "users": active_users,
            "actions": current_actions,
            "chat_history": chat_history or [],
            "seq_num": seq_num,
        },
    )


def create_join_failure_msg(reason: str, room_id: str = "MAIN") -> ProtocolMessage:
    """Server -> Client: Rejection of room join request."""
    return ProtocolMessage(
        msg_type=MessageType.JOIN_FAILURE,
        room_id=room_id,
        sender_id="SERVER",
        payload={"reason": reason},
    )


def create_draw_event_msg(
    sender_id: str,
    room_id: str,
    stroke_id: str,
    points: List[List[float]],
    color: str,
    width: int,
    shape_type: str = "pen",
    fill: Optional[str] = None,
    is_live: bool = False,
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """
    Client <-> Server: Drawing stroke or geometric shape event.
    Conforms directly to the schema example:
    {
      "type": "DRAW_EVENT",
      "room_id": "ROOM123",
      "sender_id": "USER001",
      "timestamp": 1790000000,
      "payload": {
        "stroke_id": "STROKE001",
        "color": "#FF0000",
        "width": 4,
        "points": [[100, 120], [105, 125]]
      }
    }
    """
    payload_dict = {
        "stroke_id": stroke_id,
        "color": color,
        "width": width,
        "points": points,
        "shape_type": shape_type,
        "fill": fill,
        "is_live": is_live,
    }
    # Embed complete action dict for state manager compatibility
    payload_dict["action"] = {
        "action_id": stroke_id,
        "action_type": shape_type,
        "user_id": sender_id,
        "username": "",
        "color": color,
        "width": width,
        "points": points,
        "fill": fill,
    }
    return ProtocolMessage(
        msg_type=MessageType.DRAW_EVENT,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload=payload_dict,
    )


def create_erase_event_msg(
    sender_id: str,
    room_id: str,
    stroke_id: str,
    points: List[List[float]],
    width: int,
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """Client <-> Server: Eraser path event."""
    payload_dict = {
        "stroke_id": stroke_id,
        "points": points,
        "width": width,
        "action": {
            "action_id": stroke_id,
            "action_type": "eraser",
            "user_id": sender_id,
            "color": "#FFFFFF",
            "width": width,
            "points": points,
        },
    }
    return ProtocolMessage(
        msg_type=MessageType.ERASE_EVENT,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload=payload_dict,
    )


def create_undo_event_msg(
    sender_id: str,
    room_id: str,
    action_id: Optional[str] = None,
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """Client <-> Server: Collaborative undo event."""
    return ProtocolMessage(
        msg_type=MessageType.UNDO_EVENT,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload={"action_id": action_id, "user_id": sender_id},
    )


def create_redo_event_msg(
    sender_id: str,
    room_id: str,
    action_id: Optional[str] = None,
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """Client <-> Server: Collaborative redo event."""
    return ProtocolMessage(
        msg_type=MessageType.REDO_EVENT,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload={"action_id": action_id, "user_id": sender_id},
    )


def create_clear_canvas_msg(
    sender_id: str,
    room_id: str,
    username: str = "",
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """Client <-> Server: Clear entire canvas for all room participants."""
    return ProtocolMessage(
        msg_type=MessageType.CLEAR_CANVAS,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload={"user_id": sender_id, "username": username},
    )


def create_canvas_state_msg(
    room_id: str,
    actions: List[Dict[str, Any]],
    chat_history: Optional[List[Dict[str, Any]]] = None,
    seq_num: int = 0,
) -> ProtocolMessage:
    """Server -> Client: Authoritative canvas snapshot."""
    return ProtocolMessage(
        msg_type=MessageType.CANVAS_STATE,
        room_id=room_id,
        sender_id="SERVER",
        seq_num=seq_num,
        payload={"actions": actions, "chat_history": chat_history or [], "seq_num": seq_num},
    )


def create_state_request_msg(
    sender_id: str,
    room_id: str,
    last_seq_num: int = 0,
) -> ProtocolMessage:
    """Client -> Server: Explicit request for state recovery."""
    return ProtocolMessage(
        msg_type=MessageType.STATE_REQUEST,
        room_id=room_id,
        sender_id=sender_id,
        payload={"last_seq_num": last_seq_num},
    )


def create_chat_message_msg(
    sender_id: str,
    room_id: str,
    username: str,
    text: str,
    color: str = "#2C3E50",
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """Client <-> Server: Integrated group chat message."""
    return ProtocolMessage(
        msg_type=MessageType.CHAT_MESSAGE,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload={
            "user_id": sender_id,
            "username": username,
            "text": text,
            "color": color,
            "time": time.strftime("%H:%M:%S"),
        },
    )


def create_participant_joined_msg(
    sender_id: str,
    room_id: str,
    username: str,
    color: str,
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """Server -> Clients: Broadcast notification of participant arrival."""
    return ProtocolMessage(
        msg_type=MessageType.PARTICIPANT_JOINED,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload={"user_id": sender_id, "username": username, "color": color},
    )


def create_participant_left_msg(
    sender_id: str,
    room_id: str,
    username: str,
    reason: str = "disconnected",
    seq_num: Optional[int] = None,
) -> ProtocolMessage:
    """Server -> Clients: Broadcast notification of participant departure."""
    return ProtocolMessage(
        msg_type=MessageType.PARTICIPANT_LEFT,
        room_id=room_id,
        sender_id=sender_id,
        seq_num=seq_num,
        payload={"user_id": sender_id, "username": username, "reason": reason},
    )


def create_participant_list_msg(
    room_id: str,
    participants: List[Dict[str, Any]],
) -> ProtocolMessage:
    """Server -> Client: Complete active participant roster."""
    return ProtocolMessage(
        msg_type=MessageType.PARTICIPANT_LIST,
        room_id=room_id,
        sender_id="SERVER",
        payload={"users": participants},
    )


def create_ping_msg(sender_id: str, room_id: str = "MAIN") -> ProtocolMessage:
    """Client <-> Server: Keepalive health ping."""
    return ProtocolMessage(
        msg_type=MessageType.PING,
        room_id=room_id,
        sender_id=sender_id,
        payload={"sender_id": sender_id},
    )


def create_pong_msg(sender_id: str, room_id: str = "MAIN") -> ProtocolMessage:
    """Client <-> Server: Keepalive health reply."""
    return ProtocolMessage(
        msg_type=MessageType.PONG,
        room_id=room_id,
        sender_id=sender_id,
        payload={"sender_id": sender_id, "is_reply": True},
    )


def create_error_msg(
    sender_id: str,
    room_id: str,
    error_code: str,
    error_message: str,
) -> ProtocolMessage:
    """Server -> Client: Error notification."""
    return ProtocolMessage(
        msg_type=MessageType.ERROR,
        room_id=room_id,
        sender_id=sender_id,
        payload={"error_code": error_code, "error_message": error_message},
    )


def create_disconnect_msg(
    sender_id: str,
    room_id: str = "MAIN",
    reason: str = "normal",
) -> ProtocolMessage:
    """Client -> Server: Graceful departure notice."""
    return ProtocolMessage(
        msg_type=MessageType.DISCONNECT,
        room_id=room_id,
        sender_id=sender_id,
        payload={"reason": reason},
    )


def create_load_state_msg(actions: List[Dict[str, Any]], loaded_by: str, room_id: str = "MAIN") -> ProtocolMessage:
    return ProtocolMessage(
        msg_type=MessageType.LOAD_STATE,
        room_id=room_id,
        sender_id="SYSTEM",
        payload={"actions": actions, "loaded_by": loaded_by},
    )


def create_room_list_msg(rooms: List[Dict[str, Any]]) -> ProtocolMessage:
    return ProtocolMessage(
        msg_type=MessageType.ROOM_LIST,
        room_id="SYSTEM",
        sender_id="SERVER",
        payload={"rooms": rooms},
    )


# =============================================================================
# Backward Compatibility Factory Aliases
# =============================================================================

def create_join_msg(
    username: str,
    client_id: str,
    room_code: str = "MAIN",
    passcode: str = "",
    is_reconnect: bool = False,
    last_seq_num: int = 0,
) -> ProtocolMessage:
    return create_join_room_msg(
        username=username,
        client_id=client_id,
        room_id=room_code,
        passcode=passcode,
        is_reconnect=is_reconnect,
        last_seq_num=last_seq_num,
    )


def create_join_ack_msg(
    user_id: str,
    username: str,
    color: str,
    active_users: List[Dict[str, Any]],
    current_actions: List[Dict[str, Any]],
    room_code: str = "MAIN",
    chat_history: Optional[List[Dict[str, Any]]] = None,
    seq_num: int = 0,
) -> ProtocolMessage:
    return create_join_success_msg(
        user_id=user_id,
        username=username,
        color=color,
        active_users=active_users,
        current_actions=current_actions,
        room_id=room_code,
        chat_history=chat_history,
        seq_num=seq_num,
    )


def create_join_reject_msg(reason: str, room_code: str = "MAIN") -> ProtocolMessage:
    return create_join_failure_msg(reason=reason, room_id=room_code)


def create_draw_segment_msg(
    user_id: str,
    stroke_id: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: str,
    width: int,
    room_code: str = "MAIN",
) -> ProtocolMessage:
    """Ephemeral line segment transmitted during mouse drag."""
    return create_draw_event_msg(
        sender_id=user_id,
        room_id=room_code,
        stroke_id=stroke_id,
        points=[[x1, y1], [x2, y2]],
        color=color,
        width=width,
        shape_type="pen",
        is_live=True,
    )


def create_draw_stroke_msg(action_dict: Dict[str, Any], room_code: str = "MAIN") -> ProtocolMessage:
    user_id = action_dict.get("user_id", "unknown")
    return create_draw_event_msg(
        sender_id=user_id,
        room_id=room_code,
        stroke_id=action_dict.get("action_id", uuid.uuid4().hex),
        points=action_dict.get("points", []),
        color=action_dict.get("color", "#000000"),
        width=action_dict.get("width", 3),
        shape_type=action_dict.get("action_type", "pen"),
        fill=action_dict.get("fill"),
    )


def create_shape_msg(action_dict: Dict[str, Any], room_code: str = "MAIN") -> ProtocolMessage:
    return create_draw_stroke_msg(action_dict, room_code=room_code)


def create_erase_msg(action_dict: Dict[str, Any], room_code: str = "MAIN") -> ProtocolMessage:
    user_id = action_dict.get("user_id", "unknown")
    return create_erase_event_msg(
        sender_id=user_id,
        room_id=room_code,
        stroke_id=action_dict.get("action_id", uuid.uuid4().hex),
        points=action_dict.get("points", []),
        width=action_dict.get("width", 10),
    )


def create_clear_msg(user_id: str, username: str, room_code: str = "MAIN") -> ProtocolMessage:
    return create_clear_canvas_msg(sender_id=user_id, room_id=room_code, username=username)


def create_undo_msg(user_id: str, action_id: Optional[str] = None, room_code: str = "MAIN") -> ProtocolMessage:
    return create_undo_event_msg(sender_id=user_id, room_id=room_code, action_id=action_id)


def create_redo_msg(user_id: str, action_id: Optional[str] = None, room_code: str = "MAIN") -> ProtocolMessage:
    return create_redo_event_msg(sender_id=user_id, room_id=room_code, action_id=action_id)


def create_chat_msg(user_id: str, username: str, text: str, color: str = "#000000", room_code: str = "MAIN") -> ProtocolMessage:
    return create_chat_message_msg(sender_id=user_id, room_id=room_code, username=username, text=text, color=color)


def create_heartbeat_msg(sender_id: str, is_reply: bool = False, room_code: str = "MAIN") -> ProtocolMessage:
    return create_pong_msg(sender_id, room_code) if is_reply else create_ping_msg(sender_id, room_code)


def create_user_joined_msg(user_id: str, username: str, color: str, room_code: str = "MAIN") -> ProtocolMessage:
    return create_participant_joined_msg(sender_id=user_id, room_id=room_code, username=username, color=color)


def create_user_left_msg(user_id: str, username: str, reason: str = "disconnected", room_code: str = "MAIN") -> ProtocolMessage:
    return create_participant_left_msg(sender_id=user_id, room_id=room_code, username=username, reason=reason)


def create_user_list_msg(users: List[Dict[str, Any]], room_code: str = "MAIN") -> ProtocolMessage:
    return create_participant_list_msg(room_id=room_code, participants=users)


def create_sync_state_msg(actions: List[Dict[str, Any]], room_code: str = "MAIN") -> ProtocolMessage:
    return create_canvas_state_msg(room_id=room_code, actions=actions)
