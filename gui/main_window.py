"""
Main Collaborative Whiteboard Application Window.
Integrates Canvas, Sidebar Chat/Roster, Toolbars, Menus, and the Network Event Loop.
"""

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import config
from network.tcp_client import WhiteboardClient
from protocol.messages import MessageType, ProtocolMessage
from state.action import ActionType, WhiteboardAction
from state.whiteboard_state import WhiteboardState

from .canvas_view import CanvasView, Tool
from .chat_panel import ChatPanel
from .color_palette import ColorPaletteWidget
from .connection_dialog import ConnectionDialog


class MainWindow(tk.Tk):
    """
    Main Tkinter application window for the LAN-Based Multi-User Collaborative Whiteboard.
    """

    def __init__(
        self,
        username: str = "Student",
        auto_connect: bool = False,
        host: str = "127.0.0.1",
        port: int = config.DEFAULT_SERVER_PORT,
        room_code: str = "MAIN",
        passcode: str = "",
    ):
        super().__init__()
        self.title("LAN Collaborative Whiteboard")
        self.geometry("1400x880")
        self.minsize(1000, 650)

        # Application state
        self.client = WhiteboardClient(username=username)
        self.local_state = WhiteboardState()
        self.current_tool = Tool.PEN
        self.active_users = []

        # Build UI layout
        self._build_menu()
        self._build_toolbar()
        self._build_main_workspace()
        self._build_status_bar()
        self._bind_shortcuts()

        # Start periodic network event poll (~66 FPS loop)
        self.after(15, self._poll_network_events)

        # Handle window close cleanly
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if auto_connect:
            self.after(200, lambda: self._do_connect(username, host, port, room_code, passcode))
        else:
            self.after(200, self._open_connection_dialog)

    # =========================================================================
    # UI Building Blocks
    # =========================================================================

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        # --- File Menu ---
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Connect to Server...", accelerator="Ctrl+O", command=self._open_connection_dialog)
        file_menu.add_command(label="Disconnect", command=self._do_disconnect)
        file_menu.add_separator()
        file_menu.add_command(label="Save Session State (JSON)...", accelerator="Ctrl+S", command=self._save_state_dialog)
        file_menu.add_command(label="Load Session State (JSON)...", command=self._load_state_dialog)
        file_menu.add_command(label="Export Canvas Image (PNG)...", accelerator="Ctrl+E", command=self._export_image_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # --- Edit Menu ---
        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self._action_undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y", command=self._action_redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Canvas", accelerator="Ctrl+Shift+C", command=self._action_clear)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        # --- Tools Menu ---
        tools_menu = tk.Menu(menubar, tearoff=False)
        tools_menu.add_command(label="Pen (Freehand)", command=lambda: self._select_tool(Tool.PEN))
        tools_menu.add_command(label="Straight Line", command=lambda: self._select_tool(Tool.LINE))
        tools_menu.add_command(label="Rectangle", command=lambda: self._select_tool(Tool.RECTANGLE))
        tools_menu.add_command(label="Circle / Oval", command=lambda: self._select_tool(Tool.CIRCLE))
        tools_menu.add_command(label="Arrow", command=lambda: self._select_tool(Tool.ARROW))
        tools_menu.add_command(label="Eraser", command=lambda: self._select_tool(Tool.ERASER))
        menubar.add_cascade(label="Tools", menu=tools_menu)

        # --- Network / Help Menu ---
        net_menu = tk.Menu(menubar, tearoff=False)
        net_menu.add_command(label="Reconnect / State Recovery", command=self._action_reconnect)
        net_menu.add_command(label="Request Full State Sync", command=self._request_sync)
        net_menu.add_command(label="Network Details...", command=self._show_network_info)
        menubar.add_cascade(label="Network", menu=net_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About Project", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, padding=(6, 4))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        # --- Tool Selection Buttons ---
        tools_frame = ttk.LabelFrame(toolbar, text="Drawing Tools", padding=(6, 4))
        tools_frame.pack(side=tk.LEFT, padx=6)

        self.tool_buttons = {}
        tool_defs = [
            (Tool.PEN, "✏️ Pen"),
            (Tool.LINE, "📏 Line"),
            (Tool.RECTANGLE, "⬜ Rect"),
            (Tool.CIRCLE, "⭕ Circle"),
            (Tool.ARROW, "➡️ Arrow"),
            (Tool.ERASER, "🧹 Eraser"),
        ]

        for t_val, label in tool_defs:
            btn = ttk.Button(
                tools_frame,
                text=label,
                width=8,
                command=lambda t=t_val: self._select_tool(t),
            )
            btn.pack(side=tk.LEFT, padx=2)
            self.tool_buttons[t_val] = btn

        # --- Colors and Width Widget ---
        self.color_widget = ColorPaletteWidget(
            toolbar,
            initial_color=config.DEFAULT_STROKE_COLOR,
            initial_width=config.DEFAULT_STROKE_WIDTH,
            on_color_changed=self._on_color_changed,
            on_width_changed=self._on_width_changed,
        )
        self.color_widget.pack(side=tk.LEFT, padx=6)

        # --- Actions Frame (Undo, Redo, Clear, Export) ---
        actions_frame = ttk.LabelFrame(toolbar, text="Canvas Actions", padding=(6, 4))
        actions_frame.pack(side=tk.LEFT, padx=6)

        self.undo_btn = ttk.Button(actions_frame, text="↩ Undo", width=7, command=self._action_undo)
        self.undo_btn.pack(side=tk.LEFT, padx=2)

        self.redo_btn = ttk.Button(actions_frame, text="↪ Redo", width=7, command=self._action_redo)
        self.redo_btn.pack(side=tk.LEFT, padx=2)

        self.clear_btn = ttk.Button(actions_frame, text="🗑 Clear", width=7, command=self._action_clear)
        self.clear_btn.pack(side=tk.LEFT, padx=2)

        self.save_btn = ttk.Button(actions_frame, text="💾 Save", width=7, command=self._save_state_dialog)
        self.save_btn.pack(side=tk.LEFT, padx=2)

        self.export_btn = ttk.Button(actions_frame, text="🖼 Export", width=7, command=self._export_image_dialog)
        self.export_btn.pack(side=tk.LEFT, padx=2)

    def _build_main_workspace(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Left / Center: Interactive Canvas View
        canvas_container = ttk.Frame(paned)
        paned.add(canvas_container, weight=4)

        self.canvas_view = CanvasView(
            canvas_container,
            width=config.DEFAULT_CANVAS_WIDTH,
            height=config.DEFAULT_CANVAS_HEIGHT,
            on_segment_drawn=self._on_canvas_segment_drawn,
            on_action_committed=self._on_canvas_action_committed,
        )
        self.canvas_view.pack(fill=tk.BOTH, expand=True)

        # Right: Sidebar (Participants & Chat)
        sidebar_container = ttk.Frame(paned, width=320)
        paned.add(sidebar_container, weight=1)

        self.chat_panel = ChatPanel(
            sidebar_container,
            on_send_chat=self._on_send_chat_message,
        )
        self.chat_panel.pack(fill=tk.BOTH, expand=True)

    def _build_status_bar(self) -> None:
        self.status_bar = ttk.Frame(self, relief=tk.SUNKEN, padding=(6, 2))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.conn_label = ttk.Label(
            self.status_bar,
            text="🔴 Disconnected",
            foreground="#C0392B",
            font=("Segoe UI", 9, "bold"),
        )
        self.conn_label.pack(side=tk.LEFT, padx=6)

        sep1 = ttk.Separator(self.status_bar, orient=tk.VERTICAL)
        sep1.pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.user_label = ttk.Label(self.status_bar, text="User: Offline", font=("Segoe UI", 9))
        self.user_label.pack(side=tk.LEFT, padx=6)

        sep2 = ttk.Separator(self.status_bar, orient=tk.VERTICAL)
        sep2.pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.tool_status_label = ttk.Label(self.status_bar, text="Tool: Pen | Width: 3px", font=("Segoe UI", 9))
        self.tool_status_label.pack(side=tk.LEFT, padx=6)

        sep3 = ttk.Separator(self.status_bar, orient=tk.VERTICAL)
        sep3.pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.room_status_label = ttk.Label(self.status_bar, text="Room: MAIN | Seq: 0", font=("Segoe UI", 9))
        self.room_status_label.pack(side=tk.LEFT, padx=6)

        self.action_counter_label = ttk.Label(self.status_bar, text="Actions: 0", font=("Segoe UI", 9))
        self.action_counter_label.pack(side=tk.RIGHT, padx=6)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-z>", lambda e: self._action_undo())
        self.bind("<Control-y>", lambda e: self._action_redo())
        self.bind("<Control-s>", lambda e: self._save_state_dialog())
        self.bind("<Control-e>", lambda e: self._export_image_dialog())
        self.bind("<Control-Shift-C>", lambda e: self._action_clear())

    # =========================================================================
    # Toolbar & Canvas Interactions
    # =========================================================================

    def _select_tool(self, tool: Tool) -> None:
        self.current_tool = tool
        self.canvas_view.set_tool(tool)
        self.tool_status_label.config(
            text=f"Tool: {tool.value.capitalize()} | Width: {self.canvas_view.stroke_width}px"
        )

    def _on_color_changed(self, hex_color: str) -> None:
        self.canvas_view.set_color(hex_color)

    def _on_width_changed(self, width: int) -> None:
        self.canvas_view.set_width(width)
        self.tool_status_label.config(
            text=f"Tool: {self.current_tool.value.capitalize()} | Width: {width}px"
        )

    def _on_canvas_segment_drawn(
        self, stroke_id: str, x1: float, y1: float, x2: float, y2: float, color: str, width: int
    ) -> None:
        """Invoked live while user is dragging mouse with Pen/Eraser."""
        if self.client.is_connected():
            self.client.send_draw_segment(stroke_id, x1, y1, x2, y2, color, width)

    def _on_canvas_action_committed(self, action: WhiteboardAction) -> None:
        """Invoked when a drawing stroke or geometric shape is completed."""
        action.user_id = self.client.user_id or "local"
        action.username = self.client.username

        # Add to local state
        self.local_state.add_action(action)
        self._update_action_counter()

        # Transmit to server over TCP socket
        if self.client.is_connected():
            if action.action_type == ActionType.PEN.value:
                self.client.send_stroke(action)
            elif action.action_type == ActionType.ERASER.value:
                self.client.send_erase(action)
            else:
                self.client.send_shape(action)

    def _update_action_counter(self) -> None:
        count = len(self.local_state.get_active_actions())
        self.action_counter_label.config(text=f"Actions: {count}")

    # =========================================================================
    # Undo / Redo / Clear Handlers
    # =========================================================================

    def _action_undo(self) -> None:
        if self.client.is_connected():
            self.client.send_undo()
        else:
            # Local-only undo
            undone = self.local_state.undo_action(user_id="local")
            if undone:
                self.canvas_view.redraw_all(self.local_state.get_all_actions())
                self._update_action_counter()

    def _action_redo(self) -> None:
        if self.client.is_connected():
            self.client.send_redo()
        else:
            # Local-only redo
            redone = self.local_state.redo_action(user_id="local")
            if redone:
                self.canvas_view.redraw_all(self.local_state.get_all_actions())
                self._update_action_counter()

    def _action_clear(self) -> None:
        confirm = messagebox.askyesno(
            "Clear Whiteboard",
            "Are you sure you want to clear the entire whiteboard for all participants?",
            parent=self,
        )
        if not confirm:
            return

        if self.client.is_connected():
            self.client.send_clear()
        else:
            self.local_state.clear(user_id="local", username="You")
            self.canvas_view.clear()
            self._update_action_counter()

    # =========================================================================
    # Group Chat Handlers
    # =========================================================================

    def _on_send_chat_message(self, text: str) -> None:
        if self.client.is_connected():
            self.client.send_chat(text)
        else:
            self.chat_panel.append_chat_message(
                self.client.username, text, time.strftime("%H:%M:%S"), color="#2C3E50"
            )

    # =========================================================================
    # Network Event Loop & Inbound Queue Processing
    # =========================================================================

    def _poll_network_events(self) -> None:
        """
        Periodically drains the client network inbound queue on Tkinter's main UI thread.
        Guarantees thread-safe GUI updates without race conditions or deadlocks.
        """
        while not self.client.inbound_queue.empty():
            try:
                msg: ProtocolMessage = self.client.inbound_queue.get_nowait()
                self._dispatch_inbound_message(msg)
            except Exception as e:
                logger_err = f"Error processing network event: {e}"
                print(logger_err)

        # Reschedule next poll in 15ms (~66 Hz)
        self.after(15, self._poll_network_events)

    def _dispatch_inbound_message(self, msg: ProtocolMessage) -> None:
        msg_type = msg.msg_type
        payload = msg.payload

        if msg_type in (MessageType.DRAW_EVENT.value, "DRAW_EVENT", "DRAW_SEGMENT", "DRAW_STROKE", "SHAPE"):
            is_live = payload.get("is_live", False) or (msg_type == "DRAW_SEGMENT")
            if is_live:
                pts = payload.get("points")
                if pts and len(pts) >= 2:
                    x1, y1 = pts[0][0], pts[0][1]
                    x2, y2 = pts[-1][0], pts[-1][1]
                else:
                    x1 = payload.get("x1", 0.0)
                    y1 = payload.get("y1", 0.0)
                    x2 = payload.get("x2", 0.0)
                    y2 = payload.get("y2", 0.0)
                self.canvas_view.draw_remote_segment(
                    stroke_id=payload.get("stroke_id", ""),
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    color=payload.get("color", "#000000"),
                    width=payload.get("width", 3),
                )
            else:
                action_data = payload.get("action")
                if not action_data:
                    action_data = {
                        "action_id": payload.get("stroke_id"),
                        "action_type": payload.get("shape_type", "pen"),
                        "user_id": msg.sender_id,
                        "color": payload.get("color", "#000000"),
                        "width": payload.get("width", 3),
                        "points": payload.get("points", []),
                        "fill": payload.get("fill"),
                    }
                action = WhiteboardAction.from_dict(action_data)
                self.local_state.add_action(action)
                self.canvas_view.render_action(action)
                self._update_action_counter()

        elif msg_type in (MessageType.ERASE_EVENT.value, "ERASE_EVENT", "ERASE"):
            action_data = payload.get("action")
            if not action_data:
                action_data = {
                    "action_id": payload.get("stroke_id"),
                    "action_type": "eraser",
                    "user_id": msg.sender_id,
                    "color": "#FFFFFF",
                    "width": payload.get("width", 10),
                    "points": payload.get("points", []),
                }
            action = WhiteboardAction.from_dict(action_data)
            self.local_state.add_action(action)
            self.canvas_view.render_action(action)
            self._update_action_counter()

        elif msg_type in (MessageType.CLEAR_CANVAS.value, "CLEAR_CANVAS", "CLEAR"):
            user = payload.get("username", "Someone")
            self.local_state.clear(payload.get("user_id", msg.sender_id), user)
            self.canvas_view.clear()
            self.chat_panel.append_system_notification(f"Whiteboard cleared by {user}")
            self._update_action_counter()

        elif msg_type in (MessageType.UNDO_EVENT.value, "UNDO_EVENT", "UNDO"):
            action_id = payload.get("action_id")
            user_id = payload.get("user_id", msg.sender_id)
            undone = self.local_state.undo_action(user_id=user_id, target_action_id=action_id)
            self.canvas_view.redraw_all(self.local_state.get_all_actions())
            self._update_action_counter()
            if undone:
                self.chat_panel.append_system_notification(f"Undo action by {undone.username or 'user'}")

        elif msg_type in (MessageType.REDO_EVENT.value, "REDO_EVENT", "REDO"):
            action_id = payload.get("action_id")
            user_id = payload.get("user_id", msg.sender_id)
            redone = self.local_state.redo_action(user_id=user_id, target_action_id=action_id)
            self.canvas_view.redraw_all(self.local_state.get_all_actions())
            self._update_action_counter()
            if redone:
                self.chat_panel.append_system_notification(f"Redo action by {redone.username or 'user'}")

        elif msg_type in (MessageType.CHAT_MESSAGE.value, "CHAT_MESSAGE", "CHAT"):
            self.chat_panel.append_chat_message(
                username=payload.get("username", "User"),
                text=payload.get("text", ""),
                timestamp=payload.get("time", time.strftime("%H:%M:%S")),
                color=payload.get("color", "#2C3E50"),
            )

        elif msg_type in (MessageType.PARTICIPANT_JOINED.value, "PARTICIPANT_JOINED", "USER_JOINED"):
            new_user = {
                "user_id": payload.get("user_id", msg.sender_id),
                "username": payload.get("username"),
                "color": payload.get("color"),
            }
            self.active_users.append(new_user)
            self.chat_panel.update_participants(self.active_users)
            self.chat_panel.append_system_notification(f"{new_user['username']} joined the whiteboard")

        elif msg_type in (MessageType.PARTICIPANT_LEFT.value, "PARTICIPANT_LEFT", "USER_LEFT"):
            left_id = payload.get("user_id", msg.sender_id)
            left_name = payload.get("username", "Participant")
            self.active_users = [u for u in self.active_users if u.get("user_id") != left_id]
            self.chat_panel.update_participants(self.active_users)
            self.chat_panel.append_system_notification(f"{left_name} left the whiteboard")

        elif msg_type in (MessageType.CANVAS_STATE.value, "CANVAS_STATE", "SYNC_STATE"):
            actions_raw = payload.get("actions", [])
            self.local_state.load_from_dict_list(actions_raw)
            self.canvas_view.redraw_all(self.local_state.get_all_actions())
            self._update_action_counter()
            chat_hist = payload.get("chat_history", [])
            if chat_hist:
                for chat in chat_hist:
                    self.chat_panel.append_chat_message(
                        username=chat.get("username", "User"),
                        text=chat.get("text", ""),
                        timestamp=chat.get("time", time.strftime("%H:%M:%S")),
                        color=chat.get("color", "#2C3E50"),
                    )
            self.chat_panel.append_system_notification("Canvas state and chat synchronized with server")

        elif msg_type == MessageType.LOAD_STATE.value:
            actions_raw = payload.get("actions", [])
            loaded_by = payload.get("loaded_by", "Peer")
            self.local_state.load_from_dict_list(actions_raw)
            self.canvas_view.redraw_all(self.local_state.get_all_actions())
            self._update_action_counter()
            self.chat_panel.append_system_notification(f"Session replaced from file by {loaded_by}")

        elif msg_type == "DISCONNECTED":
            reason = payload.get("reason", "Connection lost")
            self.conn_label.config(text="🔴 Disconnected", foreground="#C0392B")
            self.chat_panel.append_system_notification(f"Disconnected: {reason}")
            messagebox.showwarning("Connection Lost", f"Disconnected from server:\n{reason}", parent=self)

        # Update sequence number and room display
        self.room_status_label.config(
            text=f"Room: {self.client.room_code} | Seq: {self.client.last_seq_num}"
        )

    # =========================================================================
    # Connection Management
    # =========================================================================

    def _open_connection_dialog(self) -> None:
        ConnectionDialog(
            self,
            default_username=self.client.username,
            default_host="127.0.0.1",
            default_port=config.DEFAULT_SERVER_PORT,
            on_connect=self._do_connect,
        )

    def _do_connect(
        self, username: str, host: str, port: int, room_code: str = "MAIN", passcode: str = ""
    ) -> None:
        self.client.username = username
        self.conn_label.config(text="🟡 Connecting...", foreground="#E67E22")
        self.update_idletasks()

        success, err, users, actions = self.client.connect(
            host=host, port=port, room_code=room_code, passcode=passcode
        )
        if not success:
            self.conn_label.config(text="🔴 Disconnected", foreground="#C0392B")
            messagebox.showerror("Connection Error", f"Failed to connect:\n{err}", parent=self)
            return

        # Connected successfully
        self.title(f"LAN Collaborative Whiteboard - Room: {self.client.room_code}")
        self.conn_label.config(
            text=f"🟢 Connected ({host}:{port})", foreground="#27AE60"
        )
        self.user_label.config(
            text=f"User: {self.client.username} ({self.client.user_id})"
        )
        self.room_status_label.config(
            text=f"Room: {self.client.room_code} | Seq: {self.client.last_seq_num}"
        )

        # Synchronize local state with server state
        self.active_users = users
        self.chat_panel.set_current_user_id(self.client.user_id)
        self.chat_panel.update_participants(users)

        # Replay persistent chat history from room
        if hasattr(self.client, "initial_chat_history") and self.client.initial_chat_history:
            for chat in self.client.initial_chat_history:
                self.chat_panel.append_chat_message(
                    username=chat.get("username", "User"),
                    text=chat.get("text", ""),
                    timestamp=chat.get("time", time.strftime("%H:%M:%S")),
                    color=chat.get("color", "#2C3E50"),
                )

        self.local_state.load_from_dict_list(actions)
        self.canvas_view.redraw_all(self.local_state.get_all_actions())
        self._update_action_counter()

        self.chat_panel.append_system_notification(
            f"Joined room '{self.client.room_code}' on {host}:{port} as {self.client.username}"
        )

    def _action_reconnect(self) -> None:
        """Attempts immediate reconnection and state recovery to previous room."""
        if self.client.is_connected():
            messagebox.showinfo("Status", "Already connected to room.", parent=self)
            return

        self.conn_label.config(text="🟡 Reconnecting...", foreground="#E67E22")
        self.update_idletasks()

        success, err, users, actions = self.client.reconnect()
        if not success:
            self.conn_label.config(text="🔴 Disconnected", foreground="#C0392B")
            messagebox.showerror("Reconnection Failed", f"Could not reconnect:\n{err}", parent=self)
            return

        self.conn_label.config(
            text=f"🟢 Connected ({self.client.server_host}:{self.client.server_port})", foreground="#27AE60"
        )
        self.user_label.config(text=f"User: {self.client.username} ({self.client.user_id})")
        self.room_status_label.config(text=f"Room: {self.client.room_code} | Seq: {self.client.last_seq_num}")

        self.active_users = users
        self.chat_panel.update_participants(users)
        self.local_state.load_from_dict_list(actions)
        self.canvas_view.redraw_all(self.local_state.get_all_actions())
        self._update_action_counter()
        self.chat_panel.append_system_notification("Reconnected successfully! State recovered from server.")

    def _do_disconnect(self) -> None:
        if self.client.is_connected():
            self.client.disconnect()
            self.conn_label.config(text="🔴 Disconnected", foreground="#C0392B")
            self.chat_panel.append_system_notification("Disconnected from server.")

    def _request_sync(self) -> None:
        if self.client.is_connected():
            self.client.request_sync()
        else:
            messagebox.showinfo("Offline", "Client is not connected to a server.", parent=self)

    # =========================================================================
    # State Persistence (Save, Load, Export)
    # =========================================================================

    def _save_state_dialog(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save Whiteboard Session",
            defaultextension=".json",
            filetypes=[("Whiteboard Session State (*.json)", "*.json"), ("All Files", "*.*")],
            parent=self,
        )
        if path:
            try:
                self.local_state.save_to_file(path)
                messagebox.showinfo("Session Saved", f"Session saved successfully to:\n{path}", parent=self)
            except Exception as e:
                messagebox.showerror("Save Error", f"Failed to save session state:\n{e}", parent=self)

    def _load_state_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Load Whiteboard Session",
            filetypes=[("Whiteboard Session State (*.json)", "*.json"), ("All Files", "*.*")],
            parent=self,
        )
        if path:
            try:
                self.local_state.load_from_file(path)
                self.canvas_view.redraw_all(self.local_state.get_all_actions())
                self._update_action_counter()

                # If connected, broadcast loaded state to all peers
                if self.client.is_connected():
                    self.client.send_load_state(self.local_state.to_dict_list())

                messagebox.showinfo("Session Loaded", f"Session loaded successfully from:\n{path}", parent=self)
            except Exception as e:
                messagebox.showerror("Load Error", f"Failed to load session state:\n{e}", parent=self)

    def _export_image_dialog(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export Canvas Image",
            defaultextension=".png",
            filetypes=[("PNG Image (*.png)", "*.png"), ("JPEG Image (*.jpg)", "*.jpg")],
            parent=self,
        )
        if path:
            try:
                success = self.local_state.export_to_image(
                    path, width=config.DEFAULT_CANVAS_WIDTH, height=config.DEFAULT_CANVAS_HEIGHT
                )
                if success:
                    messagebox.showinfo("Export Successful", f"Canvas image exported to:\n{path}", parent=self)
                else:
                    messagebox.showwarning("Pillow Missing", "Pillow library is required for image export.", parent=self)
            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export canvas image:\n{e}", parent=self)

    def _show_network_info(self) -> None:
        lan_ip = config.get_lan_ip()
        info = (
            f"Local LAN IP: {lan_ip}\n"
            f"Client Status: {'Connected' if self.client.is_connected() else 'Disconnected'}\n"
            f"Server Host: {self.client.server_host or 'N/A'}:{self.client.server_port}\n"
            f"Assigned User ID: {self.client.user_id or 'Offline'}\n"
            f"Assigned Color: {self.client.assigned_color}\n"
            f"Socket TCP_NODELAY: Enabled (Immediate coordinate transmission)\n"
            f"Active Canvas Actions: {len(self.local_state.get_active_actions())}\n"
            f"Online Participants: {len(self.active_users)}"
        )
        messagebox.showinfo("Network Diagnostics", info, parent=self)

    def _show_about(self) -> None:
        about_text = (
            "LAN-Based Multi-User Collaborative Whiteboard\n"
            "Computer Networks Laboratory Project\n\n"
            "Features:\n"
            "• Pure Python TCP/IP Socket Programming\n"
            "• Length-Prefixed Stream Framing Protocol\n"
            "• Real-Time Interactive Stroke Synchronization\n"
            "• Geometric Shapes: Line, Rectangle, Circle, Arrow\n"
            "• Eraser & Stroke Width Controls\n"
            "• Collaborative Non-Destructive Undo / Redo\n"
            "• Integrated Group Chat & Presence Roster\n"
            "• UDP Broadcast LAN Auto-Discovery\n"
            "• Session Persistence (JSON) & Image Export (PNG)"
        )
        messagebox.showinfo("About Collaborative Whiteboard", about_text, parent=self)

    def _on_close(self) -> None:
        self._do_disconnect()
        self.destroy()
