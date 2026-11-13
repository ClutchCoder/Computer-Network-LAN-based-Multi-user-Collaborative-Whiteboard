"""
Unit Tests for Low-Level TCP Stream Framing, Serialization, and Protocol Compliance.
"""

import socket
import struct
import unittest

from protocol.framer import (
    HEADER_FORMAT,
    HEADER_SIZE,
    FrameError,
    InvalidJSONError,
    OversizedMessageError,
    StreamFramer,
    decode_message,
    encode_message,
    pack_message,
    receive_message,
    send_message,
)
from protocol.messages import (
    MessageType,
    ProtocolMessage,
    create_chat_message_msg,
    create_draw_event_msg,
    create_join_room_msg,
    deserialize_message,
    serialize_message,
)


class TestStreamFraming(unittest.TestCase):
    """
    Validates stream framing against TCP fragmentation and packet coalescing.
    """

    def test_pack_message_header(self):
        payload = b"Hello Whiteboard"
        framed = pack_message(payload)
        self.assertEqual(len(framed), HEADER_SIZE + len(payload))
        expected_len = struct.unpack(HEADER_FORMAT, framed[:HEADER_SIZE])[0]
        self.assertEqual(expected_len, len(payload))
        self.assertEqual(framed[HEADER_SIZE:], payload)

    def test_single_message_extraction(self):
        framer = StreamFramer()
        payload = b"Test Payload 123"
        framed = pack_message(payload)

        framer.feed(framed)
        extracted = list(framer.extract_messages())

        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0], payload)
        self.assertFalse(framer.has_partial_data())

    def test_packet_coalescing_sticky_packets(self):
        """
        Simulates TCP packet coalescing: multiple messages delivered in a single recv() buffer.
        """
        framer = StreamFramer()
        msg1 = pack_message(b"Message Alpha")
        msg2 = pack_message(b"Message Beta")
        msg3 = pack_message(b"Message Gamma")

        # Deliver all 3 messages coalesced in one chunk
        framer.feed(msg1 + msg2 + msg3)
        extracted = list(framer.extract_messages())

        self.assertEqual(len(extracted), 3)
        self.assertEqual(extracted[0], b"Message Alpha")
        self.assertEqual(extracted[1], b"Message Beta")
        self.assertEqual(extracted[2], b"Message Gamma")
        self.assertFalse(framer.has_partial_data())

    def test_tcp_fragmentation_byte_by_byte(self):
        """
        Simulates severe TCP fragmentation: message is delivered one single byte at a time.
        """
        framer = StreamFramer()
        payload = b"Fragmented Packet Test Coordinates X:100 Y:200"
        framed = pack_message(payload)

        collected = []
        for byte_idx in range(len(framed)):
            single_byte = framed[byte_idx: byte_idx + 1]
            framer.feed(single_byte)
            msgs = list(framer.extract_messages())
            collected.extend(msgs)

        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0], payload)
        self.assertFalse(framer.has_partial_data())

    def test_oversized_payload_protection(self):
        """Validates that excessively large payload headers trigger OversizedMessageError."""
        framer = StreamFramer(max_payload_size=1000)
        # Construct header claiming 10,000 bytes
        malicious_header = struct.pack(HEADER_FORMAT, 10000)
        framer.feed(malicious_header)

        with self.assertRaises(OversizedMessageError):
            list(framer.extract_messages())


