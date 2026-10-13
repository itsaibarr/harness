import json
import tempfile
import unittest
from pathlib import Path

from route_task import events


class RunDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_id_is_sortable_slugged_and_unique(self):
        a = events.new_run_id("Add Retry: fetch client!")
        b = events.new_run_id("Add Retry: fetch client!")
        self.assertRegex(a, r"^\d{8}-\d{6}-[0-9a-f]{4}-add-retry-fetch-client$")
        self.assertNotEqual(a, b)

    def test_state_roundtrip_and_update_merges(self):
        r = events.RunDir.create(self.root, "r1", {"status": "running", "stage": "intake"})
        r.update(stage="worker", attempts={"worker": 1})
        s = events.RunDir.open(self.root, "r1").state
        self.assertEqual(s["status"], "running")
        self.assertEqual(s["stage"], "worker")
        self.assertEqual(s["attempts"], {"worker": 1})

    def test_state_write_is_atomic_no_tmp_left(self):
        r = events.RunDir.create(self.root, "r1", {"status": "running"})
        r.update(status="done")
        self.assertEqual(sorted(p.name for p in r.path.iterdir()), ["events.jsonl", "state.json"])

    def test_events_are_appended_as_jsonl_with_timestamps(self):
        r = events.RunDir.create(self.root, "r1", {})
        r.log("stage_start", stage="worker")
        r.log("stage_end", stage="worker", ok=True)
        lines = [json.loads(l) for l in (r.path / "events.jsonl").read_text().splitlines()]
        self.assertEqual([l["type"] for l in lines], ["run_created", "stage_start", "stage_end"])
        self.assertTrue(all("ts" in l for l in lines))
        self.assertEqual(lines[2]["ok"], True)

    def test_open_missing_run_is_error(self):
        with self.assertRaises(FileNotFoundError):
            events.RunDir.open(self.root, "nope")


if __name__ == "__main__":
    unittest.main()
