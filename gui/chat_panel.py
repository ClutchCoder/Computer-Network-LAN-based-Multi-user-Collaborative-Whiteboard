"""
Integrated Group Chat and Connected Participants Panel.
"""

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional


class ChatPanel(ttk.Frame):
    """
    Combined sidebar displaying real-time participant roster and group chat log.
    """

    def __init__(
        self,
        parent: tk.Widget,
        on_send_chat: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.on_send_chat = on_send_chat
        self.my_user_id: Optional[str] = None

        self._build_ui()

    def _build_ui(self) -> None:
        # Create PanedWindow for adjustable participant list vs chat log
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # =================================================================
        # Upper Section: Connected Participants
        # =================================================================
        participants_frame = ttk.LabelFrame(paned, text="Participants (0 online)", padding=6)
        paned.add(participants_frame, weight=1)

        # Participant Listbox with vertical scrollbar
        plist_scroll = ttk.Scrollbar(participants_frame, orient=tk.VERTICAL)
        self.user_listbox = tk.Listbox(
            participants_frame,
            yscrollcommand=plist_scroll.set,
            height=6,
            selectmode=tk.SINGLE,
            font=("Segoe UI", 9),
            bd=1,
            relief=tk.SOLID,
        )
        plist_scroll.config(command=self.user_listbox.yview)

        self.user_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        plist_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.participants_label = participants_frame

        # =================================================================
        # Lower Section: Group Chat
        # =================================================================
        chat_frame = ttk.LabelFrame(paned, text="Integrated Group Chat", padding=6)
        paned.add(chat_frame, weight=3)

        # Chat Log Box
        chat_log_scroll = ttk.Scrollbar(chat_frame, orient=tk.VERTICAL)
        self.chat_text = tk.Text(
            chat_frame,
            yscrollcommand=chat_log_scroll.set,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Segoe UI", 9),
            bd=1,
            relief=tk.SOLID,
            bg="#FAFAFA",
        )
        chat_log_scroll.config(command=self.chat_text.yview)

        self.chat_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        chat_log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Configure style tags for rich text in chat
        self.chat_text.tag_configure("timestamp", foreground="#888888", font=("Segoe UI", 8))
        self.chat_text.tag_configure("system_msg", foreground="#7F8C8D", font=("Segoe UI", 8, "italic"))
        self.chat_text.tag_configure("msg_text", foreground="#2C3E50", font=("Segoe UI", 9))

        # Bottom Input Bar
        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        self.chat_entry = ttk.Entry(input_frame, font=("Segoe UI", 9))
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.chat_entry.bind("<Return>", self._on_send_pressed)

        self.send_btn = ttk.Button(input_frame, text="Send", width=6, command=self._on_send_pressed)
        self.send_btn.pack(side=tk.RIGHT)

    def set_current_user_id(self, user_id: str) -> None:
        self.my_user_id = user_id

    def update_participants(self, users: List[Dict[str, Any]]) -> None:
        """Refreshes the participant listbox and count badge."""
        self.user_listbox.delete(0, tk.END)
        count = len(users)
        self.participants_label.config(text=f"Participants ({count} online)")

        for u in users:
            uid = u.get("user_id")
            uname = u.get("username", "Anonymous")
            color = u.get("color", "#000000")
            is_me = (uid == self.my_user_id)
            is_host = u.get("is_host", False)
            host_badge = " [Host 👑]" if is_host else ""
            display_text = f"● {uname}{host_badge} {'(You)' if is_me else ''}"

            self.user_listbox.insert(tk.END, display_text)
            idx = self.user_listbox.size() - 1
            # Color the bullet point indicator or row
            self.user_listbox.itemconfig(idx, foreground=color)

    def append_chat_message(
        self, username: str, text: str, timestamp: str, color: str = "#2C3E50", is_system: bool = False
    ) -> None:
        """Appends a new formatted message into the chat transcript."""
        self.chat_text.config(state=tk.NORMAL)

        if is_system:
            self.chat_text.insert(tk.END, f"[{timestamp}] ", "timestamp")
            self.chat_text.insert(tk.END, f"*** {text} ***\n", "system_msg")
        else:
            self.chat_text.insert(tk.END, f"[{timestamp}] ", "timestamp")

            # Unique tag for username color
            user_tag = f"user_{username}_{color}"
            self.chat_text.tag_configure(user_tag, foreground=color, font=("Segoe UI", 9, "bold"))

            self.chat_text.insert(tk.END, f"{username}: ", user_tag)
            self.chat_text.insert(tk.END, f"{text}\n", "msg_text")

        self.chat_text.config(state=tk.DISABLED)
        self.chat_text.see(tk.END)

    def append_system_notification(self, text: str) -> None:
        import time
        now_str = time.strftime("%H:%M:%S")
        self.append_chat_message("System", text, now_str, is_system=True)

    def _on_send_pressed(self, event=None) -> None:
        text = self.chat_entry.get().strip()
        if not text:
            return
        if len(text) > 2000:
            text = text[:2000]
        self.chat_entry.delete(0, tk.END)
        if self.on_send_chat:
            self.on_send_chat(text)
