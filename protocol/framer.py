"""
Low-Level Protocol Message Framer & Network Serialization for TCP Stream Communication.

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
  | 4-Byte Payload Length Header  | Variable Length JSON Payload      |
  | (Network Byte Order: '!I')   | (e.g. JSON-encoded UTF-8 bytes)   |
  +-------------------------------+-----------------------------------+
"""

import json
import socket
import struct
from typing import Any, Dict, Generator, Optional, Tuple, Union

# 4 bytes unsigned int (big-endian / network byte order), max 4 GB payload
HEADER_FORMAT = "!I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 4 bytes
DEFAULT_MAX_PAYLOAD_SIZE = 16 * 1024 * 1024   # 16 MB limit (DoS protection)


class FrameError(Exception):
    """Base exception for framing errors or stream corruptions."""
    pass


class ConnectionClosedError(FrameError):
    """Raised when a socket connection is closed prematurely or gracefully by the peer."""
    pass


class OversizedMessageError(FrameError):
    """Raised when an incoming frame claims a length exceeding allowable limits."""
    pass


class InvalidJSONError(FrameError):
    """Raised when a received frame payload is not valid UTF-8 JSON."""
    pass


# =============================================================================
# Core Framing Primitives
# =============================================================================

def pack_message(payload: bytes) -> bytes:
    """
    Prepends a 4-byte big-endian network byte order length prefix to the message payload.
    Format: '!I'
    """
    length = len(payload)
    header = struct.pack(HEADER_FORMAT, length)
    return header + payload


def read_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """
    Reads exactly `num_bytes` from the socket.
    Handles partial TCP reads by looping until all requested bytes are gathered.
    Raises ConnectionClosedError if the remote host closes the connection (EOF).
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        bytes_needed = num_bytes - len(buffer)
        try:
            chunk = sock.recv(bytes_needed)
        except socket.timeout:
            raise
        except (socket.error, ConnectionResetError, BrokenPipeError) as e:
            raise ConnectionClosedError(f"Socket error during recv: {e}") from e

        if not chunk:
            # Remote peer closed connection (EOF)
            if len(buffer) == 0:
                raise ConnectionClosedError("Remote peer disconnected (EOF).")
            else:
                raise ConnectionClosedError(
                    f"Connection closed prematurely. Expected {num_bytes} bytes, received {len(buffer)}."
                )
        buffer.extend(chunk)
    return bytes(buffer)


def read_framed_message(sock: socket.socket, max_payload_size: int = DEFAULT_MAX_PAYLOAD_SIZE) -> bytes:
    """
    Synchronously reads one complete framed message from the socket:
      1. Reads exactly 4 header bytes to decode payload size using '!I'.
      2. Validates payload size does not exceed `max_payload_size`.
      3. Reads exactly `length` bytes of payload.
    Returns the raw payload bytes.
    """
    # Step 1: Read 4 header bytes
    header_bytes = read_exact(sock, HEADER_SIZE)
    (payload_length,) = struct.unpack(HEADER_FORMAT, header_bytes)

    # Step 2: Validate frame length against denial-of-service / buffer overflow
    if payload_length > max_payload_size:
        raise OversizedMessageError(
            f"Payload size {payload_length} bytes exceeds maximum limit of {max_payload_size} bytes."
        )

    # Step 3: Read exact payload bytes
    if payload_length == 0:
        return b""
    return read_exact(sock, payload_length)


def send_framed_message(sock: socket.socket, payload: bytes) -> None:
    """
    Packs payload with 4-byte length prefix and transmits using socket.sendall().
    Guarantees that all bytes are pushed across the kernel TCP send buffers.
    """
    framed = pack_message(payload)
    try:
        sock.sendall(framed)
    except (socket.error, BrokenPipeError, ConnectionResetError) as e:
        raise ConnectionClosedError(f"Failed to send data: socket closed or broken pipe: {e}") from e


# =============================================================================
# High-Level Reusable Functions (Mandatory Requirement)
# encode_message(), decode_message(), send_message(), receive_message()
# =============================================================================

def encode_message(message: Union[Any, Dict[str, Any]]) -> bytes:
    """
    Encodes an application message object or dictionary into length-prefixed bytes.
    1. Serializes message dictionary into compact UTF-8 JSON bytes.
    2. Prepends 4-byte network-byte-order header: struct.pack('!I', length).
    Returns [4-byte message length][JSON message payload] ready to send over TCP.
    """
    if hasattr(message, "to_dict"):
        data_dict = message.to_dict()
    elif isinstance(message, dict):
        data_dict = message
    else:
        raise TypeError(f"Message must be a ProtocolMessage or dict, got {type(message).__name__}")

    try:
        json_bytes = json.dumps(data_dict, separators=(",", ":")).encode("utf-8")
    except Exception as e:
        raise ValueError(f"Failed to serialize message to JSON: {e}") from e

    return pack_message(json_bytes)


def decode_message(raw_bytes: bytes) -> Any:
    """
    Decodes raw payload bytes into a ProtocolMessage object or dictionary.
    Handles invalid JSON payloads by raising InvalidJSONError.
    """
    try:
        json_str = raw_bytes.decode("utf-8")
        data_dict = json.loads(json_str)
    except UnicodeDecodeError as e:
        raise InvalidJSONError(f"Payload contains invalid UTF-8 bytes: {e}") from e
    except json.JSONDecodeError as e:
        raise InvalidJSONError(f"Malformed JSON payload: {e}") from e

    # Import ProtocolMessage lazily to avoid circular import
    from .messages import ProtocolMessage
    return ProtocolMessage.from_dict(data_dict)


def send_message(sock: socket.socket, message: Union[Any, Dict[str, Any], bytes]) -> None:
    """
    Reusable function to encode and transmit a length-prefixed message over a TCP socket.
    Handles ProtocolMessage objects, dicts, or raw framed bytes.
    """
    if isinstance(message, bytes):
        # Already framed or raw payload
        framed_bytes = message
    else:
        framed_bytes = encode_message(message)

    try:
        sock.sendall(framed_bytes)
    except (socket.error, BrokenPipeError, ConnectionResetError) as e:
        raise ConnectionClosedError(f"Socket connection error during send_message: {e}") from e


def receive_message(sock: socket.socket, max_payload_size: int = DEFAULT_MAX_PAYLOAD_SIZE) -> Any:
    """
    Reusable function to read and decode one complete message from a TCP socket.
    Handles:
      - Partial TCP reads
      - Multiple coalesced packets in stream
      - Disconnected sockets (raises ConnectionClosedError)
      - Invalid JSON (raises InvalidJSONError)
      - Oversized messages (raises OversizedMessageError)
      - Socket timeouts (propagates socket.timeout)
    Returns decoded ProtocolMessage.
    """
    payload_bytes = read_framed_message(sock, max_payload_size=max_payload_size)
    return decode_message(payload_bytes)


# =============================================================================
# Stateful Stream Framer (For Chunked / Asynchronous Ingestion)
# =============================================================================

class StreamFramer:
    """
    Stateful byte-stream framer suitable for buffer accumulation.
    Handles multiple messages arriving coalesced in a single chunk
    as well as partial fragments across multiple chunks.
    """

    def __init__(self, max_payload_size: int = DEFAULT_MAX_PAYLOAD_SIZE):
        self._buffer = bytearray()
        self.max_payload_size = max_payload_size

    def feed(self, chunk: bytes) -> None:
        """Appends incoming raw bytes to internal accumulation buffer."""
        self._buffer.extend(chunk)

    def extract_messages(self) -> Generator[bytes, None, None]:
        """
        Extracts and yields all complete raw payloads present in the buffer.
        Leaves any partial messages in the buffer for subsequent chunks.
        """
        while len(self._buffer) >= HEADER_SIZE:
            (payload_length,) = struct.unpack(HEADER_FORMAT, self._buffer[:HEADER_SIZE])

            if payload_length > self.max_payload_size:
                self._buffer.clear()
                raise OversizedMessageError(
                    f"Payload length {payload_length} exceeds maximum allowable size of {self.max_payload_size}."
                )

            total_frame_size = HEADER_SIZE + payload_length
            if len(self._buffer) < total_frame_size:
                # Incomplete frame; await more bytes
                break

            message_payload = bytes(self._buffer[HEADER_SIZE:total_frame_size])
            del self._buffer[:total_frame_size]
            yield message_payload

    def has_partial_data(self) -> bool:
        return len(self._buffer) > 0
