"""
Integration Tests for Real TCP Network Socket Communication.
Spins up a live TCP server on localhost and connects multiple concurrent clients
to verify message broadcast, drawing synchronization, group chat, and late-join state sync.
"""

import socket
import time
import unittest

from network.tcp_client import WhiteboardClient
from network.tcp_server import WhiteboardServer
from protocol.messages import MessageType
from state.action import ActionType, WhiteboardAction


def get_free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestNetworkIntegration(unittest.TestCase):

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

    def test_multi_client_collaboration(self):
        # 1. Connect Client 1: Alice
        alice = WhiteboardClient(username="Alice")
        success, err, users_alice, actions_alice = alice.connect("127.0.0.1", self.port)
        self.assertTrue(success, f"Alice connection failed: {err}")
        self.assertEqual(len(users_alice), 1)

        # 2. Connect Client 2: Bob
        bob = WhiteboardClient(username="Bob")
        success, err, users_bob, actions_bob = bob.connect("127.0.0.1", self.port)
        self.assertTrue(success, f"Bob connection failed: {err}")
        self.assertEqual(len(users_bob), 2)

        # Wait a moment for network dispatch
        time.sleep(0.1)

        # Alice should have received USER_JOINED notification for Bob
        join_notifications = []
        while not alice.inbound_queue.empty():
            msg = alice.inbound_queue.get()
            if msg.msg_type == MessageType.USER_JOINED.value:
                join_notifications.append(msg)
        self.assertGreaterEqual(len(join_notifications), 1)
        self.assertEqual(join_notifications[0].payload["username"], "Bob")

        # 3. Alice draws a stroke
        stroke = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=alice.user_id,
            username="Alice",
            points=[[10, 20], [30, 40], [50, 60]],
            color="#FF0000",
            width=4,
        )
        alice.send_stroke(stroke)
        time.sleep(0.15)

        # Bob should receive DRAW_STROKE in his inbound queue
        bob_strokes = []
        while not bob.inbound_queue.empty():
            msg = bob.inbound_queue.get()
            if msg.msg_type == MessageType.DRAW_STROKE.value:
                bob_strokes.append(msg)
        self.assertEqual(len(bob_strokes), 1)
        self.assertEqual(bob_strokes[0].payload["action"]["color"], "#FF0000")

        # 4. Bob sends a chat message
        bob.send_chat("Looks great, Alice!")
        time.sleep(0.15)

        # Alice should receive CHAT message
        alice_chats = []
        while not alice.inbound_queue.empty():
            msg = alice.inbound_queue.get()
            if msg.msg_type == MessageType.CHAT.value:
                alice_chats.append(msg)
        self.assertEqual(len(alice_chats), 1)
        self.assertEqual(alice_chats[0].payload["text"], "Looks great, Alice!")
        self.assertEqual(alice_chats[0].payload["username"], "Bob")

        # 5. Alice triggers Undo
        alice.send_undo()
        time.sleep(0.15)

        # Bob should receive UNDO message
        bob_undos = []
        while not bob.inbound_queue.empty():
            msg = bob.inbound_queue.get()
            if msg.msg_type == MessageType.UNDO.value:
                bob_undos.append(msg)
        self.assertEqual(len(bob_undos), 1)
        self.assertEqual(bob_undos[0].payload["action_id"], stroke.action_id)

        # 6. Test Late-Join State Synchronization: Connect Client 3 (Charlie)
        charlie = WhiteboardClient(username="Charlie")
        success, err, users_charlie, actions_charlie = charlie.connect("127.0.0.1", self.port)
        self.assertTrue(success)
        # Charlie should have received the stroke action in actions_charlie (with is_undone=True)
        self.assertEqual(len(actions_charlie), 1)
        self.assertTrue(actions_charlie[0]["is_undone"])

        # 7. Disconnect Alice
        alice.disconnect()
        time.sleep(0.15)

        # Bob should receive USER_LEFT notice
        bob_left_notices = []
        while not bob.inbound_queue.empty():
            msg = bob.inbound_queue.get()
            if msg.msg_type == MessageType.USER_LEFT.value:
                bob_left_notices.append(msg)
        self.assertEqual(len(bob_left_notices), 1)
        self.assertEqual(bob_left_notices[0].payload["username"], "Alice")

        # Clean up
        bob.disconnect()
        charlie.disconnect()


if __name__ == "__main__":
    unittest.main()
