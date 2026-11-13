"""
Whiteboard Action Data Model.
Represents individual drawing, shape, erase, or canvas actions with serialization support.
"""

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional


class ActionType(str, Enum):
    PEN = "pen"
    LINE = "line"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    ARROW = "arrow"
    ERASER = "eraser"
    CLEAR = "clear"


class WhiteboardAction:
    """
    Represents an immutable or mutable action on the whiteboard canvas.
    """

    def __init__(
        self,
        action_type: ActionType or str,
        user_id: str,
        username: str,
        action_id: Optional[str] = None,
        color: str = "#000000",
        width: int = 3,
        points: Optional[List[List[float]]] = None,
        fill: Optional[str] = None,
        timestamp: Optional[float] = None,
        is_undone: bool = False,
    ):
        self.action_id = action_id or uuid.uuid4().hex
        self.action_type = ActionType(action_type).value if isinstance(action_type, (ActionType, str)) else str(action_type)
        self.user_id = user_id
        self.username = username
        self.color = color
        self.width = width
        # Points: For pen/eraser, a list of [x, y] coordinates: [[x1, y1], [x2, y2], ...]
        # For shapes: two points: [[x1, y1], [x2, y2]]
        self.points = points if points is not None else []
        self.fill = fill
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.is_undone = is_undone

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "user_id": self.user_id,
            "username": self.username,
            "color": self.color,
            "width": self.width,
            "points": self.points,
            "fill": self.fill,
            "timestamp": self.timestamp,
            "is_undone": self.is_undone,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WhiteboardAction":
        return cls(
            action_type=d.get("action_type", ActionType.PEN.value),
            user_id=d.get("user_id", "unknown"),
            username=d.get("username", "Anonymous"),
            action_id=d.get("action_id"),
            color=d.get("color", "#000000"),
            width=d.get("width", 3),
            points=d.get("points", []),
            fill=d.get("fill"),
            timestamp=d.get("timestamp"),
            is_undone=d.get("is_undone", False),
        )

    def __repr__(self) -> str:
        return f"<Action {self.action_type} id={self.action_id[:6]} user={self.username} undone={self.is_undone}>"
