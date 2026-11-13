"""
Unit Tests for Whiteboard State, Collaborative Undo/Redo, and File Persistence.
"""

import os
import tempfile
import unittest

from state.action import ActionType, WhiteboardAction
from state.whiteboard_state import WhiteboardState


class TestWhiteboardState(unittest.TestCase):

    def setUp(self):
        self.state = WhiteboardState()

    def test_add_action(self):
        act = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id="u1",
            username="Alice",
            color="#FF0000",
            width=3,
            points=[[10, 10], [20, 20]],
        )
        self.state.add_action(act)
        self.assertEqual(len(self.state.get_all_actions()), 1)
        self.assertEqual(len(self.state.get_active_actions()), 1)

    def test_user_centric_undo_redo(self):
        # Alice draws
        act_alice = WhiteboardAction(
            action_type=ActionType.PEN,
            user_id="u_alice",
            username="Alice",
            points=[[10, 10], [20, 20]],
        )
        self.state.add_action(act_alice)

        # Bob draws
        act_bob = WhiteboardAction(
            action_type=ActionType.RECTANGLE,
            user_id="u_bob",
            username="Bob",
            points=[[50, 50], [100, 100]],
        )
        self.state.add_action(act_bob)

        self.assertEqual(len(self.state.get_active_actions()), 2)

        # Alice clicks Undo -> Alice's action should be undone, NOT Bob's!
        undone = self.state.undo_action(user_id="u_alice")
        self.assertIsNotNone(undone)
        self.assertEqual(undone.action_id, act_alice.action_id)
        self.assertTrue(act_alice.is_undone)
        self.assertFalse(act_bob.is_undone)
        self.assertEqual(len(self.state.get_active_actions()), 1)
        self.assertEqual(self.state.get_active_actions()[0].user_id, "u_bob")

        # Alice clicks Redo -> Alice's action should be restored
        redone = self.state.redo_action(user_id="u_alice")
        self.assertIsNotNone(redone)
        self.assertEqual(redone.action_id, act_alice.action_id)
        self.assertFalse(act_alice.is_undone)
        self.assertEqual(len(self.state.get_active_actions()), 2)

    def test_clear_canvas(self):
        self.state.add_action(
            WhiteboardAction(action_type=ActionType.PEN, user_id="u1", username="Alice", points=[[0, 0]])
        )
        self.state.add_action(
            WhiteboardAction(action_type=ActionType.LINE, user_id="u2", username="Bob", points=[[1, 1], [2, 2]])
        )
        self.assertEqual(len(self.state.get_active_actions()), 2)

        clear_act = self.state.clear(user_id="u1", username="Alice")
        self.assertEqual(clear_act.action_type, ActionType.CLEAR.value)
        # Only CLEAR action is active
        self.assertEqual(len(self.state.get_active_actions()), 1)
        self.assertEqual(self.state.get_active_actions()[0].action_type, ActionType.CLEAR.value)

    def test_json_file_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test_session.json")

            # Add two actions
            act1 = WhiteboardAction(
                action_type=ActionType.PEN,
                user_id="u1",
                username="Alice",
                points=[[10, 15], [20, 25]],
                color="#E74C3C",
                width=5,
            )
            act2 = WhiteboardAction(
                action_type=ActionType.CIRCLE,
                user_id="u2",
                username="Bob",
                points=[[50, 50], [80, 80]],
                color="#3498DB",
                width=2,
            )
            self.state.add_action(act1)
            self.state.add_action(act2)

            # Save
            self.state.save_to_file(file_path)
            self.assertTrue(os.path.exists(file_path))

            # Load into fresh state
            new_state = WhiteboardState()
            new_state.load_from_file(file_path)

            loaded_actions = new_state.get_all_actions()
            self.assertEqual(len(loaded_actions), 2)
            self.assertEqual(loaded_actions[0].action_type, ActionType.PEN.value)
            self.assertEqual(loaded_actions[0].color, "#E74C3C")
            self.assertEqual(loaded_actions[1].action_type, ActionType.CIRCLE.value)
            self.assertEqual(loaded_actions[1].points, [[50, 50], [80, 80]])

    def test_image_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            img_path = os.path.join(tmpdir, "test_whiteboard.png")

            # Add a drawing and a rectangle
            self.state.add_action(
                WhiteboardAction(
                    action_type=ActionType.PEN,
                    user_id="u1",
                    username="Alice",
                    points=[[10, 10], [50, 50], [90, 20]],
                    color="#FF0000",
                    width=4,
                )
            )
            self.state.add_action(
                WhiteboardAction(
                    action_type=ActionType.RECTANGLE,
                    user_id="u2",
                    username="Bob",
                    points=[[100, 100], [200, 200]],
                    color="#0000FF",
                    width=2,
                )
            )

            success = self.state.export_to_image(img_path, width=400, height=300)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(img_path))
            self.assertGreater(os.path.getsize(img_path), 500)

    def test_structured_drawing_data_and_unique_stroke_ids(self):
        """Verifies structured drawing representation and UUID uniqueness."""
        actions = []
        stroke_ids = set()
        for i in range(100):
            act = WhiteboardAction(
                action_type=ActionType.PEN,
                user_id=f"user_{i % 5}",
                username=f"Student_{i % 5}",
                points=[[i, i + 1], [i + 2, i + 3]],
                color="#000000",
                width=3,
            )
            actions.append(act)
            stroke_ids.add(act.action_id)
            self.state.add_action(act)

        # 100 unique stroke IDs generated
        self.assertEqual(len(stroke_ids), 100)
        self.assertEqual(len(self.state.get_active_actions()), 100)

        # Structured serialization contains all required fields
        serialized = actions[0].to_dict()
        self.assertIn("action_id", serialized)
        self.assertIn("action_type", serialized)
        self.assertIn("user_id", serialized)
        self.assertIn("color", serialized)
        self.assertIn("width", serialized)
        self.assertIn("points", serialized)
        self.assertIn("timestamp", serialized)


if __name__ == "__main__":
    unittest.main()
