"""
LAN-Based Multi-User Collaborative Whiteboard - Server Executable.

Starts the central authoritative TCP Whiteboard & Chat Server with
background UDP LAN Auto-Discovery beacon and interactive console monitoring.
"""

import argparse
import logging
import os
import sys
import threading
import time

import config
from network.tcp_server import WhiteboardServer


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S")


def print_banner(server: WhiteboardServer) -> None:
    lan_ip = config.get_lan_ip()
    border = "=" * 70
    print(border)
    print("    LAN-BASED MULTI-USER COLLABORATIVE WHITEBOARD SERVER")
    print("               Computer Networks Project")
    print(border)
    print(f"  • Listening Address : {server.host}:{server.port}")
    print(f"  • Primary LAN IP    : {lan_ip}")
    print(f"  • UDP Discovery     : Port {config.DEFAULT_DISCOVERY_PORT} (Beacon Active)")
    print(f"  • Socket Tuning     : TCP_NODELAY (Nagle Disabled), SO_REUSEADDR")
    print(f"  • Concurrency       : Multi-Threaded (Thread-per-Client)")
    print(border)
    print("  Clients on your LAN (Wi-Fi or Ethernet) can connect using:")
    print(f"  Host: {lan_ip}   Port: {server.port}")
    print(border)
    print("  Type 'help' for console commands, or press Ctrl+C to stop.\n")


def console_loop(server: WhiteboardServer) -> None:
    """Provides an interactive CLI management console for the running server."""
    while server.is_running():
        try:
            cmd = input().strip()
            if not cmd:
                continue

            parts = cmd.split()
            base_cmd = parts[0].lower()

            if base_cmd in ("quit", "exit", "stop"):
                print("Stopping server...")
                server.stop()
                break

            elif base_cmd == "status":
                uptime = int(time.time() - server.start_time)
                active_users = server.get_active_users_list()
                actions = server.state.get_active_actions()
                print(f"[STATUS] Uptime: {uptime}s | Active Clients: {len(active_users)} | Active Actions: {len(actions)}")

            elif base_cmd == "clients":
                users = server.get_active_users_list()
                if not users:
                    print("[CLIENTS] No active clients connected.")
                else:
                    print(f"[CLIENTS] Total Connected: {len(users)}")
                    for u in users:
                        print(f"  - User: {u['username']} (ID: {u['user_id']}) @ {u['addr']} [Color: {u['color']}]")

            elif base_cmd == "clear":
                clear_action = server.state.clear(user_id="server_admin", username="Server Admin")
                from protocol.messages import create_clear_msg
                server.broadcast(create_clear_msg("server_admin", "Server Admin"))
                print("[WHITEBOARD] Cleared canvas for all clients.")

            elif base_cmd == "save":
                filename = parts[1] if len(parts) > 1 else f"whiteboard_backup_{int(time.time())}.json"
                server.state.save_to_file(filename)
                print(f"[STORAGE] Saved whiteboard state to {filename}")

            elif base_cmd == "export":
                filename = parts[1] if len(parts) > 1 else f"whiteboard_render_{int(time.time())}.png"
                success = server.state.export_to_image(filename)
                if success:
                    print(f"[IMAGE] Exported canvas image to {filename}")
                else:
                    print("[IMAGE] Export failed: Pillow library not installed.")

            elif base_cmd == "help":
                print("Available Console Commands:")
                print("  status        - Show server uptime, connected clients, action count")
                print("  clients       - List connected client IP addresses and usernames")
                print("  clear         - Wipe the whiteboard canvas for all connected clients")
                print("  save [file]   - Persist current whiteboard state to JSON file")
                print("  export [file] - Render and export current canvas as PNG image")
                print("  quit / exit   - Gracefully shutdown the server")

            else:
                print(f"Unknown command: '{cmd}'. Type 'help' for available commands.")

        except (EOFError, KeyboardInterrupt):
            print("\nShutting down server...")
            server.stop()
            break
        except Exception as e:
            print(f"Error handling console command: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN Collaborative Whiteboard TCP Server")
    parser.add_argument("--host", default=config.DEFAULT_SERVER_HOST, help="Host/Interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=config.DEFAULT_SERVER_PORT, help="TCP port to listen on (default: 5050)")
    parser.add_argument("--no-discovery", action="store_true", help="Disable UDP LAN auto-discovery beacon")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    server = WhiteboardServer(
        host=args.host,
        port=args.port,
        enable_discovery=not args.no_discovery,
    )

    try:
        server.start()
        print_banner(server)
        console_loop(server)
    except KeyboardInterrupt:
        print("\nShutdown signal received.")
    except Exception as e:
        print(f"\n[FATAL] Server failed to start: {e}")
        sys.exit(1)
    finally:
        server.stop()
        print("Server shutdown complete.")


if __name__ == "__main__":
    main()
