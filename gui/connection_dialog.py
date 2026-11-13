"""
Connection Dialog & LAN Server Discovery Window with Room Management.
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

import config
from network.discovery import discover_lan_servers


class ConnectionDialog(tk.Toplevel):
    """
    Modal dialog prompting user for Username, Server Host IP, Port, Room Code,
    and optional Room Passcode, with built-in UDP LAN auto-discovery.
    """

    def __init__(
        self,
        parent: tk.Widget,
        default_username: str = "User",
        default_host: str = "127.0.0.1",
        default_port: int = config.DEFAULT_SERVER_PORT,
        default_room_code: str = "MAIN",
        default_passcode: str = "",
        on_connect: Optional[Callable[..., None]] = None,
    ):
        super().__init__(parent)
        self.title("Connect to LAN Whiteboard")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.on_connect = on_connect
        self.result = None

        self.default_username = default_username
        self.default_host = default_host
        self.default_port = default_port
        self.default_room_code = default_room_code
        self.default_passcode = default_passcode

        self._build_ui()
        self._center_window()

    def _center_window(self) -> None:
        self.update_idletasks()
        width = 480
        height = 430
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self, padding=16)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title / Header
        header_label = ttk.Label(
            main_frame,
            text="LAN Whiteboard Connection",
            font=("Segoe UI", 12, "bold"),
        )
        header_label.pack(anchor=tk.W, pady=(0, 4))

        sub_label = ttk.Label(
            main_frame,
            text="Connect to a server session or choose an isolated collaborative room.",
            font=("Segoe UI", 8),
            foreground="#555555",
            wraplength=440,
        )
        sub_label.pack(anchor=tk.W, pady=(0, 10))

        # Form Grid
        grid_frame = ttk.Frame(main_frame)
        grid_frame.pack(fill=tk.X, pady=(0, 8))

        # Username
        ttk.Label(grid_frame, text="Username:", font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=3
        )
        self.username_var = tk.StringVar(value=self.default_username)
        self.username_entry = ttk.Entry(grid_frame, textvariable=self.username_var, width=32)
        self.username_entry.grid(row=0, column=1, sticky=tk.W, pady=3, padx=(8, 0))

        # Server IP
        ttk.Label(grid_frame, text="Server IP:", font=("Segoe UI", 9, "bold")).grid(
            row=1, column=0, sticky=tk.W, pady=3
        )
        self.host_var = tk.StringVar(value=self.default_host)
        self.host_entry = ttk.Entry(grid_frame, textvariable=self.host_var, width=32)
        self.host_entry.grid(row=1, column=1, sticky=tk.W, pady=3, padx=(8, 0))

        # Port
        ttk.Label(grid_frame, text="Port:", font=("Segoe UI", 9, "bold")).grid(
            row=2, column=0, sticky=tk.W, pady=3
        )
        self.port_var = tk.IntVar(value=self.default_port)
        self.port_entry = ttk.Entry(grid_frame, textvariable=self.port_var, width=12)
        self.port_entry.grid(row=2, column=1, sticky=tk.W, pady=3, padx=(8, 0))

        # Room Code
        ttk.Label(grid_frame, text="Room Code:", font=("Segoe UI", 9, "bold")).grid(
            row=3, column=0, sticky=tk.W, pady=3
        )
        room_subframe = ttk.Frame(grid_frame)
        room_subframe.grid(row=3, column=1, sticky=tk.W, pady=3, padx=(8, 0))

        self.room_var = tk.StringVar(value=self.default_room_code)
        self.room_entry = ttk.Entry(room_subframe, textvariable=self.room_var, width=16)
        self.room_entry.pack(side=tk.LEFT)

        self.gen_room_btn = ttk.Button(
            room_subframe, text="🎲 New Code", width=11, command=self._generate_random_room_code
        )
        self.gen_room_btn.pack(side=tk.LEFT, padx=(6, 0))

        # Room Passcode (Optional)
        ttk.Label(grid_frame, text="Passcode (Opt):", font=("Segoe UI", 9)).grid(
            row=4, column=0, sticky=tk.W, pady=3
        )
        self.passcode_var = tk.StringVar(value=self.default_passcode)
        self.passcode_entry = ttk.Entry(grid_frame, textvariable=self.passcode_var, width=16, show="*")
        self.passcode_entry.grid(row=4, column=1, sticky=tk.W, pady=3, padx=(8, 0))

        # LAN Auto-Discovery Section
        disc_frame = ttk.LabelFrame(main_frame, text="LAN Auto-Discovery (UDP Broadcast)", padding=8)
        disc_frame.pack(fill=tk.X, pady=(0, 10))

        disc_top = ttk.Frame(disc_frame)
        disc_top.pack(fill=tk.X)

        self.scan_btn = ttk.Button(disc_top, text="🔍 Scan LAN for Servers", command=self._start_scan)
        self.scan_btn.pack(side=tk.LEFT)

        self.scan_status = ttk.Label(disc_top, text="Idle", foreground="#666666", font=("Segoe UI", 8))
        self.scan_status.pack(side=tk.LEFT, padx=10)

        # Discovered servers combobox
        self.discovered_servers = []
        self.combo_var = tk.StringVar(value="-- Discovered Servers --")
        self.server_combo = ttk.Combobox(
            disc_frame,
            textvariable=self.combo_var,
            state="readonly",
            font=("Segoe UI", 9),
        )
        self.server_combo.pack(fill=tk.X, pady=(6, 0))
        self.server_combo.bind("<<ComboboxSelected>>", self._on_server_selected)

        # Buttons Bar
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.connect_btn = ttk.Button(
            btn_frame, text="Connect", style="Accent.TButton", command=self._do_connect
        )
        self.connect_btn.pack(side=tk.RIGHT, padx=(6, 0))

        self.cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self.destroy)
        self.cancel_btn.pack(side=tk.RIGHT)

        # Focus username
        self.username_entry.focus_set()

    def _generate_random_room_code(self) -> None:
        """Generates a random unique alphanumeric room code for new session creation."""
        import random
        import string
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        self.room_var.set(f"ROOM_{suffix}")

    def _start_scan(self) -> None:
        self.scan_btn.config(state=tk.DISABLED)
        self.scan_status.config(text="Scanning subnet via UDP...", foreground="#2980B9")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            results = discover_lan_servers(timeout=2.0)
            self.after(0, lambda: self._scan_finished(results))
        except Exception as e:
            self.after(0, lambda: self._scan_error(str(e)))

    def _scan_finished(self, results: list) -> None:
        self.scan_btn.config(state=tk.NORMAL)
        self.discovered_servers = results
        if not results:
            self.scan_status.config(text="No servers found on LAN.", foreground="#C0392B")
            self.server_combo["values"] = ["No servers detected"]
            self.combo_var.set("No servers detected")
        else:
            self.scan_status.config(text=f"Found {len(results)} server(s)!", foreground="#27AE60")
            options = [f"{s['server_name']} ({s['lan_ip']}:{s['port']})" for s in results]
            self.server_combo["values"] = options
            self.server_combo.current(0)
            self._on_server_selected()

    def _scan_error(self, err: str) -> None:
        self.scan_btn.config(state=tk.NORMAL)
        self.scan_status.config(text=f"Scan error: {err}", foreground="#C0392B")

    def _on_server_selected(self, event=None) -> None:
        idx = self.server_combo.current()
        if 0 <= idx < len(self.discovered_servers):
            target = self.discovered_servers[idx]
            self.host_var.set(target["lan_ip"])
            self.port_var.set(target["port"])

    def _do_connect(self) -> None:
        uname = self.username_var.get().strip()
        host = self.host_var.get().strip()
        port = self.port_var.get()
        room = self.room_var.get().strip().upper() or "MAIN"
        passcode = self.passcode_var.get()

        if not uname:
            messagebox.showerror("Validation Error", "Username cannot be empty.", parent=self)
            return
        if not host:
            messagebox.showerror("Validation Error", "Server IP cannot be empty.", parent=self)
            return

        self.result = (uname, host, port, room, passcode)
        if self.on_connect:
            try:
                self.on_connect(uname, host, port, room, passcode)
            except TypeError:
                # Backward compatibility if 3 args expected
                self.on_connect(uname, host, port)
        self.destroy()