class TestProtocolSerializationAndFunctions(unittest.TestCase):
    """
    Validates encode_message, decode_message, send_message, receive_message,
    and exact JSON schema adherence.
    """

    def test_example_draw_event_schema_compliance(self):
        """
        Validates the exact example drawing event schema specified in the prompt:
        {
          "type": "DRAW_EVENT",
          "room_id": "ROOM123",
          "sender_id": "USER001",
          "timestamp": 1790000000,
          "payload": {
            "stroke_id": "STROKE001",
            "color": "#FF0000",
            "width": 4,
            "points": [[100, 120], [105, 125], [110, 130]]
          }
        }
        """
        draw_msg = create_draw_event_msg(
            sender_id="USER001",
            room_id="ROOM123",
            stroke_id="STROKE001",
            points=[[100, 120], [105, 125], [110, 130]],
            color="#FF0000",
            width=4,
        )
        draw_msg.timestamp = 1790000000

        data = draw_msg.to_dict()
        self.assertEqual(data["type"], "DRAW_EVENT")
        self.assertEqual(data["room_id"], "ROOM123")
        self.assertEqual(data["sender_id"], "USER001")
        self.assertEqual(data["timestamp"], 1790000000)
        self.assertEqual(data["payload"]["stroke_id"], "STROKE001")
        self.assertEqual(data["payload"]["color"], "#FF0000")
        self.assertEqual(data["payload"]["width"], 4)
        self.assertEqual(data["payload"]["points"], [[100, 120], [105, 125], [110, 130]])

    def test_encode_and_decode_message(self):
        msg = create_join_room_msg("Alice", "client_101", room_id="ROOM_A")
        framed_bytes = encode_message(msg)

        # Check 4-byte header length matches
        payload_len = struct.unpack(HEADER_FORMAT, framed_bytes[:4])[0]
        self.assertEqual(payload_len, len(framed_bytes) - 4)

        # Decode payload
        decoded = decode_message(framed_bytes[4:])
        self.assertEqual(decoded.type, MessageType.JOIN_ROOM.value)
        self.assertEqual(decoded.room_id, "ROOM_A")
        self.assertEqual(decoded.sender_id, "client_101")
        self.assertEqual(decoded.payload["username"], "Alice")

    def test_invalid_json_handling(self):
        garbage_bytes = b"{this is not valid json!}"
        with self.assertRaises(InvalidJSONError):
            decode_message(garbage_bytes)


class TestSocketReusableFunctions(unittest.TestCase):
    """
    Validates send_message(), receive_message(), socket timeouts,
    oversized message detection, and EOF connection closure using loopback sockets.
    """

    def setUp(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        self.client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client_sock.connect(("127.0.0.1", port))
        self.server_sock, _ = listener.accept()
        listener.close()

    def tearDown(self):
        try:
            self.client_sock.close()
        except Exception:
            pass
        try:
            self.server_sock.close()
        except Exception:
            pass

    def test_send_and_receive_protocol_message(self):
        msg = create_draw_event_msg(
            sender_id="U1",
            room_id="ROOM1",
            stroke_id="STR1",
            points=[[10, 20], [30, 40]],
            color="#00FF00",
            width=2,
            seq_num=42,
        )
        send_message(self.client_sock, msg)
        received = receive_message(self.server_sock)

        self.assertEqual(received.type, MessageType.DRAW_EVENT.value)
        self.assertEqual(received.room_id, "ROOM1")
        self.assertEqual(received.sender_id, "U1")
        self.assertEqual(received.seq_num, 42)
        self.assertEqual(received.payload["points"], [[10, 20], [30, 40]])

    def test_send_and_receive_dictionary(self):
        data = {
            "type": "CHAT_MESSAGE",
            "room_id": "MAIN",
            "sender_id": "Alice",
            "timestamp": 123456789.0,
            "payload": {"text": "Hello Socket!"},
        }
        send_message(self.client_sock, data)
        received = receive_message(self.server_sock)

        self.assertEqual(received.type, "CHAT_MESSAGE")
        self.assertEqual(received.payload["text"], "Hello Socket!")

    def test_receive_oversized_message_raises_error(self):
        # Client sends frame claiming 50,000 bytes
        header = struct.pack(HEADER_FORMAT, 50000)
        self.client_sock.sendall(header)

        with self.assertRaises(OversizedMessageError):
            receive_message(self.server_sock, max_payload_size=1000)

    def test_receive_invalid_json_raises_error(self):
        garbage = b"Not Valid JSON!"
        header = struct.pack(HEADER_FORMAT, len(garbage))
        self.client_sock.sendall(header + garbage)

        with self.assertRaises(InvalidJSONError):
            receive_message(self.server_sock)

    def test_receive_connection_closed_raises_error(self):
        from protocol.framer import ConnectionClosedError
        self.client_sock.close()

        with self.assertRaises(ConnectionClosedError):
            receive_message(self.server_sock)

    def test_receive_socket_timeout(self):
        self.server_sock.settimeout(0.05)
        with self.assertRaises(socket.timeout):
            receive_message(self.server_sock)


if __name__ == "__main__":
    unittest.main()
