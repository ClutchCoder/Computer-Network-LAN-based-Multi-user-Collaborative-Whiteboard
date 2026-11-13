"""
Authoritative Whiteboard State Manager with Collaborative Undo/Redo & Image Export.
"""

import json
import math
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from .action import ActionType, WhiteboardAction


class WhiteboardState:
    """
    Thread-safe authoritative state manager for the shared whiteboard.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._actions: List[WhiteboardAction] = []
        self._action_ids: set = set()
        # Redo stack tracks action_ids that were recently undone
        self._redo_stack: List[str] = []

    def add_action(self, action: WhiteboardAction) -> bool:
        """
        Appends a new action to the active history.
        Detects and rejects duplicate action IDs to prevent state corruption.
        Returns True if added, False if duplicate.
        """
        with self._lock:
            if action.action_id in self._action_ids:
                return False
            self._actions.append(action)
            self._action_ids.add(action.action_id)
            # When a new action is performed, clear redo stack
            self._redo_stack.clear()
            return True

    def has_action(self, action_id: str) -> bool:
        """Checks if an action with the given ID exists in the whiteboard state."""
        with self._lock:
            return action_id in self._action_ids

    def get_all_actions(self) -> List[WhiteboardAction]:
        """Returns shallow copy of all actions."""
        with self._lock:
            return list(self._actions)

    def get_active_actions(self) -> List[WhiteboardAction]:
        """Returns all actions that are not currently marked as undone."""
        with self._lock:
            return [a for a in self._actions if not a.is_undone]

    def get_action_by_id(self, action_id: str) -> Optional[WhiteboardAction]:
        with self._lock:
            for a in self._actions:
                if a.action_id == action_id:
                    return a
        return None

    def undo_action(self, user_id: Optional[str] = None, target_action_id: Optional[str] = None) -> Optional[WhiteboardAction]:
        """
        Collaborative Undo:
        If target_action_id is specified, undos that specific action.
        Else if user_id is provided, undos that user's most recent active action.
        Else, undos the board's most recent active action.
        """
        with self._lock:
            target = None
            if target_action_id:
                target = self.get_action_by_id(target_action_id)
                if target and target.is_undone:
                    target = None
            elif user_id:
                # Find user's last active action
                for a in reversed(self._actions):
                    if a.user_id == user_id and not a.is_undone:
                        target = a
                        break

            # Fallback to board's last active action if user has none
            if not target:
                for a in reversed(self._actions):
                    if not a.is_undone:
                        target = a
                        break

            if target:
                target.is_undone = True
                self._redo_stack.append(target.action_id)
                return target
            return None

    def redo_action(self, user_id: Optional[str] = None, target_action_id: Optional[str] = None) -> Optional[WhiteboardAction]:
        """
        Collaborative Redo:
        Restores the most recently undone action.
        """
        with self._lock:
            target = None
            if target_action_id:
                target = self.get_action_by_id(target_action_id)
                if target and not target.is_undone:
                    target = None
            elif user_id and self._redo_stack:
                # Check redo stack for user's action
                for act_id in reversed(self._redo_stack):
                    candidate = self.get_action_by_id(act_id)
                    if candidate and candidate.user_id == user_id and candidate.is_undone:
                        target = candidate
                        self._redo_stack.remove(act_id)
                        break

            # Fallback to general redo stack
            if not target and self._redo_stack:
                act_id = self._redo_stack.pop()
                target = self.get_action_by_id(act_id)

            if target:
                target.is_undone = False
                return target
            return None

    def clear(self, user_id: str = "system", username: str = "System") -> WhiteboardAction:
        """
        Wipes active actions by recording a CLEAR action.
        """
        with self._lock:
            # We record a CLEAR action so it can even be undone!
            clear_action = WhiteboardAction(
                action_type=ActionType.CLEAR,
                user_id=user_id,
                username=username,
            )
            # Mark all preceding actions as undone
            for a in self._actions:
                a.is_undone = True
            self._actions.append(clear_action)
            self._action_ids.add(clear_action.action_id)
            self._redo_stack.clear()
            return clear_action

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """Serializes all actions to a list of dictionaries."""
        with self._lock:
            return [a.to_dict() for a in self._actions]

    def load_from_dict_list(self, dict_list: List[Dict[str, Any]]) -> None:
        """Replaces current state with actions from a list of dictionaries."""
        with self._lock:
            self._actions = [WhiteboardAction.from_dict(d) for d in dict_list]
            self._action_ids = {a.action_id for a in self._actions}
            self._redo_stack.clear()
            for a in self._actions:
                if a.is_undone:
                    self._redo_stack.append(a.action_id)

    def save_to_file(self, filepath: str) -> None:
        """Persists the whiteboard session state to a JSON file."""
        with self._lock:
            data = {
                "format": "LAN_WHITEBOARD_STATE_V1",
                "action_count": len(self._actions),
                "actions": self.to_dict_list(),
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

    def load_from_file(self, filepath: str) -> None:
        """Restores the whiteboard session state from a JSON file."""
        with self._lock:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            actions_raw = data.get("actions", [])
            self.load_from_dict_list(actions_raw)

    def export_to_image(self, filepath: str, width: int = 1280, height: int = 800) -> bool:
        """
        Renders the active actions to an image file (PNG/JPEG) using Pillow.
        Returns True on success, False if Pillow is missing.
        """
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return False

        with self._lock:
            img = Image.new("RGB", (width, height), "white")
            draw = ImageDraw.Draw(img)

            for action in self.get_active_actions():
                act_type = action.action_type
                pts = action.points
                color = action.color
                w = max(1, action.width)

                if act_type == ActionType.CLEAR.value:
                    # Canvas cleared
                    draw.rectangle([0, 0, width, height], fill="white")

                elif act_type in (ActionType.PEN.value, ActionType.ERASER.value):
                    fill_color = "white" if act_type == ActionType.ERASER.value else color
                    if len(pts) == 1:
                        x, y = pts[0]
                        r = w / 2
                        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill_color)
                    elif len(pts) > 1:
                        # Draw line segments connecting points
                        for i in range(len(pts) - 1):
                            p1 = pts[i]
                            p2 = pts[i + 1]
                            draw.line([p1[0], p1[1], p2[0], p2[1]], fill=fill_color, width=w)
                            # Draw circles at joints for smooth strokes
                            r = w / 2
                            draw.ellipse([p1[0] - r, p1[1] - r, p1[0] + r, p1[1] + r], fill=fill_color)

                elif act_type == ActionType.LINE.value:
                    if len(pts) >= 2:
                        p1, p2 = pts[0], pts[1]
                        draw.line([p1[0], p1[1], p2[0], p2[1]], fill=color, width=w)

                elif act_type == ActionType.RECTANGLE.value:
                    if len(pts) >= 2:
                        p1, p2 = pts[0], pts[1]
                        x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1])
                        x2, y2 = max(p1[0], p2[0]), max(p1[1], p2[1])
                        draw.rectangle([x1, y1, x2, y2], outline=color, width=w)

                elif act_type == ActionType.CIRCLE.value:
                    if len(pts) >= 2:
                        p1, p2 = pts[0], pts[1]
                        x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1])
                        x2, y2 = max(p1[0], p2[0]), max(p1[1], p2[1])
                        draw.ellipse([x1, y1, x2, y2], outline=color, width=w)

                elif act_type == ActionType.ARROW.value:
                    if len(pts) >= 2:
                        p1, p2 = pts[0], pts[1]
                        draw.line([p1[0], p1[1], p2[0], p2[1]], fill=color, width=w)
                        # Draw arrowhead
                        dx = p2[0] - p1[0]
                        dy = p2[1] - p1[1]
                        angle = math.atan2(dy, dx)
                        arrow_len = max(10, w * 3)
                        arrow_angle = math.pi / 6
                        left_x = p2[0] - arrow_len * math.cos(angle - arrow_angle)
                        left_y = p2[1] - arrow_len * math.sin(angle - arrow_angle)
                        right_x = p2[0] - arrow_len * math.cos(angle + arrow_angle)
                        right_y = p2[1] - arrow_len * math.sin(angle + arrow_angle)
                        draw.polygon([(p2[0], p2[1]), (left_x, left_y), (right_x, right_y)], fill=color)

            img.save(filepath)
            return True
