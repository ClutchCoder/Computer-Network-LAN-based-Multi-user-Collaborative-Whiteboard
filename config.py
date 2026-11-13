"""
Configuration parameters for the LAN-Based Multi-User Collaborative Whiteboard.
Centralizes networking, GUI, and protocol settings.
"""

import socket

# --- Network Configuration ---
DEFAULT_SERVER_HOST = "0.0.0.0"       # Bind to all available network interfaces
DEFAULT_SERVER_PORT = 5050          # TCP port for whiteboard coordination and chat
DEFAULT_DISCOVERY_PORT = 5051       # UDP port for LAN server discovery beacon
DISCOVERY_MAGIC_KEY = "LAN_WHITEBOARD_BROADCAST_BEACON_V1"

# --- Socket & Protocol Tuning ---
BUFFER_SIZE = 4096                  # Read chunk size for socket.recv()
HEADER_SIZE = 4                     # 4-byte unsigned integer for length-prefixed framing ('!I')
SOCKET_TIMEOUT = 30.0               # Inactivity timeout for client sockets (seconds)
HEARTBEAT_INTERVAL = 10.0           # Interval to send keep-alive pings (seconds)
HEARTBEAT_TIMEOUT = 25.0            # Time without pong before declaring client dead (seconds)
MAX_CLIENT_QUEUE_SIZE = 1000        # Per-client outgoing message queue capacity

# --- TCP Socket Options ---
# TCP_NODELAY disables Nagle's algorithm. For real-time drawing, we must transmit
# mouse coordinates immediately without waiting for TCP ACK or packet accumulation.
ENABLE_TCP_NODELAY = True

# SO_REUSEADDR allows immediate restart of the server on the same port without
# waiting for TIME_WAIT socket states to expire.
ENABLE_SO_REUSEADDR = True

# --- Chat Configuration ---
MAX_CHAT_LENGTH = 2000              # Maximum character length for a single chat message
MAX_CHAT_HISTORY = 500              # Maximum persistent chat messages stored per room

# --- Canvas Defaults ---
DEFAULT_CANVAS_WIDTH = 1280
DEFAULT_CANVAS_HEIGHT = 800
DEFAULT_STROKE_COLOR = "#000000"
DEFAULT_STROKE_WIDTH = 3
DEFAULT_BG_COLOR = "#FFFFFF"

# Throttle interval for freehand drawing coordinates (seconds)
# Prevents sending hundreds of raw mouse move events per second while maintaining high fluidity
MOUSE_THROTTLE_INTERVAL = 0.015     # ~66 updates/second max

# --- Color Palette Options ---
PRESET_COLORS = [
    "#000000",  # Black
    "#E74C3C",  # Red
    "#3498DB",  # Blue
    "#2ECC71",  # Green
    "#F39C12",  # Orange
    "#9B59B6",  # Purple
    "#1ABC9C",  # Teal
    "#E67E22",  # Dark Orange
    "#34495E",  # Slate Gray
    "#E91E63",  # Deep Pink
    "#795548",  # Brown
    "#FFFFFF",  # White / Eraser
]

def get_lan_ip() -> str:
    """
    Determines the primary LAN IP address of this machine.
    Uses a dummy UDP socket connect to avoid hardcoding or resolving hostname localhost.
    Does not actually send any network packets.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connecting to a non-routable public IP queries the OS routing table for the outbound interface IP
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip
