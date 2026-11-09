"""지난 실행 기록의 web_search 결과 손질(stewart.scrub_records): 보기만이 기본, --apply 로 바꿈, 내용은 찍지 않음."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from stewart import scrub_records as S
from stewart.record import Recorder

SECRET = "비밀검색결과ZZZ https://secret.example/q"


def make_run(root: Path, name: str):
    d = root / name
    d.mkdir(parents=True)
    tool_msg = {"role": "tool", "tool_call_id": "c1", "name": "web_search", "content": SECRET}
    other = {"role": "tool", "tool_call_id": "c2", "name": "read_file", "content": "파일 내용 그대로"}
    nameless = {"role": "tool", "tool_call_id": "c3", "content": SECRET + " 이름 없음"}
    asst = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{\"query\": \"q\"}"}},
        {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "c3", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]}
    ev = [{"kind": "run_start", "meta": {}, "messages": [{"role": "system", "content": "s"}]},
          {"kind": "web_search", "step": 1, "query": "q", "urls": ["https://secret.example/q", "https://b.example"]},
          {"kind": "step", "step": 1, "new_messages": [asst, tool_msg, other, nameless],
           "response": {"tool_calls": [{"name": "web_search", "result": SECRET, "ok": True},
                                       {"name": "read_file", "result": "파일 내용 그대로", "ok": True}]}},
          {"kind": "run_end", "summary": {"web_searches": [{"query": "q", "urls": ["https://secret.example/q"]}],
                                          "web_result_urls": ["https://secret.example/q"]}}]
    (d / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n", encoding="utf-8")
    (d / "messages.json").write_text(json.dumps([{"role": "system", "content": "s"}, asst, tool_msg, other, nameless],
                                                ensure_ascii=False), encoding="utf-8")
    (d / "summary.json").write_text(json.dumps({"web_searches": [{"query": "q", "urls": ["https://secret.example/q"]}],
                                                "web_result_urls": ["https://secret.example/q"], "steps": 3}, ensure_ascii=False))
    (d / "transcript.md").write_text("## 단계 1\n**툴콜** `web_search` {\"query\": \"q\"}\n→ 성공 (0.10초)\n\n```\n" + SECRET +
                                     "\n```\n**툴콜** `read_file` {}\n→ 성공 (0.01초)\n\n```\n파일 내용 그대로\n```\n", encoding="utf-8")
    (d / "plan.json").write_text(json.dumps({"items": [{"src": "a", "urls": ["https://keep.example"]}]}))
    os.chmod(d / "events.jsonl", 0o640)
    return d


class TScrub(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.new = make_run(self.root, "20261110-101010-organize-private")
        self.grouped = make_run(self.root / "queue-x", "20261111-090000-code")
        self.old = make_run(self.root, "20261108-235959-organize-private")

    def run_main(self, *extra):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = S.main(["--runs-dir", str(self.root), "--since", "2026-11-09", *extra])
        return rc, buf.getvalue()

    def snapshot(self, d):
        return {f.name: f.read_bytes() for f in d.iterdir()}

    def test_dry_run_default_changes_nothing_and_prints_no_content(self):
        before = {d: self.snapshot(d) for d in (self.new, self.grouped, self.old)}
        rc, out = self.run_main("--list-out", str(self.root / "list.tsv"))
        self.assertEqual(rc, 0)
        for d, snap in before.items():
            self.assertEqual(self.snapshot(d), snap)                           # 아무것도 안 바뀜
        self.assertNotIn("비밀검색결과", out)
        self.assertNotIn("secret.example", out)
        self.assertIn("20261110-101010-organize-private\t", out)
        self.assertIn("queue-x/20261111-090000-code\t", out)
        self.assertNotIn("20261108", out)                                       # --since 이전은 대상 밖
        self.assertIn("보기만", out)
        self.assertTrue((self.root / "list.tsv").read_text().startswith("실행 폴더\t"))

    def test_apply_scrubs_and_is_idempotent(self):
        rc, out = self.run_main("--apply")
        self.assertEqual(rc, 0)
        for d in (self.new, self.grouped):
            for name in S.FILES:
                t = (d / name).read_text(encoding="utf-8")
                self.assertNotIn("비밀검색결과", t, (d.name, name))
                self.assertNotIn("secret.example", t, (d.name, name))
            self.assertIn("파일 내용 그대로", (d / "transcript.md").read_text(encoding="utf-8"))   # 다른 도구 결과는 그대로
            self.assertIn("파일 내용 그대로", (d / "messages.json").read_text(encoding="utf-8"))
            self.assertIn("keep.example", (d / "plan.json").read_text())                          # plan.json 그대로
            self.assertEqual(oct((d / "events.jsonl").stat().st_mode & 0o777), oct(0o640))       # 권한 그대로
            json.loads((d / "summary.json").read_text())
            for ln in (d / "events.jsonl").read_text().splitlines():
                json.loads(ln)
            self.assertIn("약관상 기록에 남기지 않음(지난 기록 손질)", (d / "messages.json").read_text(encoding="utf-8"))
        self.assertIn("비밀검색결과", (self.old / "events.jsonl").read_text(encoding="utf-8"))  # 이전 실행은 그대로
        rc, out2 = self.run_main("--apply")
        self.assertIn("실행 0개", out2)                                          # 다시 돌려도 바뀌는 것 없음

    def test_backup_needs_apply(self):
        rc, _ = self.run_main("--backup-dir", str(self.root / "bk"))
        self.assertEqual(rc, 2)
        rc, _ = self.run_main("--apply", "--backup-dir", str(self.root / "bk"))
        self.assertIn("비밀검색결과", (self.root / "bk" / "20261110-101010-organize-private" / "events.jsonl").read_text(encoding="utf-8"))

    def test_real_recorder_output_shape(self):
        """실제 Recorder 가 쓴 대화록 꼴(단계·툴콜·결과 칸)을 잡는다."""
        rec = Recorder(self.root / "real", "t")
        rec.md_step(1, {"elapsed_s": 0.1, "content": "", "tool_calls": [
            {"name": "web_search", "raw_arguments": "{\"query\": \"q\"}", "ok": True, "elapsed_s": 0.1, "result": SECRET}]})
        rec.close({"stop_reason": "completed", "steps": 1, "elapsed_s": 1, "model_time_s": 1, "tool_calls": 1, "tool_errors": 0,
                   "parse_failures": 0, "format_failures": 0, "completion_tokens": 0, "last_prompt_tokens": 0,
                   "gen_tok_per_s": None, "prompt_tok_per_s": None}, None)
        new, c = S.scrub_file(rec.dir / "transcript.md")
        self.assertEqual(c.results, 1)
        self.assertNotIn("비밀검색결과", new)


if __name__ == "__main__":
    unittest.main()
