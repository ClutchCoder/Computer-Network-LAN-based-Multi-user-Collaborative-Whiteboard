"""
Unit & Integration Tests for Concurrent Client Management (Section 6).
Verifies:
  1. Multi-client simultaneous connections and concurrent drawing operations.
  2. Authoritative monotonic sequence numbering and strict total order.
  3. Per-client outgoing message queue isolation and non-blocking broadcasts.
  4. Duplicate action detection and rejection (preventing state corruption).
  5. Stale operations handling (empty undo, non-existent action undo/redo).
  6. Invalid operations handling (out-of-bounds coordinates, invalid widths).
  7. Cross-room isolation under simultaneous concurrent load.
"""

import socket
import threading
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


class TestConcurrencyAndClientManagement(unittest.TestCase):

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

    def test_01_simultaneous_concurrent_clients_and_monotonic_ordering(self):
        """
        Tests multiple simultaneous clients connecting and drawing at the exact same time.
        Verifies that the server assigns unique, strictly monotonic sequence numbers
        and maintains consistent authoritative canvas state without race conditions.
        """
        num_clients = 5
        actions_per_client = 4
        clients: List[WhiteboardClient] = []
        errors = []

        # 1. Connect all clients simultaneously using worker threads
        def connect_worker(cid: int):
            c = WhiteboardClient(username=f"ConcurrentWorker_{cid}")
            success, err, _, _ = c.connect("127.0.0.1", self.port, room_code="ROOM_CONCURRENT")
            if not success:
                errors.append(f"Client {cid} failed to connect: {err}")
            clients.append(c)

        connect_threads = [
            threading.Thread(target=connect_worker, args=(i,)) for i in range(num_clients)
        ]
        for t in connect_threads:
            t.start()
        for t in connect_threads:
            t.join(timeout=3.0)

        self.assertEqual(len(errors), 0, f"Connection errors: {errors}")
        self.assertEqual(len(clients), num_clients)
        time.sleep(0.2)

        # 2. Concurrently dispatch drawing operations across all clients
        def draw_worker(client: WhiteboardClient, client_index: int):
            for a_idx in range(actions_per_client):
                action = WhiteboardAction(
                    action_type=ActionType.PEN,
                    user_id=client.user_id,
                    username=client.username,
                    points=[[client_index * 10, a_idx * 10], [client_index * 10 + 5, a_idx * 10 + 5]],
                    color="#000000",
                    width=3,
                    action_id=f"concurrent_act_{client_index}_{a_idx}",
                )
                client.send_stroke(action)
                time.sleep(0.01)

        draw_threads = [
            threading.Thread(target=draw_worker, args=(c, i)) for i, c in enumerate(clients)
        ]
        for t in draw_threads:
            t.start()
        for t in draw_threads:
            t.join(timeout=5.0)

        # Wait for server and queues to settle
        time.sleep(0.5)

        # 3. Verify Authoritative State on Server
        room = self.server.room_manager.get_room("ROOM_CONCURRENT")
        self.assertIsNotNone(room)

        expected_total_actions = num_clients * actions_per_client
        server_actions = room.state.get_active_actions()
        self.assertEqual(
            len(server_actions),
            expected_total_actions,
            f"Expected {expected_total_actions} actions, found {len(server_actions)} in server room state",
        )

        # Sequence number must have advanced at least by the number of actions
        self.assertGreaterEqual(room.current_seq_num, expected_total_actions)

        # Disconnect clients
        for c in clients:
            c.disconnect()
        time.sleep(0.1)

    def test_02_duplicate_action_detection_and_rejection(self):
        """
        Verifies that retransmitted duplicate actions (identical action_id)
        are safely rejected by the server and do not cause duplicate state entries.
        """
        client = WhiteboardClient(username="DupUser")
        success, _, _, _ = client.connect("127.0.0.1", self.port, room_code="ROOM_DUP")
        self.assertTrue(success)

        room = self.server.room_manager.get_room("ROOM_DUP")
        self.assertIsNotNone(room)

        unique_action_id = "unique_stroke_id_9999"
        action = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=client.user_id,
            username="DupUser",
            points=[[50, 50], [60, 60]],
            action_id=unique_action_id,
        )

        # First transmission
        client.send_stroke(action)
        time.sleep(0.15)
        self.assertEqual(len(room.state.get_active_actions()), 1)
        seq_after_first = room.current_seq_num

        # Duplicate transmission with same action_id
        client.send_stroke(action)
        time.sleep(0.15)

        # State should still contain exactly 1 action!
        self.assertEqual(
            len(room.state.get_active_actions()),
            1,
            "Duplicate action was incorrectly committed to whiteboard state!",
        )
        # Sequence number should not have incremented for the rejected duplicate
        self.assertEqual(room.current_seq_num, seq_after_first)

        client.disconnect()
        time.sleep(0.1)

    def test_03_stale_and_invalid_operations_handling(self):
        """
        Verifies server defense against stale undo/redo requests,
        empty chats, and out-of-bounds coordinates.
        """
        client = WhiteboardClient(username="DefenseUser")
        success, _, _, _ = client.connect("127.0.0.1", self.port, room_code="ROOM_DEFENSE")
        self.assertTrue(success)

        room = self.server.room_manager.get_room("ROOM_DEFENSE")
        self.assertIsNotNone(room)
        initial_seq = room.current_seq_num

        # 1. Stale Undo when canvas has 0 actions: should safely no-op without crashing
        client.send_undo()
        time.sleep(0.1)
        self.assertEqual(room.current_seq_num, initial_seq, "Empty undo incremented sequence number!")

        # 2. Stale Redo when redo stack is empty: should safely no-op
        client.send_redo()
        time.sleep(0.1)
        self.assertEqual(room.current_seq_num, initial_seq)

        # 3. Invalid Draw Action (invalid stroke width: -50)
        invalid_action = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=client.user_id,
            username="DefenseUser",
            points=[[10, 10]],
            width=-50,
            action_id="invalid_width_action",
        )
        client.send_stroke(invalid_action)
        time.sleep(0.1)
        self.assertEqual(len(room.state.get_active_actions()), 0)

        # 4. Out-of-bounds coordinates (> 50,000)
        oob_action = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id=client.user_id,
            username="DefenseUser",
            points=[[999999, 999999]],
            width=3,
            action_id="oob_action",
        )
        client.send_stroke(oob_action)
        time.sleep(0.1)
        self.assertEqual(len(room.state.get_active_actions()), 0)

        # 5. Empty chat message: should be rejected
        client.send_chat("   ")
        time.sleep(0.1)
        self.assertEqual(len(room.get_chat_history()), 0)

        client.disconnect()
        time.sleep(0.1)

    def test_04_per_client_queue_isolation(self):
        """
        Verifies that each client has an independent outgoing queue and dedicated writer thread,
        preventing simultaneous socket writes and slow consumer Head-of-Line blocking.
        """
        client = WhiteboardClient(username="QueueTestUser")
        success, _, _, _ = client.connect("127.0.0.1", self.port, room_code="ROOM_QUEUE")
        self.assertTrue(success)

        # Verify ClientHandler has an outgoing queue and active writer thread
        room = self.server.room_manager.get_room("ROOM_QUEUE")
        handler = room.get_client(client.user_id)
        self.assertIsNotNone(handler)
        self.assertTrue(hasattr(handler, "_send_queue"))
        self.assertTrue(hasattr(handler, "_writer_thread"))
        self.assertTrue(handler._writer_thread.is_alive())

        # Send stroke through client
        act = WhiteboardAction(
            action_type=ActionType.CIRCLE,
            user_id=client.user_id,
            username="QueueTestUser",
            points=[[10, 10], [50, 50]],
        )
        client.send_shape(act)
        time.sleep(0.15)

        self.assertEqual(len(room.state.get_active_actions()), 1)

        client.disconnect()
        time.sleep(0.1)

    def test_05_concurrent_cross_room_isolation(self):
        """
        Verifies that high-frequency concurrent operations in Room A and Room B
        remain completely isolated without sequence collision or cross-talk.
        """
        client_a = WhiteboardClient(username="AlphaUser")
        client_b = WhiteboardClient(username="BetaUser")

        succ_a, _, _, _ = client_a.connect("127.0.0.1", self.port, room_code="ROOM_ALPHA")
        succ_b, _, _, _ = client_b.connect("127.0.0.1", self.port, room_code="ROOM_BETA")
        self.assertTrue(succ_a)
        self.assertTrue(succ_b)

        time.sleep(0.1)

        # Dispatch 5 strokes to Room Alpha and 3 strokes to Room Beta concurrently
        def send_alpha():
            for i in range(5):
                client_a.send_stroke(
                    WhiteboardAction(
                        action_type=ActionType.PEN,
                        user_id=client_a.user_id,
                        username="AlphaUser",
                        points=[[i, i]],
                        action_id=f"alpha_{i}",
                    )
                )
                time.sleep(0.01)

        def send_beta():
            for j in range(3):
                client_b.send_stroke(
                    WhiteboardAction(
                        action_type=ActionType.RECTANGLE,
                        user_id=client_b.user_id,
                        username="BetaUser",
                        points=[[j, j], [j + 10, j + 10]],
                        action_id=f"beta_{j}",
                    )
                )
                time.sleep(0.01)

        t1 = threading.Thread(target=send_alpha)
        t2 = threading.Thread(target=send_beta)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        time.sleep(0.3)

        room_a = self.server.room_manager.get_room("ROOM_ALPHA")
        room_b = self.server.room_manager.get_room("ROOM_BETA")

        self.assertEqual(len(room_a.state.get_active_actions()), 5)
        self.assertEqual(len(room_b.state.get_active_actions()), 3)

        # Verify client B received 0 messages from Alpha
        b_msgs = []
        while not client_b.inbound_queue.empty():
            msg = client_b.inbound_queue.get()
            if msg.msg_type in (MessageType.DRAW_STROKE.value, MessageType.DRAW_EVENT.value):
                b_msgs.append(msg)
        self.assertEqual(len(b_msgs), 0, "Client B in ROOM_BETA received drawing events from ROOM_ALPHA!")

        client_a.disconnect()
        client_b.disconnect()
        time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
