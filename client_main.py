"""
LAN-Based Multi-User Collaborative Whiteboard - Client Executable.

Launches the Tkinter GUI client application for connecting to a LAN Whiteboard server.
"""

import argparse
import sys

import config
from gui.main_window import MainWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN Collaborative Whiteboard Client")
    parser.add_argument("--username", default="Student", help="Display name for whiteboard and chat")
    parser.add_argument("--host", default="127.0.0.1", help="Target server LAN IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=config.DEFAULT_SERVER_PORT, help="Target server port (default: 5050)")
    parser.add_argument("--auto-connect", action="store_true", help="Automatically connect to target server on launch")
    args = parser.parse_args()

    app = MainWindow(
        username=args.username,
        auto_connect=args.auto_connect,
        host=args.host,
        port=args.port,
    )
    app.mainloop()


if __name__ == "__main__":
    main()
