"""U2-5: 흐름 잇기(앞 대화 나눠 부르기·메인 할 일 상태), 바꾼 것 보기, 입력 칸, 이미지 입력."""

import json
import time
import unittest
from pathlib import Path

from stewart.cli import resolve
from stewart.hubmain import HubMain
from stewart.record import Recorder
from stewart.web import WebApp
from tests.test_hub import msg
from tests.test_hub3 import MainBase


class TFlow(MainBase):
    def reopen_with(self, history):
        from stewart import hub as H
        self.main.close()
        profile, backend = resolve(str(self.tmp / "main.toml"), "fake", str(self.wd))
        self.hub = H.Hub(self.tmp / "hub-f", self.tmp / "runs")
        self.main = HubMain(self.hub, profile, backend, Recorder(self.tmp / "runs", "hub-main"), mode="edits",
                            history=history, resumed_from="앞 기록")
        self.addCleanup(self.main.close)
        return WebApp(self.main, self.hub, "tok", port=0)

    def test_prior_and_paging(self):
        hist = [{"role": "system", "content": "s"}]
        for i in range(120):
            hist += [{"role": "user", "content": f"앞 말 {i}"}, {"role": "assistant", "content": f"앞 답 {i}"}]
        web = self.reopen_with(hist)
        self.addCleanup(web.httpd.server_close)
        self.r.scripts["MAIN"] = [msg("새 답")]
        self.main.send("새 말")
        self.assertTrue(self.main.wait_idle())
        p = web.session_conversation("main", prior=True, limit=80)
        self.assertEqual(p["total"], 240)                                   # 앞 대화만(새 말은 빠짐)
        self.assertEqual((p["start"], len(p["items"])), (160, 80))
        self.assertEqual(p["items"][-1]["text"], "앞 답 119")
        q = web.session_conversation("main", prior=True, before=p["start"], limit=80)
        self.assertEqual((q["start"], q["items"][0]["text"]), (80, "앞 말 40"))
        full = web.session_conversation("main")
        self.assertGreater(full["total"], 240)                              # 기본은 앞 + 지금
        self.assertIn("새 답", [x["text"] for x in full["items"]])

    def test_state_todos(self):
        self.r.scripts["MAIN"] = [msg(calls=[("todo_write", {"items": [{"content": "읽기", "status": "done"},
                                                                         {"content": "고치기", "status": "in_progress"}]})]),
                                  msg("하는 중")]
        (self.tmp / "main.toml").write_text('name = "main"\ntools = ["read_file", "todo_write"]\nmax_steps = 10\n'
                                            'system = "MAIN 지시"\n', encoding="utf-8")
        web = self.reopen_with(None)
        self.addCleanup(web.httpd.server_close)
        self.main.send("해")
        self.assertTrue(self.main.wait_idle())
        st = self.main.state()
        self.assertEqual([t["status"] for t in st["todos"]], ["done", "in_progress"])
        self.assertEqual(st["main_run"], str(self.main.sess.rec.dir))



# ── 2) 바꾼 것 보기 ──
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from stewart import chat as C, engine, images as IMG, tools as T
from stewart.config import Backend, Profile
from tests.test_cloud import FakeUI

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (3).to_bytes(4, "big") + (2).to_bytes(4, "big")
       + b"\x08\x02\x00\x00\x00" + b"\x00" * 40)


class TDiffView(unittest.TestCase):
    def test_diff_stat_and_main_ui(self):
        d = C.make_diff("a\nb\n", "a\nc\nd\n", "x.py", max_lines=C.DIFF_KEEP)
        self.assertEqual(C.diff_stat(d), {"path": "x.py", "plus": 2, "minus": 1})
        from stewart import hub as H
        from stewart.hubmain import HubMainUI
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        hub = H.Hub(tmp / "hub", tmp / "runs")
        hub.register_main(tmp, "edits")
        ui = HubMainUI(hub)
        ui.diff(d)
        ui.turn_files(["x.py"])
        self.assertEqual((ui.log[-2]["kind"], ui.log[-2]["stat"]["plus"]), ("diff", 2))
        self.assertIn("이번 차례에 바뀐 파일 1개: x.py", ui.log[-1]["text"])

    def test_args_summary_long_and_append(self):
        from stewart.worker import args_summary
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        env = T.ToolEnv(workdir=tmp, shell_timeout_s=5)
        big = "".join(f"줄 {i}\n" for i in range(300))
        d = args_summary(env, "write_file", {"path": "a.txt", "content": big})["diff"]
        self.assertGreater(len(d), 300)                                    # 40줄에서 자르지 않는다(화면이 접는다)
        (tmp / "b.txt").write_text("앞\n")
        d2 = args_summary(env, "write_file", {"path": "b.txt", "content": "뒤\n", "append": True})["diff"]
        self.assertTrue(any(l == "+뒤" for l in d2) and not any(l == "-앞" for l in d2))   # 이어 쓰기는 앞을 지우지 않는다


