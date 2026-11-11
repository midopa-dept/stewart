"""판 52 기억 둘째 조각: 차례 끝 모델로 알아채기(더하기·고치기·버리기), "기억하지 마", 프로젝트 기억은 그 대상에서만,
기억 자리 API(목록·고치기·지우기·더하기), 규칙 파일이 이긴다는 말."""

import http.client
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

from stewart import memory as MEM
from stewart.config import Profile
from tests.test_hub import msg, wait
from tests.test_hub3 import MainBase


def mem_at(tmp: Path, wd: Path) -> MEM.Memory:
    p = Profile(name="chat", path=tmp / "p.toml", system="s", tools=["read_file"], workdir=wd)
    with mock.patch.dict(os.environ, {"STEWART_MEMORY_DIR": str(tmp / "mem")}):
        return MEM.Memory(p, tmp / "runs" / "r1")


class TOps(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a").mkdir()
        (self.tmp / "b").mkdir()
        self.m = mem_at(self.tmp, self.tmp / "a")

    def test_extract_ops_filters(self):
        out = MEM.extract_ops('설명\n{"op":"add","kind":"feedback","text":"보고는 짧게 — 사용자가 길면 안 읽는다"}\n'
                              '{"op":"add","kind":"secret","text":"x"}\n{"op":"add","kind":"user","text":"토큰 ghp_' + "a" * 36 + '"}\n'
                              '```\n{"op":"drop","id":"abc"}\n{"op":"update","id":"abc","text":"새 글"}\nnot json')
        self.assertEqual([o["op"] for o in out], ["add", "drop", "update"])

    def test_apply_add_update_drop_deny(self):
        done = MEM.apply_ops(self.m, [{"op": "add", "kind": "feedback", "text": "앞으로는 보고를 파일로"}])
        it = done[0]["item"]
        self.assertEqual((it["scope"], it["source"]), ("user", "model"))
        MEM.apply_ops(self.m, [{"op": "update", "id": it["id"], "text": "앞으로는 보고를 파일로 + 한 줄 요약"}])
        self.assertEqual([x["text"] for x in self.m.remembered()], ["앞으로는 보고를 파일로 + 한 줄 요약"])
        MEM.apply_ops(self.m, [{"op": "drop", "id": it["id"]}])
        self.assertEqual(self.m.remembered(), [])
        self.assertTrue(self.m.deny(text="회의록을 남겨라"))
        self.assertIsNone(self.m.remember("feedback", "앞으로는 회의록을 남겨라"))          # 기억하지 말라 한 것
        x = self.m.remember("feedback", "앞으로는 숫자를 표로")
        self.assertTrue(self.m.deny(x["id"]))
        self.assertEqual(self.m.remembered(), [])

    def test_project_scope_only_in_its_place(self):
        MEM.apply_ops(self.m, [{"op": "add", "kind": "project", "text": "a 는 make check 로 시험한다"},
                               {"op": "add", "kind": "user", "text": "나는 짧은 답을 좋아한다"}])
        text_a, _ = self.m.inject()
        self.assertIn("make check", text_a)
        self.assertIn("규칙 파일이 이긴다", text_a)                                  # 손으로 쓴 규칙이 정본
        other = mem_at(self.tmp, self.tmp / "b")
        text_b, _ = other.inject()
        self.assertNotIn("make check", text_b)                                      # 다른 대상에는 안 들어감
        self.assertIn("짧은 답", text_b)                                             # 사용자 기억은 모든 프로젝트
        p = MEM.extract_prompt(self.m, ["보고는 짧게 해"], "알겠습니다")
        self.assertIn("기억하지 말라 한 것", p)
        self.assertIn("짧은 답", p)                                                  # 이미 기억한 것을 보인다


class THub(MainBase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"STEWART_MEMORY": "1", "STEWART_MEMORY_DIR": tempfile.mkdtemp(prefix="mem-")})   # 시험마다 따로
        p.start()
        self.addCleanup(p.stop)
        super().setUp()

    def test_turn_end_extract_and_api(self):
        self.r.scripts["MAIN"] = [msg("알겠습니다. 다음부터 그렇게 하겠습니다.")]
        self.r.scripts["WORKER"] = [msg('{"op":"add","kind":"feedback","text":"보고 끝에 열린 일을 한 줄로 적는다 — 사용자가 놓치지 않게"}')]
        self.main.send("보고 끝에는 열린 일을 꼭 한 줄로 정리해 줬으면 해")      # 규칙(앞으로·하지 마)으로는 안 잡히는 말
        self.assertTrue(self.main.wait_idle())
        got = wait(lambda: [m for m in self.main.state()["main_log"] if m["kind"] == "memory"], timeout=10)
        self.assertEqual(got[0]["mem_source"], "model")
        self.assertIn("열린 일을 한 줄로", got[0]["text"])
        req = [r for r in self.r.requests["WORKER"]][-1]
        self.assertNotIn("tools", req)                                              # 도구 없는 한 번
        self.assertLess(len(json.dumps(req["messages"], ensure_ascii=False)), 6000)  # 문맥을 다 보내지 않음
        # 기억 자리 API
        from stewart.web import WebApp, make_token
        tok = make_token(self.hub.dir)
        web = WebApp(self.main, self.hub, tok)
        web.serve()
        self.addCleanup(web.close)
        host = f"127.0.0.1:{web.port}"

        def req(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", web.port, timeout=10)
            c.request(method, path, body=body, headers={"Host": host, **(headers or {})})
            r = c.getresponse()
            d = r.read()
            c.close()
            return r, d
        r, _ = req("POST", "/login", urlencode({"token": tok}), {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://{host}"})
        ck = r.getheader("Set-Cookie").split(";")[0]
        import re
        _, page = req("GET", "/?ui=old", headers={"Cookie": ck})
        csrf = re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()
        good = {"Cookie": ck, "Origin": f"http://{host}", "X-Stewart-Token": csrf, "Content-Type": "application/json"}
        r, d = req("GET", "/api/memory", headers={"Cookie": ck})
        items = json.loads(d)["items"]
        self.assertEqual(len(items), 1)
        iid = items[0]["id"]
        self.assertEqual(req("POST", "/api/memory/update", json.dumps({"id": iid, "text": "보고 끝에 열린 일 한 줄"}), good)[0].status, 200)
        self.assertEqual(req("POST", "/api/memory/add", json.dumps({"text": "나는 표를 좋아한다", "kind": "user"}), good)[0].status, 200)
        self.assertEqual(req("POST", "/api/memory/deny", json.dumps({"id": iid}), good)[0].status, 200)
        r, d = req("GET", "/api/memory", headers={"Cookie": ck})
        d = json.loads(d)
        self.assertEqual([x["text"] for x in d["items"]], ["나는 표를 좋아한다"])
        self.assertEqual(d["deny"], ["보고 끝에 열린 일 한 줄"])

    def test_no_extract_when_model_down(self):
        self.main.watch.status = {**self.main.watch.status, "state": "down"}
        self.r.scripts["MAIN"] = [msg("네.")]
        self.main.send("보고 끝에는 열린 일을 꼭 정리해 줬으면 해")
        self.assertTrue(self.main.wait_idle())
        time.sleep(0.5)
        self.assertEqual(self.r.requests["WORKER"], [])                             # 서버가 꺼져 있으면 부르지 않는다


if __name__ == "__main__":
    unittest.main()
