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
                rooms = server.room_manager.list_rooms()
                total_clients = sum(r["participants_count"] for r in rooms)
                total_actions = sum(r["action_count"] for r in rooms)
                print(f"[STATUS] Uptime: {uptime}s | Active Rooms: {len(rooms)} | Total Clients: {total_clients} | Total Actions: {total_actions}")

            elif base_cmd == "rooms":
                rooms = server.room_manager.list_rooms()
                print(f"[ROOMS] Total Active Rooms: {len(rooms)}")
                for r in rooms:
                    pass_str = " (Protected)" if r["has_passcode"] else ""
                    print(f"  • Room [{r['room_code']}]{pass_str}: '{r['name']}' | Users: {r['participants_count']} | Actions: {r['action_count']}")

            elif base_cmd == "clients":
                rooms = server.room_manager.list_rooms()
                total_connected = 0
                for r in rooms:
                    room_obj = server.room_manager.get_room(r["room_code"])
                    users = room_obj.get_active_users_list() if room_obj else []
                    if users:
                        total_connected += len(users)
                        print(f"[ROOM {r['room_code']}] Participants ({len(users)}):")
                        for u in users:
                            print(f"  - User: {u['username']} (ID: {u['user_id']}) @ {u['addr']} [Color: {u['color']}]")
                if total_connected == 0:
                    print("[CLIENTS] No active clients currently connected.")

            elif base_cmd == "clear":
                target_room = parts[1].upper() if len(parts) > 1 else "MAIN"
                room = server.room_manager.get_room(target_room)
                if room:
                    clear_action = room.state.clear(user_id="server_admin", username="Server Admin")
                    from protocol.messages import create_clear_msg
                    msg = create_clear_msg("server_admin", "Server Admin")
                    msg.seq_num = room.next_seq_num()
                    msg.room_code = target_room
                    room.broadcast(msg)
                    print(f"[WHITEBOARD] Cleared canvas for room '{target_room}'.")
                else:
                    print(f"[ERROR] Room '{target_room}' not found.")

            elif base_cmd == "save":
                target_room = parts[1].upper() if len(parts) > 1 and not parts[1].endswith(".json") else "MAIN"
                filename = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 and parts[1].endswith(".json") else f"whiteboard_{target_room}_{int(time.time())}.json")
                room = server.room_manager.get_room(target_room)
                if room:
                    room.state.save_to_file(filename)
                    print(f"[STORAGE] Saved whiteboard state for room '{target_room}' to {filename}")
                else:
                    print(f"[ERROR] Room '{target_room}' not found.")

            elif base_cmd == "export":
                target_room = parts[1].upper() if len(parts) > 1 and not parts[1].endswith(".png") else "MAIN"
                filename = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 and parts[1].endswith(".png") else f"whiteboard_{target_room}_{int(time.time())}.png")
                room = server.room_manager.get_room(target_room)
                if room:
                    success = room.state.export_to_image(filename)
                    if success:
                        print(f"[IMAGE] Exported canvas image for room '{target_room}' to {filename}")
                    else:
                        print("[IMAGE] Export failed: Pillow library not installed.")
                else:
                    print(f"[ERROR] Room '{target_room}' not found.")

            elif base_cmd == "help":
                print("Available Console Commands:")
                print("  status                 - Show server uptime, active rooms, client counts")
                print("  rooms                  - List all active whiteboard rooms and session stats")
                print("  clients                - List connected clients grouped by room")
                print("  clear [room]           - Wipe canvas for specified room (default: MAIN)")
                print("  save [room] [file]     - Persist room state to JSON file")
                print("  export [room] [file]   - Render and export room canvas as PNG image")
                print("  quit / exit            - Gracefully shutdown the server")

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
