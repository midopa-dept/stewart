"""데모 B 연습 저장소를 새로 만든다: <폴더>/calc(인자로 폴더를 주지 않으면 임시 폴더 아래 stewart-demo)
이미 있으면 calc.old-<시각> 으로 옮겨 둔다(지우지 않음)."""

import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "stewart-demo"
D = ROOT / "calc"

FILES = {
    "calc/__init__.py": "",
    "calc/text.py": '''
        import re


        def slugify(text):
            """'Hello, World!' -> 'hello-world'. 영숫자 외 문자는 '-' 하나로, 앞뒤 '-'는 없앤다."""
            s = re.sub(r"[^A-Za-z0-9]", "-", text)
            return s.strip("-")
    ''',
    "calc/duration.py": '''
        import re


        def parse_duration(s):
            """'1h30m' -> 5400, '45s' -> 45, '2h' -> 7200, '1h2m3s' -> 3723 (초 단위 정수)."""
            total = 0
            for num, unit in re.findall(r"(\\d+)([hms])", s):
                if unit == "h":
                    total += int(num) * 3600
                elif unit == "s":
                    total += int(num)
            return total
    ''',
    "calc/stats.py": '''
        def median(values):
            """중앙값. 짝수 개면 가운데 두 값의 평균. 빈 목록이면 ValueError."""
            xs = sorted(values)
            return xs[len(xs) // 2]
    ''',
    "tests/__init__.py": "",
    "tests/test_calc.py": '''
        import unittest

        from calc.duration import parse_duration
        from calc.stats import median
        from calc.text import slugify


        class TestSlugify(unittest.TestCase):
            def test_basic(self):
                self.assertEqual(slugify("Hello, World!"), "hello-world")

            def test_collapse(self):
                self.assertEqual(slugify("  a -- b__c  "), "a-b-c")


        class TestDuration(unittest.TestCase):
            def test_hours_minutes(self):
                self.assertEqual(parse_duration("1h30m"), 5400)

            def test_all(self):
                self.assertEqual(parse_duration("1h2m3s"), 3723)

            def test_seconds(self):
                self.assertEqual(parse_duration("45s"), 45)


        class TestMedian(unittest.TestCase):
            def test_odd(self):
                self.assertEqual(median([3, 1, 2]), 2)

            def test_even(self):
                self.assertEqual(median([4, 1, 3, 2]), 2.5)

            def test_empty(self):
                with self.assertRaises(ValueError):
                    median([])


        if __name__ == "__main__":
            unittest.main()
    ''',
    "README.md": "# calc (stewart 데모 B 연습 저장소)\n\n`python -m unittest -q`\n",
}

if D.exists():
    D.rename(D.with_name(f"calc.old-{datetime.now():%Y%m%d-%H%M%S}"))
for rel, body in FILES.items():
    p = D / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
r = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=D, capture_output=True, text=True)
print(f"만듦: {D}  (테스트 exit {r.returncode}, 실패해야 정상)")
print(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "")
