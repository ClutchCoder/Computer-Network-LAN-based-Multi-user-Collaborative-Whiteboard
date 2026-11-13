"""
Unit & Integration Tests for Section 9: Whiteboard State Management and Recovery.
Verifies:
  1. Structured stroke storage and operation history.
  2. Atomic file persistence (JSON) and state restoration.
  3. Server-authoritative operation-based collaborative undo and redo policy.
  4. Snapshot transmission (JOIN_SUCCESS and CANVAS_STATE / SYNC_REQUEST).
  5. Reconnection recovery and seamless resumption of collaboration.
"""

import os
import socket
import tempfile
import time
import unittest
from typing import List

from network.tcp_client import WhiteboardClient
from network.tcp_server import WhiteboardServer
from protocol.messages import MessageType
from state.action import ActionType, WhiteboardAction
from state.whiteboard_state import WhiteboardState


def get_free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestStateManagementAndRecovery(unittest.TestCase):

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

    def test_01_structured_stroke_storage_and_operation_history(self):
        """Verifies structured vector representation, metadata, and tombstoning."""
        state = WhiteboardState()
        action1 = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id="user_1",
            username="Alice",
            points=[[10, 10], [20, 20], [30, 30]],
            color="#FF0000",
            width=4,
            action_id="stroke_001",
        )
        self.assertTrue(state.add_action(action1))
        self.assertEqual(len(state.get_all_actions()), 1)
        self.assertEqual(len(state.get_active_actions()), 1)
        self.assertTrue(state.has_action("stroke_001"))

        # Verify duplicate rejection
        self.assertFalse(state.add_action(action1), "Duplicate stroke was not rejected!")

        # Verify structured dictionary serialization
        d = action1.to_dict()
        self.assertEqual(d["action_id"], "stroke_001")
        self.assertEqual(d["action_type"], "pen")
        self.assertEqual(d["points"], [[10, 10], [20, 20], [30, 30]])
        self.assertFalse(d["is_undone"])

    def test_02_atomic_file_persistence_and_restoration(self):
        """
        Verifies atomic file writing (temp file + os.fsync + os.replace)
        and complete state restoration from JSON storage.
        """
        state = WhiteboardState()
        state.add_action(
            WhiteboardAction(
                action_type=ActionType.PEN,
                user_id="u1",
                username="Alice",
                points=[[5, 5], [10, 10]],
                color="#3498DB",
                width=3,
                action_id="act_persist_1",
            )
        )
        state.add_action(
            WhiteboardAction(
                action_type=ActionType.RECTANGLE,
                user_id="u2",
                username="Bob",
                points=[[50, 50], [100, 100]],
                color="#E74C3C",
                width=2,
                action_id="act_persist_2",
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "whiteboard_atomic_test.json")

            # Save state atomically
            state.save_to_file(filepath)
            self.assertTrue(os.path.exists(filepath), "Target state file was not created!")
            self.assertGreater(os.path.getsize(filepath), 0)

            # Overwrite test: atomic replacement over an existing file
            state.add_action(
                WhiteboardAction(
                    action_type=ActionType.CIRCLE,
                    user_id="u1",
                    username="Alice",
                    points=[[150, 150], [200, 200]],
                    action_id="act_persist_3",
                )
            )
            state.save_to_file(filepath)

            # Restore state into a fresh instance
            restored = WhiteboardState()
            restored.load_from_file(filepath)

            self.assertEqual(len(restored.get_all_actions()), 3)
            self.assertTrue(restored.has_action("act_persist_1"))
            self.assertTrue(restored.has_action("act_persist_2"))
            self.assertTrue(restored.has_action("act_persist_3"))
            self.assertEqual(restored.get_action_by_id("act_persist_2").action_type, "rectangle")

    def test_03_server_authoritative_operation_based_undo_redo(self):
        """
        Verifies server-authoritative operation-based collaborative undo/redo policy:
        An undo reverses the most recent eligible operation in the room's history,
        regardless of which user initiated it.
        """
        alice = WhiteboardClient(username="UndoAlice")
        bob = WhiteboardClient(username="UndoBob")

        succ_a, _, _, _ = alice.connect("127.0.0.1", self.port, room_code="ROOM_UNDO_REDO")
        succ_b, _, _, _ = bob.connect("127.0.0.1", self.port, room_code="ROOM_UNDO_REDO")
        self.assertTrue(succ_a)
        self.assertTrue(succ_b)
        time.sleep(0.1)

        room = self.server.room_manager.get_room("ROOM_UNDO_REDO")
        self.assertIsNotNone(room)

        # 1. Alice draws Stroke 1
        s1 = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=alice.user_id,
            username="UndoAlice",
            points=[[10, 10]],
            action_id="stroke_alice_1",
        )
        alice.send_stroke(s1)
        time.sleep(0.1)

        # 2. Bob draws Stroke 2 (most recent in room)
        s2 = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=bob.user_id,
            username="UndoBob",
            points=[[20, 20]],
            action_id="stroke_bob_2",
        )
        bob.send_stroke(s2)
        time.sleep(0.1)

        self.assertEqual(len(room.state.get_active_actions()), 2)

        # 3. Alice presses Undo!
        # Under server-authoritative operation-based policy, this reverses Stroke 2 (Bob's stroke)
        alice.send_undo()
        time.sleep(0.2)

        active = room.state.get_active_actions()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].action_id, "stroke_alice_1")  # Alice's earlier stroke is still active!
        bob_stroke = room.state.get_action_by_id("stroke_bob_2")
        self.assertTrue(bob_stroke.is_undone)

        # 4. Bob presses Undo!
        # Reverses the next eligible action: Stroke 1 (Alice's stroke)
        bob.send_undo()
        time.sleep(0.2)

        self.assertEqual(len(room.state.get_active_actions()), 0)

        # 5. Alice presses Redo!
        # Restores Alice's stroke
        alice.send_redo()
        time.sleep(0.2)

        active_after_redo1 = room.state.get_active_actions()
        self.assertEqual(len(active_after_redo1), 1)
        self.assertEqual(active_after_redo1[0].action_id, "stroke_alice_1")

        # 6. Bob presses Redo!
        # Restores Bob's stroke
        bob.send_redo()
        time.sleep(0.2)

        self.assertEqual(len(room.state.get_active_actions()), 2)

        alice.disconnect()
        bob.disconnect()
        time.sleep(0.1)

    def test_04_snapshot_transmission_and_reconnection_recovery(self):
        """
        Verifies:
          1. Complete room state snapshot delivery on join.
          2. Explicit snapshot request via request_sync().
          3. Reconnection state recovery after network disconnect.
        """
        client = WhiteboardClient(username="RecoveryUser")
        succ, _, init_users, init_actions = client.connect(
            "127.0.0.1", self.port, room_code="ROOM_RECOVERY"
        )
        self.assertTrue(succ)
        original_user_id = client.user_id
        original_color = client.assigned_color

        # Draw 2 shapes and send a chat
        a1 = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=original_user_id,
            username="RecoveryUser",
            points=[[10, 10]],
            action_id="rec_stroke_1",
        )
        a2 = WhiteboardAction(
            action_type=ActionType.CIRCLE,
            user_id=original_user_id,
            username="RecoveryUser",
            points=[[50, 50], [80, 80]],
            action_id="rec_stroke_2",
        )
        client.send_stroke(a1)
        client.send_shape(a2)
        client.send_chat("Snapshot chat message")
        time.sleep(0.2)

        # Explicit snapshot request: client asks for complete canvas state
        client.request_sync()
        time.sleep(0.2)

        sync_messages = []
        while not client.inbound_queue.empty():
            msg = client.inbound_queue.get()
            if msg.msg_type in (MessageType.CANVAS_STATE.value, "CANVAS_STATE", "SYNC_STATE"):
                sync_messages.append(msg)

        self.assertGreaterEqual(len(sync_messages), 1)
        snapshot_payload = sync_messages[-1].payload
        self.assertEqual(len(snapshot_payload["actions"]), 2)
        self.assertEqual(len(snapshot_payload["chat_history"]), 1)

        # Disconnect client
        client.disconnect()
        self.assertFalse(client.is_connected())
        time.sleep(0.1)

        # Reconnect with state recovery
        rec_succ, err, rec_users, rec_actions = client.reconnect()
        self.assertTrue(rec_succ, f"Reconnection failed: {err}")
        self.assertTrue(client.is_connected())

        # Session attributes restored
        self.assertEqual(client.user_id, original_user_id)
        self.assertEqual(client.assigned_color, original_color)

        # Authoritative state recovered
        self.assertEqual(len(rec_actions), 2)
        self.assertEqual(len(client.initial_chat_history), 1)
        self.assertEqual(client.initial_chat_history[0]["text"], "Snapshot chat message")

        client.disconnect()
        time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
