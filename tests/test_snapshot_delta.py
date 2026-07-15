import unittest

from snapshot_delta import apply_snapshot_delta, build_snapshot_delta


class SnapshotDeltaTests(unittest.TestCase):
    def test_round_trips_nested_changes_and_removals(self) -> None:
        before = {"at": 1, "totals": {"a": {"ms": 10}, "b": {"ms": 4}}, "items": [1]}
        after = {"at": 2, "totals": {"a": {"ms": 13}, "c": {"ms": 1}}, "items": [1, 2]}
        delta = build_snapshot_delta(before, after)
        self.assertEqual(apply_snapshot_delta(before, delta), after)

    def test_initial_delta_can_create_the_entire_snapshot(self) -> None:
        after = {"at": 1, "totals": {}}
        self.assertEqual(apply_snapshot_delta(None, build_snapshot_delta(None, after)), after)
