"""
Integration Tests for Real TCP Network Socket Communication.
Verifies:
  1. Multi-client real-time drawing, chat, and collaborative undo.
  2. Room codes and multi-session isolation.
  3. Monotonically increasing sequence numbering.
  4. Room persistent chat history delivery to late joiners.
  5. Reconnection and state recovery after disconnection.
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

    def test_01_multi_client_collaboration(self):
        """Verifies multi-client connection, drawing broadcast, chat, undo, and late-join sync."""
        # 1. Connect Client 1: Alice
        alice = WhiteboardClient(username="Alice")
        success, err, users_alice, actions_alice = alice.connect("127.0.0.1", self.port, room_code="MAIN")
        self.assertTrue(success, f"Alice connection failed: {err}")
        self.assertEqual(len(users_alice), 1)

        # 2. Connect Client 2: Bob
        bob = WhiteboardClient(username="Bob")
        success, err, users_bob, actions_bob = bob.connect("127.0.0.1", self.port, room_code="MAIN")
        self.assertTrue(success, f"Bob connection failed: {err}")
        self.assertEqual(len(users_bob), 2)

        time.sleep(0.1)

        # Alice should receive USER_JOINED for Bob
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

        # Bob should receive DRAW_STROKE
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
        success, err, users_charlie, actions_charlie = charlie.connect("127.0.0.1", self.port, room_code="MAIN")
        self.assertTrue(success)
        # Charlie should receive the stroke action with is_undone=True
        self.assertEqual(len(actions_charlie), 1)
        self.assertTrue(actions_charlie[0]["is_undone"])
        # Charlie should receive persistent chat history
        self.assertEqual(len(charlie.initial_chat_history), 1)
        self.assertEqual(charlie.initial_chat_history[0]["text"], "Looks great, Alice!")

        # Cleanup
        alice.disconnect()
        bob.disconnect()
        charlie.disconnect()
        time.sleep(0.1)

    def test_02_room_isolation(self):
        """Verifies that separate room codes remain completely isolated."""
        client_a = WhiteboardClient(username="UserA")
        success, _, _, _ = client_a.connect("127.0.0.1", self.port, room_code="ROOM_A")
        self.assertTrue(success)

        client_b = WhiteboardClient(username="UserB")
        success, _, _, _ = client_b.connect("127.0.0.1", self.port, room_code="ROOM_B")
        self.assertTrue(success)

        time.sleep(0.1)

        # UserA draws in ROOM_A
        stroke_a = WhiteboardAction(
            action_type=ActionType.RECTANGLE,
            user_id=client_a.user_id,
            username="UserA",
            points=[[0, 0], [100, 100]],
            color="#3498DB",
        )
        client_a.send_shape(stroke_a)
        time.sleep(0.15)

        # UserB in ROOM_B should NOT receive anything!
        b_messages = []
        while not client_b.inbound_queue.empty():
            b_messages.append(client_b.inbound_queue.get())
        self.assertEqual(len(b_messages), 0, "User in ROOM_B received traffic from ROOM_A!")

        # UserA_2 joins ROOM_A -> should receive stroke_a
        client_a2 = WhiteboardClient(username="UserA2")
        success, _, users, actions = client_a2.connect("127.0.0.1", self.port, room_code="ROOM_A")
        self.assertTrue(success)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action_type"], ActionType.RECTANGLE.value)

        client_a.disconnect()
        client_b.disconnect()
        client_a2.disconnect()
        time.sleep(0.1)

    def test_03_monotonic_sequence_numbers(self):
        """Verifies that server assigns monotonically increasing sequence numbers."""
        client1 = WhiteboardClient(username="SeqUser1")
        success, _, _, _ = client1.connect("127.0.0.1", self.port, room_code="ROOM_SEQ")
        self.assertTrue(success)

        client2 = WhiteboardClient(username="SeqUser2")
        success, _, _, _ = client2.connect("127.0.0.1", self.port, room_code="ROOM_SEQ")
        self.assertTrue(success)

        time.sleep(0.1)

        # Client1 executes 3 operations: stroke, shape, chat
        s1 = WhiteboardAction(action_type=ActionType.PEN, user_id=client1.user_id, username="SeqUser1", points=[[1, 1]])
        client1.send_stroke(s1)
        s2 = WhiteboardAction(action_type=ActionType.LINE, user_id=client1.user_id, username="SeqUser1", points=[[2, 2], [3, 3]])
        client1.send_shape(s2)
        client1.send_chat("Third operation")

        time.sleep(0.2)

        # Client2 receives all 3 operations; verify sequence numbers strictly increase
        received_seqs = []
        while not client2.inbound_queue.empty():
            msg = client2.inbound_queue.get()
            if msg.seq_num is not None:
                received_seqs.append(msg.seq_num)

        self.assertGreaterEqual(len(received_seqs), 3)
        for i in range(len(received_seqs) - 1):
            self.assertLess(received_seqs[i], received_seqs[i + 1])

        client1.disconnect()
        client2.disconnect()
        time.sleep(0.1)

    def test_04_reconnection_and_state_recovery(self):
        """Verifies client disconnection, reconnection, and state recovery."""
        client = WhiteboardClient(username="ReconnectingUser")
        success, _, _, _ = client.connect("127.0.0.1", self.port, room_code="ROOM_RECONNECT")
        self.assertTrue(success)
        orig_user_id = client.user_id
        orig_color = client.assigned_color

        # Draw an action and send a chat
        act = WhiteboardAction(action_type=ActionType.CIRCLE, user_id=orig_user_id, username="ReconnectingUser", points=[[10, 10], [50, 50]])
        client.send_shape(act)
        client.send_chat("Message before disconnect")
        time.sleep(0.15)

        # Graceful or ungraceful disconnect
        client.disconnect()
        self.assertFalse(client.is_connected())
        time.sleep(0.1)

        # Reconnect with state recovery
        rec_success, err, rec_users, rec_actions = client.reconnect()
        self.assertTrue(rec_success, f"Reconnection failed: {err}")
        self.assertTrue(client.is_connected())

        # Session attributes restored
        self.assertEqual(client.user_id, orig_user_id)
        self.assertEqual(client.assigned_color, orig_color)

        # Authoritative state recovered
        self.assertEqual(len(rec_actions), 1)
        self.assertEqual(rec_actions[0]["action_type"], ActionType.CIRCLE.value)
        self.assertEqual(len(client.initial_chat_history), 1)
        self.assertEqual(client.initial_chat_history[0]["text"], "Message before disconnect")

        client.disconnect()


if __name__ == "__main__":
    unittest.main()
