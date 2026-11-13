"""판 120: 한 허브에 대화·코드 칸 — new_chat(kind) 가 종류마다 다른 프로필(대화: 대화 폴더·셸 없음·역할 지시 없음)로 칸을 열고,
종류는 만들 때 정해져 바뀌지 않음(갈래는 같은 종류), 대화 목록에 kind·tier, 지난 대화 찾기는 두 폴더를 함께(종류 꼬리표), 웹 API kind."""

import dataclasses
import json
from pathlib import Path

from stewart.cli import resolve
from stewart.config import load_profile
from stewart.hubmain import HUB_MAIN_NOTE, kind_of, with_role
from tests import test_hub_ui as HU


class TKinds(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def setUp(self):
        super().setUp()
        talk_dir = self.tmp / "talk"
        talk_dir.mkdir()

        def resolver(kind):
            if kind == "talk":
                p = dataclasses.replace(load_profile("talk"), workdir=talk_dir)
                _, b = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
                return p, b
            return resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        self.main.kind_resolver = resolver
        self.talk_dir = talk_dir

    def test_kind_chat(self):
        self.assertEqual(self.main.kind, "code")
        c = self.main.new_chat("fresh", kind="talk")
        self.assertEqual(c.kind, "talk")
        self.assertEqual(Path(c.sess.profile.workdir), self.talk_dir)              # 대화 폴더
        self.assertNotIn("run_shell", c.sess.tool_names)                             # 프로젝트 셸 없음
        self.assertNotIn(HUB_MAIN_NOTE[:20], c.sess.messages[0]["content"])           # 허브 역할 지시 없음
        self.assertIn("코드 대화를 새로 열어 달라", c.sess.messages[0]["content"])     # 넘기기 대신 한 줄
        code = self.main.new_chat("fresh")                                           # 기본은 메인 종류
        self.assertEqual(code.kind, "code")
        fork = self.main.new_chat("fork", source=c.name)
        self.assertEqual(fork.kind, "talk")                                          # 갈래는 같은 종류
        kinds = {x["name"]: x["kind"] for x in self.main.state()["chats"]}
        self.assertEqual((kinds["main"], kinds[c.name], kinds[code.name]), ("code", "talk", "code"))
        reg = self.hub.registry()["sessions"][c.name]
        self.assertEqual(reg["kind"], "talk")
        with self.assertRaises(ValueError):
            self.main.new_chat("fresh", kind="other")

    def test_api_and_search_dirs(self):
        code, r = self.post("/api/chat/new", how="fresh", kind="talk")
        self.assertEqual((code, r["kind"]), (200, "talk"))
        dirs = dict((k, Path(p)) for p, k in self.main.search_dirs())
        self.assertEqual(dirs["talk"], self.talk_dir)
        self.assertEqual(dirs["code"], Path(self.main._profile.workdir))

    def test_helpers(self):
        self.assertEqual(kind_of(load_profile("talk")), "talk")
        self.assertEqual(kind_of(load_profile("chat")), "code")
        p = with_role(load_profile("chat"))
        self.assertIn(HUB_MAIN_NOTE[:20], p.system)
        self.assertEqual(with_role(p).system, p.system)                              # 두 번 붙이지 않는다
        self.assertNotIn(HUB_MAIN_NOTE[:20], with_role(load_profile("talk")).system)


class TPastKinds(HU.TWebSessions):
    locals().update({k: None for k in dir(HU.TWebSessions) if k.startswith("test")})

    def test_past_search_kinds(self):
        from stewart.trash import past_search
        runs = self.tmp / "pruns"
        for i, (wd, word, prof) in enumerate(((self.tmp / "a", "사과", "chat"), (self.tmp / "b", "사과나무", "talk"))):
            d = runs / f"2026111{i}-hub-main"
            d.mkdir(parents=True)
            (d / "events.jsonl").write_text(json.dumps({"meta": {"workdir": str(wd), "started": "x", "profile": prof}}) + "\n", encoding="utf-8")
            (d / "messages.json").write_text(json.dumps([{"role": "user", "content": f"{word} 이야기"}]), encoding="utf-8")
        hits = past_search(runs, [(self.tmp / "a", "code"), (self.tmp / "b", "talk")], "사과")
        self.assertEqual(sorted(h["kind"] for h in hits), ["code", "talk"])           # 종류는 기록의 프로필에서
        self.assertEqual(len(past_search(runs, self.tmp / "a", "사과")), 2)           # 판 124.2: 폴더로 거르지 않는다


if __name__ == "__main__":
    import unittest
    unittest.main()