class TFiles(unittest.TestCase):
    def test_file_names_skip_secret_and_outside(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        wd = tmp / "w"
        (wd / "src").mkdir(parents=True)
        (wd / "src" / "app.py").write_text("x")
        (wd / ".env").write_text("SECRET=1")
        (tmp / "out.txt").write_text("밖")
        os.symlink(tmp / "out.txt", wd / "link.txt")

        class M:
            class sess:
                env = T.ToolEnv(workdir=wd, shell_timeout_s=5)
        w = WebApp.__new__(WebApp)
        w.accounts = None
        w.main = M
        names = w.file_names("")
        self.assertIn("src/app.py", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("link.txt", names)
        self.assertEqual(w.file_names("APP"), ["src/app.py"])


# ── 이미지 ──
class Sink:
    """가짜 백엔드: 받은 요청 본문을 모으고 답 하나를 준다."""

    def __init__(self, answers):
        self.bodies, self.answers, me = [], list(answers), self

        class Hd(BaseHTTPRequestHandler):
            def do_POST(self):
                me.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                m = me.answers.pop(0) if me.answers else {"role": "assistant", "content": "끝"}
                b = json.dumps({"choices": [{"message": m, "finish_reason": "stop"}], "usage": {}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TImages(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "w").mkdir()
        (self.tmp / "pic.png").write_bytes(PNG)
        p = mock.patch.dict(os.environ, {"STEWART_SERVED_CONTEXT": "0"})
        p.start()
        self.addCleanup(p.stop)

    def bk(self, url, vision=True):
        return Backend("v", url, "m", "q", 100000, vision=vision)

    def test_sniff_check(self):
        self.assertEqual(IMG.sniff(PNG), "image/png")
        self.assertEqual(IMG.dims(PNG, "image/png"), (3, 2))
        self.assertEqual(IMG.sniff(b"\xff\xd8\xff\xe0xx"), "image/jpeg")
        self.assertEqual(IMG.sniff(b"GIF89a" + b"\x00" * 8), "image/gif")
        self.assertEqual(IMG.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")
        for bad, why in ((b"hello", "PNG"), (b"", "빈")):
            with self.assertRaises(IMG.ImageError) as cm:
                IMG.check(bad)
            self.assertIn(why, str(cm.exception))
        with mock.patch.dict(os.environ, {"STEWART_IMAGE_MAX_BYTES": "10"}):
            with self.assertRaises(IMG.ImageError):
                IMG.check(PNG)
        with self.assertRaises(IMG.ImageError):
            IMG.check(PNG, ".env")

    def test_run_image_sent_not_logged_and_resumed(self):
        sink = Sink([])
        self.addCleanup(sink.close)
        from stewart.record import Recorder as R
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp / "w", max_steps=3)
        s = engine.run(p, self.bk(sink.url), "그림을 설명해", R(self.tmp / "runs", "t"), images=[str(self.tmp / "pic.png")])
        sent = sink.bodies[0]["messages"][-1]["content"]
        self.assertEqual(sent[0], {"type": "text", "text": sent[0]["text"]})
        self.assertTrue(sent[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        rd = Path(s["run_dir"])
        for f in ("events.jsonl", "messages.json"):
            self.assertNotIn("base64,", (rd / f).read_text())            # 기록에는 base64 가 없다(자리표만)
        self.assertIn(IMG.SCHEME, (rd / "messages.json").read_text())
        self.assertEqual(len(list((rd / "images").iterdir())), 1)
        # 이어 하기: 자리표를 다시 펼쳐 보낸다
        from stewart.resume import load_messages
        sink.bodies.clear()
        engine.run(p, self.bk(sink.url), "이어서", R(self.tmp / "runs", "t2"), history=load_messages(rd), resumed_from=str(rd))
        imgs = [x for m in sink.bodies[0]["messages"] if isinstance(m.get("content"), list) for x in m["content"]
                if x.get("type") == "image_url"]
        self.assertTrue(imgs and imgs[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_vision_false_refused_and_stripped(self):
        p = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp / "w", max_steps=3)
        from stewart.record import Recorder as R
        with self.assertRaises(IMG.ImageError):
            engine.run(p, self.bk("http://x/v1", vision=False), "x", R(self.tmp / "runs", "t"), images=[str(self.tmp / "pic.png")])
        msgs = [{"role": "user", "content": IMG.content("보라", [IMG.store(self.tmp, PNG, "image/png")])}]
        out = IMG.expand(msgs, vision=False)
        self.assertEqual(out[0]["content"][1]["type"], "text")           # 꺼진 백엔드에는 이미지 대신 글 자리표
        self.assertGreaterEqual(__import__("stewart.compact", fromlist=["est_tokens"]).est_tokens(json.dumps(msgs)),
                                IMG.IMAGE_TOKENS)

    def test_chat_turn_image_and_vision_off(self):
        sink = Sink([])
        self.addCleanup(sink.close)
        prof = Profile(name="chat", path=self.tmp / "p.toml", system="s", tools=["read_file"], workdir=self.tmp / "w", max_steps=3)
        ui = FakeUI()
        s = C.ChatSession(prof, self.bk(sink.url, vision=False), Recorder(self.tmp / "runs", "c"), ui, stream=False, mode="auto")
        part, _ = IMG.from_file(self.tmp / "pic.png", s.rec.dir)
        s.turn("봐", images=[part])
        self.assertIn("이미지를 받지 않는다", ui.out.getvalue())
        self.assertIsInstance(sink.bodies[-1]["messages"][-1]["content"], str)
        self.assertNotIn("view_image", [t["function"]["name"] for t in sink.bodies[-1].get("tools") or []])
        s.close()

    def test_view_image_tool(self):
        (self.tmp / "w" / "shot.png").write_bytes(PNG)
        sink = Sink([{"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "view_image", "arguments": '{"path": "shot.png"}'}}]}])
        self.addCleanup(sink.close)
        prof = Profile(name="t", path=self.tmp / "p.toml", system="s", tools=["read_file", "view_image"],
                       workdir=self.tmp / "w", max_steps=4)
        from stewart.record import Recorder as R
        engine.run(prof, self.bk(sink.url), "shot.png 을 봐라", R(self.tmp / "runs", "t"))
        self.assertIn("view_image", [t["function"]["name"] for t in sink.bodies[0]["tools"]])
        last = sink.bodies[1]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertTrue(last["content"][1]["image_url"]["url"].startswith("data:image/png"))
        env = T.ToolEnv(workdir=self.tmp / "w", shell_timeout_s=5)
        env.vision = True
        ok, r = T.read_file(env, "shot.png")
        self.assertTrue(ok and "이미지 파일" in r and "3×2" in r)
        env.vision = False
        self.assertFalse(T.view_image(env, "shot.png")[0])


class THubImages(MainBase):
    def test_main_send_images(self):
        self.main.sess.backend.vision = False
        self.assertEqual(self.main.send("봐", images=[("a.png", PNG)]), "no_vision")
        self.main.sess.backend.vision = True
        with self.assertRaises(IMG.ImageError):
            self.main.send("봐", images=[("a.txt", b"not image")])
        self.r.scripts["MAIN"] = [msg("봤다")]
        self.assertEqual(self.main.send("봐", images=[("a.png", PNG)]), "started")
        self.assertTrue(self.main.wait_idle())
        c = self.r.requests["MAIN"][-1]["messages"][-1]["content"]
        self.assertTrue(isinstance(c, list) and c[1]["image_url"]["url"].startswith("data:image/png"))


if __name__ == "__main__":
    unittest.main()
