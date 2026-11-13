"""
Unit Tests for Low-Level TCP Stream Framing & Application Protocol Serialization.
"""

import struct
import unittest

from protocol.framer import (
    HEADER_FORMAT,
    HEADER_SIZE,
    FrameError,
    StreamFramer,
    pack_message,
)
from protocol.messages import (
    MessageType,
    ProtocolMessage,
    create_chat_msg,
    create_draw_segment_msg,
    create_join_msg,
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
        """Validates that excessively large payload headers trigger FrameError."""
        framer = StreamFramer(max_payload_size=1000)
        # Construct header claiming 10,000 bytes
        malicious_header = struct.pack(HEADER_FORMAT, 10000)
        framer.feed(malicious_header)

        with self.assertRaises(FrameError):
            list(framer.extract_messages())


class TestProtocolSerialization(unittest.TestCase):
    """
    Validates JSON protocol envelope serialization and deserialization.
    """

    def test_join_message_roundtrip(self):
        msg = create_join_msg("Alice", "client_101")
        raw = serialize_message(msg)
        restored = deserialize_message(raw)

        self.assertEqual(restored.msg_type, MessageType.JOIN.value)
        self.assertEqual(restored.payload["username"], "Alice")
        self.assertEqual(restored.payload["client_id"], "client_101")
        self.assertEqual(restored.msg_id, msg.msg_id)

    def test_draw_segment_roundtrip(self):
        msg = create_draw_segment_msg("user_1", "stroke_55", 10.5, 20.5, 30.5, 40.5, "#FF0000", 5)
        raw = serialize_message(msg)
        restored = deserialize_message(raw)

        self.assertEqual(restored.msg_type, MessageType.DRAW_SEGMENT.value)
        self.assertEqual(restored.payload["x1"], 10.5)
        self.assertEqual(restored.payload["color"], "#FF0000")
        self.assertEqual(restored.payload["width"], 5)

    def test_chat_message_roundtrip(self):
        msg = create_chat_msg("user_1", "Bob", "Hello everyone!", color="#3498DB")
        raw = serialize_message(msg)
        restored = deserialize_message(raw)

        self.assertEqual(restored.msg_type, MessageType.CHAT.value)
        self.assertEqual(restored.payload["username"], "Bob")
        self.assertEqual(restored.payload["text"], "Hello everyone!")


if __name__ == "__main__":
    unittest.main()
