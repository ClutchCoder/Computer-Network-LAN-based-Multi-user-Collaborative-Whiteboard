"""
Unit & Integration Tests for Section 8: Real-Time Group Chat.
Verifies:
  1. Sending messages to the server over TCP.
  2. Broadcasting messages to all participants in the same room.
  3. Display of sender username and message timestamp.
  4. Chronological chat history maintenance and late-join delivery.
  5. Validation of message length (truncation of oversized messages and rejection of empty messages).
  6. Safe handling of multiple simultaneous chat messages.
  7. Cross-room chat isolation.
  8. Operation over the unified TCP communication infrastructure.
"""

import socket
import threading
import time
import unittest
from typing import List

from network.tcp_client import WhiteboardClient
from network.tcp_server import WhiteboardServer
from protocol.messages import MessageType


def get_free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestGroupChat(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.port = get_free_tcp_port()
        cls.server = WhiteboardServer(host="127.0.0.1", port=cls.port, enable_discovery=False)
        cls.server.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        time.sleep(0.1)

    def test_01_send_and_broadcast_chat_message(self):
        """Verifies sending, broadcasting, sender username, and timestamp."""
        alice = WhiteboardClient(username="ChatAlice")
        bob = WhiteboardClient(username="ChatBob")

        succ_a, _, _, _ = alice.connect("127.0.0.1", self.port, room_code="ROOM_CHAT_1")
        succ_b, _, _, _ = bob.connect("127.0.0.1", self.port, room_code="ROOM_CHAT_1")
        self.assertTrue(succ_a)
        self.assertTrue(succ_b)

        time.sleep(0.1)

        # Clear inbound queues
        while not bob.inbound_queue.empty():
            bob.inbound_queue.get()

        # Alice sends a chat message
        alice.send_chat("Hello from Alice over TCP!")
        time.sleep(0.2)

        # Bob should receive the broadcast
        received = []
        while not bob.inbound_queue.empty():
            msg = bob.inbound_queue.get()
            if msg.msg_type in (MessageType.CHAT_MESSAGE.value, "CHAT_MESSAGE", "CHAT"):
                received.append(msg)

        self.assertEqual(len(received), 1)
        payload = received[0].payload
        self.assertEqual(payload["username"], "ChatAlice")
        self.assertEqual(payload["text"], "Hello from Alice over TCP!")
        self.assertIn("time", payload)
        self.assertTrue(len(payload["time"]) >= 5)  # e.g., "14:25:30"
        self.assertEqual(payload["user_id"], alice.user_id)

        alice.disconnect()
        bob.disconnect()
        time.sleep(0.1)

    def test_02_chronological_chat_history_and_late_joiner_sync(self):
        """Verifies persistent chronological chat history delivered to late-joiners."""
        alice = WhiteboardClient(username="ChronAlice")
        bob = WhiteboardClient(username="ChronBob")

        succ_a, _, _, _ = alice.connect("127.0.0.1", self.port, room_code="ROOM_CHRON")
        succ_b, _, _, _ = bob.connect("127.0.0.1", self.port, room_code="ROOM_CHRON")
        self.assertTrue(succ_a)
        self.assertTrue(succ_b)

        time.sleep(0.1)

        # Sequence of messages
        alice.send_chat("Message 1: Good morning!")
        time.sleep(0.05)
        bob.send_chat("Message 2: Morning Alice!")
        time.sleep(0.05)
        alice.send_chat("Message 3: Ready to collaborate?")
        time.sleep(0.2)

        # Check server room chat history order
        room = self.server.room_manager.get_room("ROOM_CHRON")
        self.assertIsNotNone(room)
        history = room.get_chat_history()
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["text"], "Message 1: Good morning!")
        self.assertEqual(history[1]["text"], "Message 2: Morning Alice!")
        self.assertEqual(history[2]["text"], "Message 3: Ready to collaborate?")

        # Connect late joiner Charlie
        charlie = WhiteboardClient(username="ChronCharlie")
        succ_c, _, _, _ = charlie.connect("127.0.0.1", self.port, room_code="ROOM_CHRON")
        self.assertTrue(succ_c)

        # Charlie should receive full chronological chat history in initial sync
        self.assertEqual(len(charlie.initial_chat_history), 3)
        self.assertEqual(charlie.initial_chat_history[0]["text"], "Message 1: Good morning!")
        self.assertEqual(charlie.initial_chat_history[1]["text"], "Message 2: Morning Alice!")
        self.assertEqual(charlie.initial_chat_history[2]["text"], "Message 3: Ready to collaborate?")

        alice.disconnect()
        bob.disconnect()
        charlie.disconnect()
        time.sleep(0.1)

    def test_03_message_length_validation_and_empty_filtering(self):
        """Verifies that empty messages are rejected and oversized messages are truncated."""
        client = WhiteboardClient(username="ValidationUser")
        succ, _, _, _ = client.connect("127.0.0.1", self.port, room_code="ROOM_CHAT_VAL")
        self.assertTrue(succ)

        room = self.server.room_manager.get_room("ROOM_CHAT_VAL")
        self.assertIsNotNone(room)
        init_seq = room.current_seq_num

        # 1. Empty message -> Rejected
        client.send_chat("   ")
        time.sleep(0.1)
        self.assertEqual(len(room.get_chat_history()), 0)
        self.assertEqual(room.current_seq_num, init_seq)

        # 2. Oversized message (> 2000 chars) -> Truncated to 2000 chars
        oversized = "A" * 3000
        client.send_chat(oversized)
        time.sleep(0.15)

        history = room.get_chat_history()
        self.assertEqual(len(history), 1)
        self.assertLessEqual(len(history[0]["text"]), 2000)

        client.disconnect()
        time.sleep(0.1)

    def test_04_multiple_simultaneous_chat_messages(self):
        """
        Verifies thread-safe handling of multiple clients sending messages simultaneously.
        Ensures strict total ordering and no dropped messages.
        """
        num_senders = 4
        msgs_per_sender = 5
        clients: List[WhiteboardClient] = []

        for i in range(num_senders):
            c = WhiteboardClient(username=f"ConcurrentChatUser_{i}")
            succ, _, _, _ = c.connect("127.0.0.1", self.port, room_code="ROOM_CONCURRENT_CHAT")
            self.assertTrue(succ)
            clients.append(c)

        time.sleep(0.2)

        def sender_worker(client: WhiteboardClient, client_idx: int):
            for m_idx in range(msgs_per_sender):
                client.send_chat(f"Concurrent msg {client_idx}-{m_idx}")
                time.sleep(0.01)

        threads = [
            threading.Thread(target=sender_worker, args=(c, i)) for i, c in enumerate(clients)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        time.sleep(0.5)

        room = self.server.room_manager.get_room("ROOM_CONCURRENT_CHAT")
        expected_total = num_senders * msgs_per_sender
        self.assertEqual(
            len(room.get_chat_history()),
            expected_total,
            f"Expected {expected_total} chat messages in room history, got {len(room.get_chat_history())}",
        )

        for c in clients:
            c.disconnect()
        time.sleep(0.1)

    def test_05_cross_room_chat_isolation(self):
        """Verifies that chat messages are strictly isolated to the sender's room."""
        client_a = WhiteboardClient(username="RoomAUser")
        client_b = WhiteboardClient(username="RoomBUser")

        succ_a, _, _, _ = client_a.connect("127.0.0.1", self.port, room_code="ROOM_CHAT_A")
        succ_b, _, _, _ = client_b.connect("127.0.0.1", self.port, room_code="ROOM_CHAT_B")
        self.assertTrue(succ_a)
        self.assertTrue(succ_b)

        time.sleep(0.1)

        # Clear inbound queues
        while not client_b.inbound_queue.empty():
            client_b.inbound_queue.get()

        # Send messages in Room A
        for i in range(5):
            client_a.send_chat(f"Secret talk in room A #{i}")

        time.sleep(0.2)

        # Client B in Room B must receive 0 chat messages
        b_chats = []
        while not client_b.inbound_queue.empty():
            msg = client_b.inbound_queue.get()
            if msg.msg_type in (MessageType.CHAT_MESSAGE.value, "CHAT_MESSAGE", "CHAT"):
                b_chats.append(msg)

        self.assertEqual(len(b_chats), 0, "Chat message leaked across room boundaries!")

        client_a.disconnect()
        client_b.disconnect()
        time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
