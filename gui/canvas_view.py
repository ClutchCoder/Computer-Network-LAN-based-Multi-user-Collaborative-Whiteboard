"""
Interactive Whiteboard Canvas Component.
Handles mouse interactions, zero-latency local drawing, shape previews,
batched incremental point streaming, and remote action rendering.
"""

import math
import time
import tkinter as tk
import uuid
from enum import Enum
from typing import Callable, Dict, List, Optional

import config
from state.action import ActionType, WhiteboardAction


class Tool(str, Enum):
    PEN = "pen"
    LINE = "line"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    ARROW = "arrow"
    ERASER = "eraser"


class CanvasView(tk.Frame):
    """
    Whiteboard Drawing Canvas with smooth stroke interpolation, shape rubber-banding,
    and batched incremental point transmission.
    """

    def __init__(
        self,
        parent: tk.Widget,
        width: int = config.DEFAULT_CANVAS_WIDTH,
        height: int = config.DEFAULT_CANVAS_HEIGHT,
        bg_color: str = config.DEFAULT_BG_COLOR,
        on_segment_drawn: Optional[Callable[[str, float, float, float, float, str, int], None]] = None,
        on_points_drawn: Optional[Callable[[str, List[List[float]], str, int], None]] = None,
        on_action_committed: Optional[Callable[[WhiteboardAction], None]] = None,
    ):
        super().__init__(parent)
        self.canvas_width = width
        self.canvas_height = height
        self.bg_color = bg_color

        self.on_segment_drawn = on_segment_drawn
        self.on_points_drawn = on_points_drawn
        self.on_action_committed = on_action_committed

        # Drawing attributes
        self.active_tool: Tool = Tool.PEN
        self.stroke_color: str = config.DEFAULT_STROKE_COLOR
        self.stroke_width: int = config.DEFAULT_STROKE_WIDTH

        # Interaction tracking state
        self._current_stroke_id: Optional[str] = None
        self._stroke_points: List[List[float]] = []
        self._batch_points: List[List[float]] = []
        self._last_point: Optional[tuple] = None
        self._start_point: Optional[tuple] = None
        self._last_send_time = 0.0

        self._build_ui()
        self._bind_events()

    def _build_ui(self) -> None:
        # Canvas with scrollbars
        self.h_scroll = tk.Scrollbar(self, orient=tk.HORIZONTAL)
        self.v_scroll = tk.Scrollbar(self, orient=tk.VERTICAL)

        self.canvas = tk.Canvas(
            self,
            width=self.canvas_width,
            height=self.canvas_height,
            bg=self.bg_color,
            cursor="crosshair",
            xscrollcommand=self.h_scroll.set,
            yscrollcommand=self.v_scroll.set,
            scrollregion=(0, 0, self.canvas_width, self.canvas_height),
            highlightthickness=0,
        )

        self.h_scroll.config(command=self.canvas.xview)
        self.v_scroll.config(command=self.canvas.yview)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _bind_events(self) -> None:
        self.canvas.bind("<Button-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)

    def set_tool(self, tool: Tool) -> None:
        self.active_tool = tool
        if tool == Tool.ERASER:
            self.canvas.config(cursor="circle")
        else:
            self.canvas.config(cursor="crosshair")

    def set_color(self, color: str) -> None:
        self.stroke_color = color

    def set_width(self, width: int) -> None:
        self.stroke_width = max(1, width)

    # --- Mouse Event Handlers ---

    def _get_canvas_coords(self, event: tk.Event) -> tuple:
        """Converts window mouse event coordinates to virtual canvas coordinates."""
        return (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def _on_mouse_down(self, event: tk.Event) -> None:
        cx, cy = self._get_canvas_coords(event)
        self._start_point = (cx, cy)
        self._last_point = (cx, cy)
        self._current_stroke_id = uuid.uuid4().hex
        self._stroke_points = [[cx, cy]]
        self._batch_points = [[cx, cy]]
        self._last_send_time = time.time()

        if self.active_tool in (Tool.PEN, Tool.ERASER):
            draw_color = self.bg_color if self.active_tool == Tool.ERASER else self.stroke_color
            r = self.stroke_width / 2.0
            # Draw initial dot locally
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=draw_color,
                outline=draw_color,
                tags=self._current_stroke_id,
            )
            # Emit live point or segment
            if self.on_points_drawn:
                self.on_points_drawn(self._current_stroke_id, [[cx, cy]], draw_color, self.stroke_width)
            elif self.on_segment_drawn:
                self.on_segment_drawn(
                    self._current_stroke_id, cx, cy, cx, cy, draw_color, self.stroke_width
                )

    def _on_mouse_drag(self, event: tk.Event) -> None:
        cx, cy = self._get_canvas_coords(event)

        if self.active_tool in (Tool.PEN, Tool.ERASER):
            if not self._last_point:
                return

            lx, ly = self._last_point
            draw_color = self.bg_color if self.active_tool == Tool.ERASER else self.stroke_color

            # Draw immediate line segment on local canvas (zero-latency local feedback)
            self.canvas.create_line(
                lx, ly, cx, cy,
                fill=draw_color,
                width=self.stroke_width,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                tags=self._current_stroke_id,
            )

            self._stroke_points.append([cx, cy])
            self._batch_points.append([cx, cy])
            self._last_point = (cx, cy)

            # Throttle network transmission and send batched points
            now = time.time()
            if now - self._last_send_time >= config.MOUSE_THROTTLE_INTERVAL:
                if len(self._batch_points) >= 2:
                    if self.on_points_drawn and self._current_stroke_id:
                        self.on_points_drawn(
                            self._current_stroke_id, list(self._batch_points), draw_color, self.stroke_width
                        )
                    elif self.on_segment_drawn and self._current_stroke_id:
                        p1, p2 = self._batch_points[0], self._batch_points[-1]
                        self.on_segment_drawn(
                            self._current_stroke_id, p1[0], p1[1], p2[0], p2[1], draw_color, self.stroke_width
                        )
                    # Retain last point as beginning of next batch segment
                    self._batch_points = [self._batch_points[-1]]
                self._last_send_time = now

        else:
            # Shape preview rubber-banding
            if not self._start_point:
                return
            sx, sy = self._start_point
            self.canvas.delete("ghost_preview")

            if self.active_tool == Tool.LINE:
                self.canvas.create_line(
                    sx, sy, cx, cy,
                    fill=self.stroke_color,
                    width=self.stroke_width,
                    tags="ghost_preview",
                )
            elif self.active_tool == Tool.RECTANGLE:
                self.canvas.create_rectangle(
                    sx, sy, cx, cy,
                    outline=self.stroke_color,
                    width=self.stroke_width,
                    tags="ghost_preview",
                )
            elif self.active_tool == Tool.CIRCLE:
                self.canvas.create_oval(
                    sx, sy, cx, cy,
                    outline=self.stroke_color,
                    width=self.stroke_width,
                    tags="ghost_preview",
                )
            elif self.active_tool == Tool.ARROW:
                self._draw_arrow_item(
                    sx, sy, cx, cy, self.stroke_color, self.stroke_width, tag="ghost_preview"
                )

    def _on_mouse_up(self, event: tk.Event) -> None:
        cx, cy = self._get_canvas_coords(event)

        if self.active_tool in (Tool.PEN, Tool.ERASER):
            if not self._current_stroke_id or not self._stroke_points:
                return

            action_type = ActionType.ERASER if self.active_tool == Tool.ERASER else ActionType.PEN
            color = self.bg_color if self.active_tool == Tool.ERASER else self.stroke_color

            # Finalize and flush any pending batched points
            if len(self._batch_points) >= 2:
                if self.on_points_drawn:
                    self.on_points_drawn(
                        self._current_stroke_id, list(self._batch_points), color, self.stroke_width
                    )

            action = WhiteboardAction(
                action_id=self._current_stroke_id,
                action_type=action_type,
                user_id="",  # Injected by client/server
                username="",
                color=color,
                width=self.stroke_width,
                points=self._stroke_points,
            )

            if self.on_action_committed:
                self.on_action_committed(action)

        else:
            # Commit Geometric Shape
            self.canvas.delete("ghost_preview")
            if not self._start_point:
                return
            sx, sy = self._start_point

            # Filter out accidental zero-length clicks
            if math.hypot(cx - sx, cy - sy) < 3:
                return

            act_type_map = {
                Tool.LINE: ActionType.LINE,
                Tool.RECTANGLE: ActionType.RECTANGLE,
                Tool.CIRCLE: ActionType.CIRCLE,
                Tool.ARROW: ActionType.ARROW,
            }

            action_type = act_type_map.get(self.active_tool, ActionType.LINE)
            action_id = uuid.uuid4().hex

            action = WhiteboardAction(
                action_id=action_id,
                action_type=action_type,
                user_id="",
                username="",
                color=self.stroke_color,
                width=self.stroke_width,
                points=[[sx, sy], [cx, cy]],
            )

            # Render permanent shape on local canvas
            self.render_action(action)

            if self.on_action_committed:
                self.on_action_committed(action)

        self._start_point = None
        self._last_point = None
        self._current_stroke_id = None
        self._stroke_points = []
        self._batch_points = []

    # --- Canvas Rendering Methods ---

    def draw_remote_points(
        self, stroke_id: str, points: List[List[float]], color: str, width: int
    ) -> None:
        """Renders live stroke point batches arriving incrementally over the network."""
        if not points:
            return
        if len(points) == 1:
            x, y = points[0]
            r = width / 2.0
            self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill=color,
                outline=color,
                tags=stroke_id,
            )
        else:
            flat_pts = [coord for pt in points for coord in pt]
            self.canvas.create_line(
                flat_pts,
                fill=color,
                width=width,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                smooth=True,
                tags=stroke_id,
            )

    def draw_remote_segment(
        self, stroke_id: str, x1: float, y1: float, x2: float, y2: float, color: str, width: int
    ) -> None:
        """Renders live stroke segments arriving from peers over the network."""
        if x1 == x2 and y1 == y2:
            r = width / 2.0
            self.canvas.create_oval(
                x1 - r, y1 - r, x1 + r, y1 + r,
                fill=color,
                outline=color,
                tags=stroke_id,
            )
        else:
            self.canvas.create_line(
                x1, y1, x2, y2,
                fill=color,
                width=width,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                tags=stroke_id,
            )

    def render_action(self, action: WhiteboardAction) -> None:
        """Renders a committed WhiteboardAction onto the canvas."""
        if action.is_undone:
            return

        act_type = action.action_type
        pts = action.points
        color = action.color
        w = max(1, action.width)
        tag = action.action_id

        if act_type == ActionType.CLEAR.value:
            self.canvas.delete("all")

        elif act_type in (ActionType.PEN.value, ActionType.ERASER.value):
            fill_color = self.bg_color if act_type == ActionType.ERASER.value else color
            if len(pts) == 1:
                x, y = pts[0]
                r = w / 2.0
                self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=fill_color, outline=fill_color, tags=tag)
            elif len(pts) > 1:
                flat_pts = [coord for pt in pts for coord in pt]
                self.canvas.create_line(
                    flat_pts,
                    fill=fill_color,
                    width=w,
                    capstyle=tk.ROUND,
                    joinstyle=tk.ROUND,
                    smooth=True,
                    tags=tag,
                )

        elif act_type == ActionType.LINE.value:
            if len(pts) >= 2:
                p1, p2 = pts[0], pts[1]
                self.canvas.create_line(p1[0], p1[1], p2[0], p2[1], fill=color, width=w, tags=tag)

        elif act_type == ActionType.RECTANGLE.value:
            if len(pts) >= 2:
                p1, p2 = pts[0], pts[1]
                self.canvas.create_rectangle(p1[0], p1[1], p2[0], p2[1], outline=color, width=w, tags=tag)

        elif act_type == ActionType.CIRCLE.value:
            if len(pts) >= 2:
                p1, p2 = pts[0], pts[1]
                self.canvas.create_oval(p1[0], p1[1], p2[0], p2[1], outline=color, width=w, tags=tag)

        elif act_type == ActionType.ARROW.value:
            if len(pts) >= 2:
                p1, p2 = pts[0], pts[1]
                self._draw_arrow_item(p1[0], p1[1], p2[0], p2[1], color, w, tag=tag)

    def _draw_arrow_item(
        self, x1: float, y1: float, x2: float, y2: float, color: str, width: int, tag: str
    ) -> None:
        """Draws a directed arrow with arrowhead at (x2, y2)."""
        arrow_shape = (max(10, width * 3), max(12, width * 3), max(5, width * 2))
        self.canvas.create_line(
            x1, y1, x2, y2,
            fill=color,
            width=width,
            arrow=tk.LAST,
            arrowshape=arrow_shape,
            tags=tag,
        )

    def redraw_all(self, actions: List[WhiteboardAction]) -> None:
        """Full canvas re-render. Used after Undo, Redo, Load, or late-join state sync."""
        self.canvas.delete("all")
        for action in actions:
            if not action.is_undone:
                self.render_action(action)

    def clear(self) -> None:
        self.canvas.delete("all")
