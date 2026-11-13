"""판 125 조각 1: 등급 표 한눈에(tierview·`stewart tiers`) — 기본 허브는 표에 없는 등급이 기본 모델, rubato 는 설정 없음.
따로 도는 허브는 rubato 만 연결(나머지 연결 없음). 바깥 등급은 사람만·돈 상한 기본값, 키는 있음·없음만(값은 내지 않음)."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from stewart import cli
from stewart import tierview as TV

LOCAL = '''default = "big"
[backends.big]
base_url = "http://127.0.0.1:9/v1"
model = "big-model"
[backends.small]
base_url = "http://127.0.0.1:9/small/v1"
model = "small-model"
concurrency = 2
[backends.outside]
base_url = "https://api.example.invalid/v1"
model = "outside-model"
provider = "Example"
api_key_env = "TV_TEST_KEY"
price = { input = 1.0, output = 2.0 }
[tiers.presto]
backend = "small"
[tiers.rubato]
backend = "outside"
max_usd_day = 5
max_usd_chat = 0
'''

INSTANCE = '''default = "outside"
[backends.outside]
base_url = "https://api.example.invalid/v1"
model = "outside-model"
provider = "Example"
api_key_env = "TV_TEST_KEY"
'''


class TRows(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        os.environ.pop("TV_TEST_KEY", None)
        os.environ.pop("STEWART_INSTANCE", None)

    def write(self, text):
        p = self.tmp / "backends.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def by(self, rows):
        return {r["tier"]: r for r in rows}

    def test_main_hub(self):
        rows, _ = TV.load(self.write(LOCAL))
        r = self.by(rows)
        self.assertEqual([x["tier"] for x in rows], list(TV.CANON))
        self.assertEqual((r["presto"]["backend"], r["presto"]["source"]), ("small", "표"))
        self.assertEqual((r["moderato"]["backend"], r["moderato"]["source"]), ("big", "기본 모델"))
        self.assertFalse(r["presto"]["manual"])
        o = r["rubato"]
        self.assertTrue(o["external"] and o["manual"])                       # 바깥 등급은 기본이 사람만
        self.assertEqual((o["key"], o["status"]), ("없음", "nokey"))
        self.assertEqual(o["caps"], {"usd_day": 5.0, "usd_chat": None})       # 0 = 끔
        with mock.patch.dict(os.environ, {"TV_TEST_KEY": "secret-value"}):
            rows2, _ = TV.load(self.write(LOCAL))
        o2 = self.by(rows2)["rubato"]
        self.assertEqual(o2["key"], "있음")
        self.assertNotIn("secret-value", json.dumps(rows2, ensure_ascii=False))   # 값은 어디에도 없다

    def test_rubato_unset_without_table(self):
        rows, _ = TV.load(self.write('default = "big"\n[backends.big]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\n'))
        r = self.by(rows)
        self.assertEqual((r["rubato"]["connected"], r["rubato"]["status"]), (False, "unset"))
        self.assertEqual(r["largo"]["source"], "기본 모델")

    def test_instance_is_rubato_only(self):
        p = self.write(INSTANCE)
        with mock.patch.dict(os.environ, {"STEWART_INSTANCE": "x"}):
            rows, _ = TV.load(p)
        r = self.by(rows)
        for n in ("presto", "moderato", "largo"):
            self.assertEqual((r[n]["connected"], r[n]["status"]), (False, "none"))
        self.assertEqual((r["rubato"]["backend"], r["rubato"]["source"], r["rubato"]["manual"]), ("outside", "이 허브의 모델", True))
        self.assertEqual(r["rubato"]["caps"], {"usd_day": TV.DEFAULT_USD_DAY, "usd_chat": TV.DEFAULT_USD_CHAT})
        self.assertIn("가격표 없음", TV.text(rows))                             # 값을 셀 수 없음을 보인다

    def test_key_file_ignored_for_outside_manual(self):
        kf = self.tmp / "key.txt"
        kf.write_text("k", encoding="utf-8")
        p = self.write(INSTANCE.replace('api_key_env = "TV_TEST_KEY"', f'api_key_file = "{kf}"'))
        rows, _ = TV.load(p, instance_backend="outside")
        self.assertEqual(self.by(rows)["rubato"]["key"], "없음")               # 바깥 사람만 등급은 키 파일을 쓰지 않는다

    def test_probe_marks_down(self):
        rows, bks = TV.load(self.write(LOCAL))
        TV.probe(rows, bks, timeout=0.5)
        self.assertEqual(self.by(rows)["moderato"]["status"], "down")
        self.assertEqual(self.by(rows)["rubato"]["status"], "nokey")           # 키 없는 줄은 찌르지 않는다

    def test_cli(self):
        p = self.write(LOCAL)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["tiers", "--backends", str(p), "--no-check"]), 0)
        t = out.getvalue()
        self.assertIn("presto", t)
        self.assertIn("바깥 API · Example", t)
        self.assertIn("환경 변수 TV_TEST_KEY", t)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["tiers", "--backends", str(p), "--no-check", "--json"])
        self.assertEqual(json.loads(out.getvalue())[0]["tier"], "presto")


class TManual(unittest.TestCase):
    """조각 2: 사람만 등급(바깥 백엔드의 기본, 또는 manual = true)은 작업자 도구의 등급 목록·자동 고르기에서 빠진다."""

    def test_split_and_definition(self):
        from stewart import spawn as SP
        from stewart.config import load_backends
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "b.toml"
        p.write_text(LOCAL + '[tiers.moderato]\ndefault_for = ["read", "write"]\n[tiers.largo]\nmanual = true\n', encoding="utf-8")
        bks, _ = load_backends(p)
        tiers = SP.load_tiers(p)
        auto, manual = SP.split_manual(tiers, bks)
        self.assertEqual(manual, {"rubato", "largo"})
        self.assertEqual(set(auto), {"presto", "moderato"})
        enum = SP.definition(auto)["function"]["parameters"]["properties"]["tier"]["enum"]
        self.assertNotIn("rubato", enum)
        self.assertEqual(SP.pick_tier(auto, {"tier": "rubato", "task": "x"}), "moderato")    # 부른 이름이 사람만이면 자동 규칙으로
        self.assertIn("사람이 대화를 열 때", SP.unknown_note(auto, "rubato", manual))
        p.write_text(LOCAL.replace('max_usd_chat = 0', 'max_usd_chat = 0\nmanual = false'), encoding="utf-8")
        auto2, manual2 = SP.split_manual(SP.load_tiers(p), bks)
        self.assertIn("rubato", auto2)                                                       # 사람이 표에 manual = false 를 적으면 자동에도


class TSpend(unittest.TestCase):
    """조각 4: 돈 상한 — 대화·하루(장부 파일, 다시 켜도 이어 셈), 가격표 없으면 토큰 상한만."""

    def test_caps(self):
        from stewart.backend import BackendError
        from stewart.spend import Spend
        d = Path(tempfile.mkdtemp())
        s = Spend(d, "rubato", usd_day=1.0, usd_chat=0.5)
        s.before()
        s.after({"cost_usd": 0.6, "prompt_tokens": 10, "completion_tokens": 5})
        with self.assertRaises(BackendError) as e:
            s.before()                                                           # 대화 상한
        self.assertIn("이 대화의 돈 상한", str(e.exception))
        s2 = Spend(d, "rubato", usd_day=1.0, usd_chat=0.5)                       # 새 대화(같은 날 장부)
        s2.before()
        s2.after({"cost_usd": 0.45})
        s3 = Spend(d, "rubato", usd_day=1.0, usd_chat=None)
        with self.assertRaises(BackendError) as e:
            s3.before()                                                          # 하루 상한(1.05 ≥ 1)
        self.assertIn("오늘 돈 상한", str(e.exception))
        self.assertEqual(s3.today()["calls"], 2)
        s4 = Spend(d, "other", usd_day=1.0, usd_chat=None)
        s4.before()                                                              # 등급마다 따로 센다
        t = Spend(d, "nop", usd_day=1.0, usd_chat=1.0, tokens_day=20, priced=False)
        t.after({"prompt_tokens": 15, "completion_tokens": 6})
        with self.assertRaises(BackendError):
            t.before()                                                           # 가격표 없으면 토큰 상한

    def test_backend_hook(self):
        from stewart import backend as B
        from stewart.backend import BackendError
        from stewart.spend import Spend

        class Bk:
            kind = "openai"
            used = None
        b = Bk()
        b.spend = Spend(Path(tempfile.mkdtemp()), "rubato", usd_day=None, usd_chat=0.1)
        B.count_use(b, {"usage": {"cost_usd": 0.2}})
        with self.assertRaises(BackendError):
            B.chat(b, [{"role": "user", "content": "x"}])                       # 보내기 전에 멈춘다(연결하지 않음)


class TChatTier(__import__("tests.test_hub3", fromlist=["MainBase"]).MainBase):
    """조각 3: 사람이 새 대화를 열 때 등급을 고른다 — 그 등급의 백엔드로 열리고(만들 때 고정), 갈래는 같은 등급,
    사람만 등급으로 하던 지난 대화는 다시 열 때 그 등급으로. 연결 없음·키 없음·모르는 등급은 거절(다른 모델로 대신 돌리지 않음)."""

    def setUp(self):
        super().setUp()
        os.environ.pop("TV_OUT_KEY", None)
        with open(self.tmp / "backends.toml", "a", encoding="utf-8") as f:
            f.write(f'[backends.outside]\nbase_url = "{self.r.url}"\nmodel = "big-outside"\nprovider = "Example"\n'
                    'api_key_env = "TV_OUT_KEY"\nmax_context = 32768\n[tiers.rubato]\nbackend = "outside"\n[tiers.presto]\nbudget = 8000\n')

    def test_tier_chat(self):
        from tests.test_hub import msg
        with self.assertRaises(ValueError):
            self.main.new_chat("fresh", tier="rubato")                       # 키 없음
        with self.assertRaises(ValueError):
            self.main.new_chat("fresh", tier="없는등급")
        with mock.patch.dict(os.environ, {"TV_OUT_KEY": "k"}):
            c = self.main.new_chat("fresh", tier="rubato")
            self.assertEqual((c.tier, c.sess.backend.name, c.sess.backend.external()), ("rubato", "outside", True))
            self.assertEqual(c.sess.backend.spend.usd_day, TV.DEFAULT_USD_DAY)          # 조각 4: 돈 상한 기본값이 붙는다
            self.assertIn("spend", c.state()["backend"])
            row = next(x for x in self.main.state()["chats"] if x["name"] == c.name)
            self.assertEqual(row["tier"], "rubato")
            self.r.scripts["MAIN"] = [msg("답")]
            c.send("바깥에서")
            self.assertTrue(c.wait_idle())
            f = self.main.new_chat("fork", source=c.name)
            self.assertEqual((f.tier, f.sess.backend.name), ("rubato", "outside"))     # 갈래는 같은 등급
            run = self.main.close_chat(c.name)
            r2 = self.main.new_chat("reopen", run_dir=run)
            self.assertEqual((r2.tier, r2.sess.backend.name), ("rubato", "outside"))   # 다시 열기도 그 등급으로
        p = self.main.new_chat("fresh", tier="presto")
        self.assertEqual((p.sess.backend.name, p.sess.backend.context_budget), ("fake", 8000))   # 등급 문맥 예산
        d = self.main.new_chat("fresh")
        self.assertEqual((d.tier, d.sess.backend.name), ("", "fake"))                 # 고르지 않으면 지금과 같다

    def test_api_tiers(self):
        rows = {r["tier"]: r for r in self.main.tier_rows()["rows"]}
        self.assertEqual(rows["rubato"]["key"], "없음")
        self.assertFalse(self.main.tier_rows()["instance"])


class TSettings(unittest.TestCase):
    """조각 6·7: backends 표를 줄 단위로 — 주석·다른 표 그대로, 검사 뒤 백업하고 쓰기, 되돌리기, 칸 검사, 키는 키체인(값은 표준 입력)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = self.tmp / "backends.toml"
        self.p.write_text("# 내 표\n" + LOCAL.replace('[backends.small]', '[backends.small]  # 작은 GPU'), encoding="utf-8")

    def test_set_keys_keeps_comments(self):
        from stewart import tomledit as TE
        t = self.p.read_text(encoding="utf-8")
        t2 = TE.set_keys(t, "backends.small", {"concurrency": 3, "model": None})
        self.assertIn("# 내 표", t2)
        self.assertIn("[backends.small]  # 작은 GPU", t2)
        self.assertIn("concurrency = 3", t2)
        self.assertNotIn('model = "small-model"', t2)
        t3 = TE.set_keys(t2, "tiers.largo", {"backend": "small", "use": "계획 \"검토\""})
        self.assertTrue(t3.rstrip().endswith('use = "계획 \\"검토\\""'))
        import tomllib
        self.assertEqual(tomllib.loads(t3)["tiers"]["largo"]["use"], '계획 "검토"')

    def test_edit_backup_revert(self):
        from stewart import settings as ST
        from stewart import tomledit as TE
        before = self.p.read_text(encoding="utf-8")
        r = ST.edit(self.p, "presto", {"backend": "big"}, "small", {"concurrency": 4})
        self.assertTrue(r["backup"].startswith("backends.toml.bak-"))
        rows, _ = TV.load(self.p)
        self.assertEqual({x["tier"]: x["backend"] for x in rows}["presto"], "big")
        with self.assertRaises(ValueError):
            ST.edit(self.p, "presto", {"max_usd_day": -1})
        with self.assertRaises(ValueError):
            ST.edit(self.p, "presto", {"secret": "x"})                              # 고치지 않는 칸
        with self.assertRaises(ValueError):
            ST.edit(self.p, backend="new", backend_fields={"base_url": "ftp://x"})
        with self.assertRaises(ValueError):
            ST.edit(self.p, backend="new", backend_fields={"base_url": "http://127.0.0.1:1/v1"})   # model 없는 백엔드 — 풀리지 않으면 쓰지 않는다
        TE.revert(self.p)
        self.assertEqual(self.p.read_text(encoding="utf-8"), before)

    def test_keychain_put(self):
        from stewart import settings as ST
        seen = {}

        def run(argv, input=None, **k):
            seen.update(argv=argv, input=input)
            return mock.Mock(returncode=0)
        with mock.patch("sys.platform", "darwin"), mock.patch("shutil.which", return_value="/usr/bin/security"):
            ST.keychain_put("stewart-outside", "sk-test", run=run)
        self.assertEqual(seen["argv"], ["security", "-i"])                       # 값은 명령 줄에 없다
        self.assertIn(b'-w "sk-test"', seen["input"])
        with self.assertRaises(ValueError):
            ST.keychain_put("stewart-outside", 'a"b', run=run)
        with mock.patch("sys.platform", "linux"):
            with self.assertRaises(ValueError):
                ST.keychain_put("stewart-outside", "k", run=run)

    def test_instance_not_editable(self):
        from stewart import settings as ST
        self.assertFalse(ST.editable({"STEWART_INSTANCE": "x"})[0])
        self.assertTrue(ST.editable({})[0])


