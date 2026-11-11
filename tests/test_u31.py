"""U2-8 늘 허용: 여러 줄 따옴표·heredoc 명령도 풀어 꼴을 고르게, 넓은 앞머리(python3 -c *)는 경고와 함께 받기, 손으로 더하기."""

import http.client
import json
import unittest
from urllib.parse import urlencode

from stewart import allowrules as A
from stewart.web import WebApp, make_token
from tests.test_hub3 import MainBase

PYC = 'python3 -c "\nimport sys\nprint(\'hello\', sys.version_info[0])\n"'
HEREDOC = "python3 - <<'EOF'\nimport json\nprint(json.dumps({\"a\": (1, 2)}))\nEOF"


class TParse(unittest.TestCase):
    def kinds(self, cmd):
        r = A.candidates(cmd)
        self.assertTrue(r["ok"], r["reason"])
        return {o["kind"]: o for o in r["pieces"][0]["options"]}

    def test_multiline_python_c(self):
        k = self.kinds(PYC)
        self.assertEqual(set(k), {"exact", "code_prefix", "first_word"})
        self.assertEqual(k["code_prefix"]["pattern"], "python3 -c *")
        self.assertTrue(k["code_prefix"]["broad"])
        self.assertIn(A.BROAD_NOTE, k["code_prefix"]["warn"])
        self.assertFalse(k["exact"]["broad"])
        self.assertEqual(A.decide(PYC, [A.Rule(k["exact"]["pattern"], "allow", "hub")]).effect, "allow")   # 정확한 꼴이 그 명령에 맞는다
        self.assertNotEqual(A.decide(PYC.replace("hello", "bye"), [A.Rule(k["exact"]["pattern"], "allow", "hub")]).effect, "allow")

    def test_heredoc(self):
        k = self.kinds(HEREDOC)
        self.assertEqual(k["code_prefix"]["pattern"], "python3 - *")
        self.assertEqual(A.decide(HEREDOC, [A.Rule("python3 - *", "allow", "hub")]).effect, "allow")
        self.assertEqual(A.decide(HEREDOC, [A.Rule(k["exact"]["pattern"], "allow", "hub")]).effect, "allow")
        # heredoc 뒤 다른 명령·파일 리다이렉트는 그대로 판정한다
        self.assertEqual(A.split_command(HEREDOC + "\necho done"), [k["exact"]["pattern"], "echo done"])
        with self.assertRaises(A.Unsafe):
            A.split_command("cat <<EOF > out.txt\nx\nEOF")
        with self.assertRaises(A.Unsafe):
            A.split_command("python3 - <<'EOF'\nprint(1)\n")                     # 끝 표지 없음

    def test_bash_c_and_glob(self):
        k = self.kinds("bash -c 'echo hi; ls'")
        self.assertEqual(k["code_prefix"]["pattern"], "bash -c *")
        self.assertEqual(A.parse_pattern("npm run test*"), "npm run test*")
        self.assertTrue(A.Rule("npm run test*", "allow", "hub").matches("npm run test:unit"))
        self.assertFalse(A.Rule("npm run test*", "allow", "hub").matches("npm run build"))
        self.assertIsNone(A.check_always("python3 -c *", broad_ok=True))
        self.assertIsNotNone(A.check_always("python3 -c *"))                        # 기본(자동 제안)은 여전히 좁게
        self.assertIsNotNone(A.check_always("sudo *", broad_ok=True))                # 위험 명령은 사람이 골라도 안 됨


class TAddRule(MainBase):
    def setUp(self):
        super().setUp()
        self.token = make_token(self.hub.dir)
        self.web = WebApp(self.main, self.hub, self.token)
        self.web.serve()
        self.addCleanup(self.web.close)
        self.host = f"127.0.0.1:{self.web.port}"
        self.origin = f"http://{self.host}"
        r, _ = self.req("POST", "/login", urlencode({"token": self.token}),
                        {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin})
        ck = r.getheader("Set-Cookie").split(";")[0]
        _, page = self.req("GET", "/?ui=old", headers={"Cookie": ck})
        import re
        csrf = re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()
        self.good = {"Cookie": ck, "Origin": self.origin, "X-Stewart-Token": csrf, "Content-Type": "application/json"}

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        c.request(method, path, body=body, headers={"Host": self.host, **(headers or {})})
        r = c.getresponse()
        d = r.read()
        c.close()
        return r, d

    def test_add_by_hand(self):
        r, d = self.req("POST", "/api/rules/add", json.dumps({"pattern": "python3 -c *", "scope": "session"}), self.good)
        self.assertEqual(r.status, 200)
        self.assertIn(A.BROAD_NOTE, json.loads(d)["warn"])
        r, d = self.req("POST", "/api/rules/add", json.dumps({"pattern": "Bash(npm run test:*)"}), self.good)
        self.assertEqual(json.loads(d)["pattern"], "npm run test *")
        r, d = self.req("POST", "/api/rules/add", json.dumps({"pattern": "git push *"}), self.good)
        self.assertEqual((r.status, json.loads(d)["error"]), (400, "pattern"))
        r, d = self.req("POST", "/api/rules/add", json.dumps({"pattern": "a && b"}), self.good)
        self.assertEqual(r.status, 400)
        r, d = self.req("GET", "/api/rules", headers={"Cookie": self.good["Cookie"]})
        rules = {x["pattern"]: x for x in json.loads(d)["rules"]}
        self.assertEqual(set(rules), {"python3 -c *", "npm run test *"})
        self.assertEqual(rules["npm run test *"]["scope"], "project")
        self.assertTrue(rules["python3 -c *"]["warn"])
        self.assertEqual(self.req("GET", "/api/rules/add", headers={"Cookie": self.good["Cookie"]})[0].status, 405)
        self.assertTrue(any("늘 허용 규칙을 더했다" in n for n in self.hub.notices))   # 메인에게도 알린다


if __name__ == "__main__":
    unittest.main()
