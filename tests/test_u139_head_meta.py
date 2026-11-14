"""판 139: 기록 앞쪽에서 run_start meta 찾기 — 첫 줄이 다른 사건이어도(판 132–138 의 대화 기록), 없으면 {}."""

import json
import tempfile
import unittest
from pathlib import Path

from stewart.record import head_meta


class THeadMeta(unittest.TestCase):
    def write(self, *events):
        d = Path(tempfile.mkdtemp())
        (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        return d

    def test_finds_run_start_after_other_event(self):
        d = self.write({"kind": "think_effort", "value": "medium"}, {"kind": "run_start", "meta": {"profile": "talk", "tier": "moderato"}})
        self.assertEqual(head_meta(d), {"profile": "talk", "tier": "moderato"})

    def test_missing_or_broken(self):
        self.assertEqual(head_meta(Path(tempfile.mkdtemp())), {})
        d = self.write({"kind": "x"})
        self.assertEqual(head_meta(d), {})
        (d / "events.jsonl").write_text("not json\n" + json.dumps({"kind": "run_start", "meta": {"a": 1}}) + "\n", encoding="utf-8")
        self.assertEqual(head_meta(d), {"a": 1})
        self.assertEqual(head_meta(self.write({"meta": {"b": 2}})), {"b": 2})                     # kind 없는 옛 꼴(첫 줄 meta)


if __name__ == "__main__":
    unittest.main()
