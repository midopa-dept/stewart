"""판 148: 옛 판이 남긴 기록을 새 판이 이어 받는다(훑기 4 — 판 134 상태 위에서 새 판 켜기).

- 판 132–138 의 moderato 대화 기록은 meta 에 등급 이름이 없다 — run_start 앞의 think_effort(medium, source default)로 moderato 로 되살린다.
- 판 143 전 기록은 차례 끝에만 대화를 저장해, 차례 도중 끊긴(승인 대기 등) 사람 말이 messages.json 에 없다 — 대화에는 넣지 않고
  화면에 "다시 보내기" 칸으로만 보인다(옛 지시가 다시 실행되지 않게). 옛 기록·차례가 실제로 끊긴 때만.
"""

import json
import tempfile
import unittest
from pathlib import Path

from stewart import hubmain as HM
from stewart.resume import cut_turn_text, load_messages


def run_dir(events: list[dict], messages: list[dict] | None) -> Path:
    d = Path(tempfile.mkdtemp(prefix="oldrec-"))
    (d / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    if messages is not None:
        (d / "messages.json").write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    return d


START = {"kind": "run_start", "meta": {"profile": "work", "backend": {"name": "cloud"}}}


class TOldModerato(unittest.TestCase):
    def test_think_effort_before_run_start(self):
        d = run_dir([{"kind": "think_effort", "value": "medium", "source": "default", "note": None}, START], [])
        self.assertTrue(HM._old_moderato(d))

    def test_plain_old_record_is_not(self):
        self.assertFalse(HM._old_moderato(run_dir([START, {"kind": "think_effort", "value": "medium", "source": "default"}], [])))
        self.assertFalse(HM._old_moderato(run_dir([{"kind": "think_effort", "value": "low", "source": "tier"}, START], [])))
        self.assertFalse(HM._old_moderato(Path(tempfile.mkdtemp())))                         # 기록 없음


class TCutTurn(unittest.TestCase):
    """끊긴 말은 옛 기록·차례가 실제로 끊긴 때만 찾고, 대화에는 넣지 않는다(load_messages 는 그대로)."""
    SYS = {"role": "system", "content": "s"}
    MSGS = [SYS, {"role": "user", "content": "앞 말"}, {"role": "assistant", "content": "답"}]
    CUT = [START, {"kind": "user", "turn": 1, "content": "앞 말"}, {"kind": "turn_end"}, {"kind": "user", "turn": 2, "content": "끊긴 말"},
           {"kind": "ask", "tool": "run_shell"}]

    def test_old_cut_turn_found_not_inserted(self):
        d = run_dir(self.CUT, self.MSGS)
        self.assertEqual(cut_turn_text(d, self.MSGS), "끊긴 말")
        self.assertEqual(load_messages(d), self.MSGS)                                          # 대화에는 넣지 않는다

    def test_raw_text_preferred(self):
        ev = self.CUT[:3] + [{"kind": "user", "turn": 2, "content": "끊긴 말\n\n[엔진] 붙은 알림", "raw": "끊긴 말"}]
        self.assertEqual(cut_turn_text(run_dir(ev, self.MSGS), self.MSGS), "끊긴 말")             # 사람이 친 글 그대로

    def test_not_found(self):
        ended = self.CUT[:4] + [{"kind": "turn_end"}]
        self.assertIsNone(cut_turn_text(run_dir(ended, self.MSGS), self.MSGS))                  # 차례가 끝났다(압축으로 말이 접혀도)
        new = [{"kind": "run_start", "meta": {**START["meta"], "saves_turn_start": True}}] + self.CUT[1:]
        self.assertIsNone(cut_turn_text(run_dir(new, self.MSGS), self.MSGS))                    # 새 판 기록
        reset = self.CUT + [{"kind": "reset"}]
        self.assertIsNone(cut_turn_text(run_dir(reset, [self.SYS]), [self.SYS]))                # /clear 뒤
        env_head = self.MSGS + [{"role": "user", "content": "[엔진이 붙인 환경 정보]\n…\n\n끊긴 말"}]
        self.assertIsNone(cut_turn_text(run_dir(self.CUT, env_head), env_head))                # 환경 머리가 붙은 말로 들어 있다
        img = self.MSGS + [{"role": "user", "content": [{"type": "text", "text": "끊긴 말"}, {"type": "image_url", "image_url": {"url": "x"}}]}]
        self.assertIsNone(cut_turn_text(run_dir(self.CUT, img), img))                          # 그림 든 말로 들어 있다
        self.assertIsNone(cut_turn_text(Path(tempfile.mkdtemp()), self.MSGS))                  # 기록 없음


class THubCutCard(unittest.TestCase):
    """허브: 옛 기록(끊긴 차례)을 이어 받으면 화면에 다시 보내기 칸만 — 모델을 스스로 돌리지 않고, 다음 말에도 끊긴 말이 섞이지 않는다."""

    def test_card_no_autorun(self):
        import time
        from tests.test_hub import msg
        from tests.test_realuse_shape import FakeModel, Hub, make_user_shape
        root = Path(tempfile.mkdtemp(prefix="cutcard-"))
        m = FakeModel(route=lambda b: msg(calls=[("run_shell", {"command": "touch x"})]) if "카드" in json.dumps(b["messages"][-1:], ensure_ascii=False) else msg("네"))
        self.addCleanup(m.close)
        env = make_user_shape(root, m.url)
        h = Hub(root, env)
        h.post("/api/main", {"text": "카드 띄울 말"})
        self.assertTrue(h.wait(lambda: next((q for q in h.get("/api/state")["queue"] if q.get("kind") == "confirm"), None), 20))
        h.close()
        run = next(p for p in (root / "home").rglob("events.jsonl") if "카드 띄울 말" in p.read_text(encoding="utf-8"))
        lines = run.read_text(encoding="utf-8").splitlines()   # 옛 판 꼴로: 표지 없애고, 차례 시작 저장도 없던 것처럼
        lines = [ln.replace('"saves_turn_start": true, ', "").replace(', "saves_turn_start": true', "") for ln in lines]
        run.write_text("\n".join(lines) + "\n", encoding="utf-8")
        mj = run.parent / "messages.json"
        msgs = json.loads(mj.read_text(encoding="utf-8"))
        mj.write_text(json.dumps([x for x in msgs if "카드 띄울 말" not in json.dumps(x, ensure_ascii=False)], ensure_ascii=False), encoding="utf-8")
        n0 = len(m.requests)
        h = Hub(root, env)
        self.addCleanup(h.close)
        time.sleep(2)
        self.assertEqual([b for b in m.requests[n0:] if b.get("tools")], [])                       # 스스로 돌리지 않는다
        log = h.get("/api/state")["main_log"]
        card = [x for x in log if x.get("cls") == "cut_turn"]
        self.assertTrue(card, [(x.get("kind"), str(x.get("text"))[:60]) for x in log[-5:]])
        self.assertEqual(card[-1]["retry"], "카드 띄울 말")
        h.post("/api/main", {"text": "다른 말"})
        self.assertTrue(h.wait(lambda: not h.get("/api/state")["busy"]))
        body = next(b for b in m.requests[n0:] if b.get("tools"))
        self.assertNotIn("카드 띄울 말", json.dumps(body["messages"], ensure_ascii=False))           # 다음 말에도 섞이지 않는다


if __name__ == "__main__":
    unittest.main()
