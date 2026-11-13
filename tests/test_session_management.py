"""
Unit & Integration Tests for Section 7: Session and Room Management.
Verifies:
  1. Session creation and unique room codes.
  2. Session joining using LAN IP and room code.
  3. Optional room password authentication (acceptance / rejection).
  4. Host identification and host migration upon host departure.
  5. Participant list synchronization and join/leave notifications.
  6. Host-controlled canvas clearing (non-host rejection vs host execution).
  7. Automatic synchronization of latest canvas state & participant list for new participants.
  8. Strict room isolation across separate rooms.
"""

import socket
import time
import unittest
from typing import List

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


class TestSessionAndRoomManagement(unittest.TestCase):

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

    def test_01_unique_room_codes_and_creation(self):
        """Verifies session creation and unique room code generation."""
        rm = self.server.room_manager

        # Generate 20 room codes and verify uniqueness
        generated = {rm.generate_unique_room_code() for _ in range(20)}
        self.assertEqual(len(generated), 20, "Duplicate room code generated!")

        # Explicit room creation
        room = rm.create_room(room_code="CHEM_101", name="Chemistry Lab", passcode="chem_pass")
        self.assertEqual(room.room_code, "CHEM_101")
        self.assertEqual(room.name, "Chemistry Lab")
        self.assertTrue(room.verify_passcode("chem_pass"))
        self.assertFalse(room.verify_passcode("wrong_pass"))

    def test_02_optional_room_passcode_authentication(self):
        """Verifies room access password verification and rejection handling."""
        # Create a protected room on server
        self.server.room_manager.create_room(room_code="SECRET_ROOM", passcode="SuperSecret99")

        # 1. Connect with wrong passcode -> should fail
        bad_client = WhiteboardClient(username="Intruder")
        success, err, _, _ = bad_client.connect(
            "127.0.0.1", self.port, room_code="SECRET_ROOM", passcode="WrongPassword"
        )
        self.assertFalse(success)
        self.assertIn("Invalid passcode", err)
        bad_client.disconnect()

        # 2. Connect with correct passcode -> should succeed
        good_client = WhiteboardClient(username="AuthorizedUser")
        success, err, users, actions = good_client.connect(
            "127.0.0.1", self.port, room_code="SECRET_ROOM", passcode="SuperSecret99"
        )
        self.assertTrue(success, f"Failed to connect with valid passcode: {err}")
        self.assertEqual(good_client.room_code, "SECRET_ROOM")

        good_client.disconnect()
        time.sleep(0.1)

    def test_03_host_identification_and_host_migration(self):
        """
        Verifies:
          1. First participant becomes Host.
          2. Second participant is not Host.
          3. When Host disconnects, host authority migrates to the next active peer.
        """
        # Client 1: Alice (Creator / First to enter)
        alice = WhiteboardClient(username="HostAlice")
        succ_a, _, users_a, _ = alice.connect("127.0.0.1", self.port, room_code="ROOM_HOST_TEST")
        self.assertTrue(succ_a)
        self.assertTrue(alice.is_host, "First participant was not designated as Host!")
        self.assertEqual(alice.host_id, alice.user_id)

        # Client 2: Bob
        bob = WhiteboardClient(username="Bob")
        succ_b, _, users_b, _ = bob.connect("127.0.0.1", self.port, room_code="ROOM_HOST_TEST")
        self.assertTrue(succ_b)
        self.assertFalse(bob.is_host, "Second participant was incorrectly designated as Host!")
        self.assertEqual(bob.host_id, alice.user_id)

        # Verify active users list in room
        room = self.server.room_manager.get_room("ROOM_HOST_TEST")
        active = room.get_active_users_list()
        self.assertEqual(len(active), 2)
        alice_entry = next(u for u in active if u["user_id"] == alice.user_id)
        bob_entry = next(u for u in active if u["user_id"] == bob.user_id)
        self.assertTrue(alice_entry["is_host"])
        self.assertFalse(bob_entry["is_host"])

        time.sleep(0.1)

        # Alice (Host) disconnects -> Bob should receive leave notification with new_host_id
        alice.disconnect()
        time.sleep(0.2)

        # Bob's receiver thread should have detected host migration
        self.assertTrue(bob.is_host, "Host did not migrate to Bob after Alice left!")
        self.assertEqual(bob.host_id, bob.user_id)

        # Room state should reflect Bob as host
        self.assertEqual(room.host_id, bob.user_id)

        bob.disconnect()
        time.sleep(0.1)

    def test_04_host_controlled_canvas_clearing(self):
        """
        Verifies that ONLY the room host can clear the canvas.
        Non-host clear requests must be rejected by the authoritative server.
        """
        alice = WhiteboardClient(username="AliceHost")
        succ_a, _, _, _ = alice.connect("127.0.0.1", self.port, room_code="ROOM_CLEAR_AUTH")
        self.assertTrue(succ_a)
        self.assertTrue(alice.is_host)

        bob = WhiteboardClient(username="BobGuest")
        succ_b, _, _, _ = bob.connect("127.0.0.1", self.port, room_code="ROOM_CLEAR_AUTH")
        self.assertTrue(succ_b)
        self.assertFalse(bob.is_host)

        # Alice draws a stroke
        stroke = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=alice.user_id,
            username="AliceHost",
            points=[[10, 10], [20, 20]],
            action_id="stroke_to_clear",
        )
        alice.send_stroke(stroke)
        time.sleep(0.15)

        room = self.server.room_manager.get_room("ROOM_CLEAR_AUTH")
        self.assertEqual(len(room.state.get_active_actions()), 1)

        # 1. Non-host Bob attempts to clear canvas -> Server must reject with PERMISSION_DENIED
        bob.send_clear()
        time.sleep(0.2)

        # Canvas actions must NOT have been cleared!
        self.assertEqual(
            len(room.state.get_active_actions()),
            1,
            "Non-host was able to clear the canvas on the server!",
        )

        # Bob should have received an ERROR protocol message
        bob_errors = []
        while not bob.inbound_queue.empty():
            msg = bob.inbound_queue.get()
            if msg.msg_type in (MessageType.ERROR.value, "ERROR"):
                bob_errors.append(msg)
        self.assertEqual(len(bob_errors), 1)
        self.assertEqual(bob_errors[0].payload.get("code"), "PERMISSION_DENIED")

        # 2. Host Alice calls clear canvas -> Server must accept and broadcast
        alice.send_clear()
        time.sleep(0.2)

        # The drawing stroke should be marked undone, leaving only the CLEAR action active
        active_acts = room.state.get_active_actions()
        self.assertEqual(len(active_acts), 1)
        self.assertEqual(active_acts[0].action_type, ActionType.CLEAR.value)
        undone_ids = [a.action_id for a in room.state.get_all_actions() if a.is_undone]
        self.assertIn("stroke_to_clear", undone_ids)

        # Bob should receive CLEAR_CANVAS message
        bob_clears = []
        while not bob.inbound_queue.empty():
            msg = bob.inbound_queue.get()
            if msg.msg_type in (MessageType.CLEAR_CANVAS.value, "CLEAR_CANVAS", "CLEAR"):
                bob_clears.append(msg)
        self.assertEqual(len(bob_clears), 1)

        alice.disconnect()
        bob.disconnect()
        time.sleep(0.1)

    def test_05_new_participant_automatic_synchronization(self):
        """
        Verifies that late-joining participants automatically receive:
          1. Complete accumulated drawing state.
          2. Persistent group chat history.
          3. Complete active participant roster including host status.
        """
        user1 = WhiteboardClient(username="SyncAlice")
        succ1, _, _, _ = user1.connect("127.0.0.1", self.port, room_code="ROOM_LATE_SYNC")
        self.assertTrue(succ1)

        # Alice draws 2 strokes and sends 2 chat messages
        s1 = WhiteboardAction(
            action_type=ActionType.PEN, user_id=user1.user_id, username="SyncAlice", points=[[5, 5]]
        )
        s2 = WhiteboardAction(
            action_type=ActionType.RECTANGLE, user_id=user1.user_id, username="SyncAlice", points=[[0, 0], [50, 50]]
        )
        user1.send_stroke(s1)
        user1.send_shape(s2)
        user1.send_chat("First message")
        user1.send_chat("Second message")
        time.sleep(0.2)

        # User 2 joins late
        user2 = WhiteboardClient(username="SyncBob")
        succ2, err2, initial_users, initial_actions = user2.connect(
            "127.0.0.1", self.port, room_code="ROOM_LATE_SYNC"
        )
        self.assertTrue(succ2)

        # Verify automatic canvas state delivery
        self.assertEqual(len(initial_actions), 2)
        # Verify automatic participant list delivery
        self.assertEqual(len(initial_users), 2)
        alice_entry = next(u for u in initial_users if u["username"] == "SyncAlice")
        self.assertTrue(alice_entry["is_host"])

        # Verify automatic persistent chat history delivery
        self.assertEqual(len(user2.initial_chat_history), 2)
        self.assertEqual(user2.initial_chat_history[0]["text"], "First message")
        self.assertEqual(user2.initial_chat_history[1]["text"], "Second message")

        user1.disconnect()
        user2.disconnect()
        time.sleep(0.1)

    def test_06_strict_room_isolation_under_load(self):
        """
        Verifies that an event in Room Alpha NEVER bleeds into Room Beta.
        """
        client_alpha = WhiteboardClient(username="AlphaOnly")
        client_beta = WhiteboardClient(username="BetaOnly")

        succ_a, _, _, _ = client_alpha.connect("127.0.0.1", self.port, room_code="ROOM_ALPHA_ISO")
        succ_b, _, _, _ = client_beta.connect("127.0.0.1", self.port, room_code="ROOM_BETA_ISO")
        self.assertTrue(succ_a)
        self.assertTrue(succ_b)

        time.sleep(0.1)

        # Clear inbound queues
        while not client_beta.inbound_queue.empty():
            client_beta.inbound_queue.get()

        # Send heavy traffic into Room Alpha
        for i in range(10):
            act = WhiteboardAction(
                action_type=ActionType.PEN,
                user_id=client_alpha.user_id,
                username="AlphaOnly",
                points=[[i, i]],
            )
            client_alpha.send_stroke(act)
            client_alpha.send_chat(f"Chat in Alpha {i}")

        time.sleep(0.3)

        # Client Beta should have received ZERO messages!
        beta_messages = []
        while not client_beta.inbound_queue.empty():
            beta_messages.append(client_beta.inbound_queue.get())

        self.assertEqual(len(beta_messages), 0, "Traffic from ROOM_ALPHA_ISO leaked into ROOM_BETA_ISO!")

        client_alpha.disconnect()
        client_beta.disconnect()
        time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
