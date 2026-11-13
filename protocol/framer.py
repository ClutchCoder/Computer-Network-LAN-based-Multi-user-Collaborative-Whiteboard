"""
Low-Level Protocol Message Framer for TCP Stream Communication.

Computer Networks Principle:
TCP (Transmission Control Protocol) is a connection-oriented, reliable BYTE-STREAM protocol.
TCP has NO concept of message boundaries. When an application calls send() twice:
  send("HELLO")
  send("WORLD")
The receiving TCP stack may deliver this as:
  1. One combined chunk: "HELLOWORLD" (Coalescing / Sticky Packets)
  2. Multiple fragments: "HEL", "LOWO", "RLD" (Segmentation / Fragmentation)
  3. Exactly: "HELLO", then "WORLD"

To preserve application-layer message boundaries over TCP, we implement
a Length-Prefixed Framing Protocol:
  +-------------------------------+-----------------------------------+
  | 4-Byte Payload Length Header  | Variable Length Payload (Payload) |
  | (Network Byte Order: '!I')   | (e.g. JSON-encoded UTF-8 bytes)   |
  +-------------------------------+-----------------------------------+
"""

import struct
import socket
from typing import Optional, List, Generator

# 4 bytes unsigned int (big-endian / network byte order), max 4 GB payload
HEADER_FORMAT = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 4 bytes


class FrameError(Exception):
    """Raised when framing errors or stream corruptions occur."""
    pass


class ConnectionClosedError(Exception):
    """Raised when a socket connection is closed prematurely by the peer."""
    pass


def pack_message(payload: bytes) -> bytes:
    """
    Prepends a 4-byte big-endian length prefix to the message payload.
    """
    length = len(payload)
    header = struct.pack(HEADER_FORMAT, length)
    return header + payload


def send_framed_message(sock: socket.socket, payload: bytes) -> None:
    """
    Packs payload with length prefix and transmits the entire buffer
    using socket.sendall() to guarantee complete delivery across TCP buffers.
    """
    framed = pack_message(payload)
    try:
        sock.sendall(framed)
    except (socket.error, BrokenPipeError, ConnectionResetError) as e:
        raise ConnectionClosedError(f"Failed to send data: socket closed or broken pipe: {e}") from e


def read_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """
    Reads exactly `num_bytes` from the socket.
    Handles partial recv() reads by looping until all requested bytes are gathered.
    Raises ConnectionClosedError if the remote host closes the connection (EOF).
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        bytes_needed = num_bytes - len(buffer)
        try:
            chunk = sock.recv(bytes_needed)
        except socket.timeout:
            raise
        except (socket.error, ConnectionResetError) as e:
            raise ConnectionClosedError(f"Socket error while reading: {e}") from e

        if not chunk:
            # EOF reached (graceful or ungraceful shutdown by peer)
            if len(buffer) == 0:
                raise ConnectionClosedError("Remote peer disconnected (EOF).")
            else:
                raise ConnectionClosedError(
                    f"Connection closed prematurely. Expected {num_bytes} bytes, received {len(buffer)}."
                )
        buffer.extend(chunk)
    return bytes(buffer)


def read_framed_message(sock: socket.socket, max_payload_size: int = 16 * 1024 * 1024) -> bytes:
    """
    Synchronously reads one complete framed message from the socket:
      1. Reads exactly 4 header bytes to decode payload size.
      2. Validates payload size does not exceed `max_payload_size` (DDoS/Buffer Overflow protection).
      3. Reads exactly `length` bytes of payload.
    Returns the raw payload bytes.
    """
    # Step 1: Read the 4-byte header
    header_bytes = read_exact(sock, HEADER_SIZE)
    (payload_length,) = struct.unpack(HEADER_FORMAT, header_bytes)

    # Step 2: Validate frame length
    if payload_length > max_payload_size:
        raise FrameError(f"Payload size {payload_length} exceeds limit {max_payload_size}")

    # Step 3: Read exact payload bytes
    if payload_length == 0:
        return b""
    return read_exact(sock, payload_length)


class StreamFramer:
    """
    Stateful byte-stream framer suitable for asynchronous or chunk-based buffers.
    Feed incoming byte chunks using feed(chunk) and call extract_messages()
    to yield any fully accumulated messages.
    """

    def __init__(self, max_payload_size: int = 16 * 1024 * 1024):
        self._buffer = bytearray()
        self.max_payload_size = max_payload_size

    def feed(self, chunk: bytes) -> None:
        """Appends new raw bytes to the internal accumulation buffer."""
        self._buffer.extend(chunk)

    def extract_messages(self) -> Generator[bytes, None, None]:
        """
        Extracts and yields all complete messages present in the buffer.
        Leaves any partial messages in the buffer for subsequent chunks.
        """
        while len(self._buffer) >= HEADER_SIZE:
            # Peek at header
            (payload_length,) = struct.unpack(HEADER_FORMAT, self._buffer[:HEADER_SIZE])

            if payload_length > self.max_payload_size:
                # Reset buffer on malformed/abusive frame
                self._buffer.clear()
                raise FrameError(f"Payload length {payload_length} exceeds maximum allowable size.")

            total_frame_size = HEADER_SIZE + payload_length
            if len(self._buffer) < total_frame_size:
                # Need more bytes to complete this message
                break

            # Complete message available
            message_payload = bytes(self._buffer[HEADER_SIZE:total_frame_size])
            # Shift buffer
            del self._buffer[:total_frame_size]
            yield message_payload

    def has_partial_data(self) -> bool:
        """Returns True if there is incomplete data pending in the buffer."""
        return len(self._buffer) > 0
