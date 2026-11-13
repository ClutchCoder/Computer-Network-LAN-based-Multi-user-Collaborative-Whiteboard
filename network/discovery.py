"""
LAN Server Auto-Discovery using UDP Broadcast.

Computer Networks Principle:
Unlike TCP which is connection-oriented and unicast (one-to-one),
UDP (User Datagram Protocol) supports BROADCAST transmissions on local subnets
using the special IP address 255.255.255.255 or the subnet broadcast address.

This module implements a zero-configuration discovery beacon:
  1. The TCP Whiteboard Server periodically broadcasts a beacon datagram.
  2. Clients listening on the broadcast port detect running servers without
     requiring manual IP configuration by end-users.
"""

import json
import logging
import socket
import threading
import time
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("Discovery")


class DiscoveryBeaconServer:
    """
    Background worker that broadcasts the server's presence over UDP.
    """

    def __init__(self, tcp_port: int, server_name: str = "LAN Whiteboard Server", broadcast_port: int = config.DEFAULT_DISCOVERY_PORT):
        self.tcp_port = tcp_port
        self.server_name = server_name
        self.broadcast_port = broadcast_port
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_beacon, daemon=True, name="UDP-Discovery-Beacon")
        self._thread.start()
        logger.info(f"UDP Discovery Beacon broadcasting on port {self.broadcast_port}")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run_beacon(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        lan_ip = config.get_lan_ip()
        beacon_payload = {
            "magic": config.DISCOVERY_MAGIC_KEY,
            "server_name": self.server_name,
            "lan_ip": lan_ip,
            "port": self.tcp_port,
        }
        encoded_data = json.dumps(beacon_payload).encode("utf-8")

        while self._running:
            try:
                # Broadcast to subnet
                sock.sendto(encoded_data, ("<broadcast>", self.broadcast_port))
            except Exception as e:
                logger.debug(f"Broadcast beacon send warning: {e}")
            time.sleep(2.0)

        sock.close()


def discover_lan_servers(timeout: float = 3.0, broadcast_port: int = config.DEFAULT_DISCOVERY_PORT) -> List[Dict[str, Any]]:
    """
    Listens for UDP discovery beacons on the local network for `timeout` seconds.
    Returns a deduplicated list of discovered servers:
      [{ "server_name": ..., "lan_ip": ..., "port": ... }]
    """
    servers: Dict[str, Dict[str, Any]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_BROADCAST"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", broadcast_port))
        sock.settimeout(0.5)

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(2048)
                message = json.loads(data.decode("utf-8"))
                if message.get("magic") == config.DISCOVERY_MAGIC_KEY:
                    server_ip = message.get("lan_ip") or addr[0]
                    # If server reported 0.0.0.0 or 127.0.0.1, prefer actual sender address
                    if server_ip in ("0.0.0.0", "127.0.0.1"):
                        server_ip = addr[0]

                    key = f"{server_ip}:{message.get('port')}"
                    servers[key] = {
                        "server_name": message.get("server_name", "Whiteboard Server"),
                        "lan_ip": server_ip,
                        "port": message.get("port", config.DEFAULT_SERVER_PORT),
                    }
            except socket.timeout:
                continue
            except Exception:
                continue
    finally:
        sock.close()

    return list(servers.values())