class TSetup(unittest.TestCase):
    """조각 8: stewart setup — 표가 없으면 서버부터, 등급마다 서버 고르기·새 서버, rubato 는 바깥 API·상한·키(가림 입력 → 키체인)."""

    def test_from_scratch(self):
        from stewart import setup as SU
        p = Path(tempfile.mkdtemp()) / "cfg" / "backends.toml"
        answers = iter(["", "http://127.0.0.1:9/v1", "big-model", "2",          # 첫 서버(이름 main 기본)
                        "새", "small", "http://127.0.0.1:9/s/v1", "small-model", "1",   # presto → 새 서버
                        "", "",                                                 # moderato·largo 그대로
                        "예", "https://api.example.invalid/v1", "outside-model", "Example", "", "5", "0"])   # rubato
        put = []
        with mock.patch.object(SU, "_probe", return_value=([], None)), mock.patch("sys.platform", "darwin"), \
                mock.patch("stewart.settings.keychain_put", side_effect=lambda svc, v, **k: put.append(svc)), redirect_stdout(io.StringIO()) as out:
            rc = SU.run(p, inp=lambda prompt: next(answers), secret=lambda prompt: "sk-test")
        self.assertEqual(rc, 0, out.getvalue())
        rows, _ = TV.load(p)
        r = {x["tier"]: x for x in rows}
        self.assertEqual((r["presto"]["backend"], r["moderato"]["backend"], r["rubato"]["backend"]), ("small", "main", "outside"))
        self.assertTrue(r["rubato"]["manual"])
        self.assertEqual(r["rubato"]["caps"], {"usd_day": 5.0, "usd_chat": None})
        self.assertEqual(put, ["stewart-outside"])
        self.assertNotIn("sk-test", p.read_text(encoding="utf-8"))               # 키 값은 표에 없다
        self.assertIn('api_key_keychain = "stewart-outside"', p.read_text(encoding="utf-8"))

    def test_needs_tty(self):
        with mock.patch("sys.stdin", io.StringIO("")):
            self.assertEqual(cli.main(["setup", "--backends", "/nonexistent/x.toml"]), 2)


if __name__ == "__main__":
    unittest.main()
