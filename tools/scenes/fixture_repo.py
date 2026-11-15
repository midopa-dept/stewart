"""맡기기 장면용 시험 저장소(판 149) — 파일 수십 개짜리 작은 파이썬 서비스 "ledgerly" 를 늘 같은 꼴로 만든다.

넓은 조사가 실제로 넓도록: load_config 를 부르는 곳 20여 군데(꼴이 제각각), 오류 처리 꼴 넷, TODO·FIXME, 시험 있는 함수·없는 함수,
코드와 어긋난 문서 몇 줄. 내용은 시험용이고 실행되지 않아도 된다(장면은 읽기·찾기·고치기를 본다).

  from fixture_repo import make;  make(Path("빈 폴더"))
"""

from __future__ import annotations

from pathlib import Path

ERR = [
    ("try:\n        {body}\n    except Exception:\n        return None", "삼킴"),
    ("try:\n        {body}\n    except ValueError as e:\n        log.warning('bad value: %s', e)\n        raise", "기록 뒤 다시"),
    ("result = {body_expr}\n    if result is None:\n        raise LedgerError('없음')\n    return result", "자체 오류"),
    ("{body}  # 오류 처리 없음", "없음"),
]
USES = ["cfg = load_config()", "cfg = load_config(path=os.environ.get('LEDGERLY_CONFIG'))", "cfg = load_config(strict=True)",
        "port = load_config()['port']", "settings = config.load_config(overrides={'debug': True})"]


def _handler(i: int) -> str:
    err, _ = ERR[i % len(ERR)]
    use = USES[i % len(USES)]
    imp = "from ledgerly import config" if "config.load_config" in use else "from ledgerly.config import load_config"
    todo = "    # TODO: 재시도 횟수를 설정으로 빼기\n" if i % 4 == 1 else ("    # FIXME: 큰 금액에서 반올림이 틀린다\n" if i % 7 == 3 else "")
    ind = "    " if err.startswith("{body}") else "        "   # 오류 처리 없는 꼴은 함수 몸 들여쓰기 그대로
    body = err.format(body=f"{use}\n{ind}return handle_{i:02d}(cfg)", body_expr=f"lookup_{i:02d}()")
    return (f'"""handler {i:02d} — 장부 이벤트 하나를 처리한다."""\nimport logging\nimport os\n\n{imp}\nfrom ledgerly.errors import LedgerError\n\n'
            f"log = logging.getLogger(__name__)\n\n\ndef run_{i:02d}(event):\n{todo}    {body}\n\n\ndef handle_{i:02d}(cfg):\n"
            f"    return {{'handler': {i}, 'port': cfg.get('port') if isinstance(cfg, dict) else None}}\n\n\ndef lookup_{i:02d}():\n    return None\n")


FILES = {
    "README.md": "# Ledgerly\n\nLedgerly is a smal ledger service.\n\n## 설치\n\n    pip install ledgerly\n    ledgerly init   # 설정 파일 만들기\n\n"
                 "## 실행\n\n    ledgerly serve --port 8080\n",
    "pyproject.toml": '[project]\nname = "ledgerly"\nversion = "0.4.0"\n',
    "src/ledgerly/__init__.py": '__version__ = "0.4.0"\n',
    "src/ledgerly/errors.py": "class LedgerError(Exception):\n    pass\n",
    "src/ledgerly/config.py": ('"""설정 읽기."""\nimport json\nimport os\n\nDEFAULT_PORT = 8088\n\n\n'
                               "def load_config(path=None, strict=False, overrides=None):\n"
                               "    path = path or os.path.expanduser('~/.ledgerly.json')\n    cfg = {'port': DEFAULT_PORT, 'debug': False}\n"
                               "    if os.path.exists(path):\n        cfg.update(json.load(open(path)))\n    elif strict:\n        raise FileNotFoundError(path)\n"
                               "    cfg.update(overrides or {})\n    return cfg\n"),
    "src/ledgerly/money.py": ('"""금액."""\nfrom decimal import Decimal\n\n\ndef parse_amount(text):\n'
                              "    # TODO: 쉼표(1,000)를 받기\n    return Decimal(text.strip())\n\n\ndef fmt_amount(value):\n    return f'{value:,.2f}'\n"),
    "src/ledgerly/billing.py": ('"""청구."""\nfrom ledgerly.money import parse_amount\n\n\ndef total(lines):\n'
                                "    return sum(parse_amount(x) for x in lines)\n\n\ndef invoice(customer, lines):\n"
                                "    return {'customer': customer, 'total': total(lines)}\n"),
    "src/ledgerly/export.py": ('"""내보내기 — csv·json 만 된다(xlsx 는 자리만)."""\nimport csv\nimport json\n\nFORMATS = ("csv", "json")\n\n\n'
                               "def export(rows, fmt, out):\n    if fmt == 'csv':\n        csv.writer(out).writerows(rows)\n"
                               "    elif fmt == 'json':\n        json.dump(rows, out)\n    elif fmt == 'xlsx':\n"
                               "        raise NotImplementedError('xlsx')  # FIXME: 문서에는 된다고 적혀 있다\n    else:\n        raise ValueError(fmt)\n"),
    "src/ledgerly/logging_setup.py": ('"""로깅 설정 — serve 가 시작할 때 부른다."""\nimport logging\n\n\ndef setup(debug=False):\n'
                                      "    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)\n"),
    "src/ledgerly/serve.py": ('"""서버."""\nfrom ledgerly.config import load_config\nfrom ledgerly.logging_setup import setup\n\n\n'
                              "def main():\n    cfg = load_config()\n    setup(cfg['debug'])\n    return cfg['port']\n"),
    "docs/index.md": "# 문서\n\n- install.md\n- export.md\n- config.md\n- billing.md\n",
    "docs/install.md": "# 설치\n\n`pip install ledgerly` 뒤 `ledgerly init`.\n",
    "docs/export.md": "# 내보내기\n\ncsv·json·xlsx 세 가지를 지원한다.\n",   # 코드와 어긋남(xlsx)
    "docs/config.md": "# 설정\n\n기본 포트는 8080 이다. 설정 파일은 `~/.ledgerly.json`.\n",   # 어긋남(8088)
    "docs/billing.md": "# 청구\n\n`invoice(customer, lines)` 는 할인을 적용한다.\n",   # 어긋남(할인 없음)
    "docs/logging.md": "# 로깅\n\n로그 수준은 `LEDGERLY_LOG` 환경 변수로 정한다.\n",   # 어긋남(debug 설정으로)
    "tests/test_money.py": "from ledgerly.money import parse_amount\n\n\ndef test_parse():\n    assert str(parse_amount(' 3.5 ')) == '3.5'\n",
    "tests/test_billing.py": "from ledgerly.billing import total\n\n\ndef test_total():\n    assert total(['1', '2']) == 3\n",
    "tests/test_config.py": "from ledgerly.config import load_config\n\n\ndef test_default_port():\n    assert load_config('/nonexistent')['port'] == 8088\n",
}


def make(root: Path, n_handlers: int = 22) -> Path:
    root = Path(root)
    for rel, text in FILES.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    hd = root / "src" / "ledgerly" / "handlers"
    hd.mkdir(parents=True, exist_ok=True)
    (hd / "__init__.py").write_text("", encoding="utf-8")
    for i in range(1, n_handlers + 1):
        (hd / f"h{i:02d}.py").write_text(_handler(i), encoding="utf-8")
    for i in (1, 4, 9, 16):
        (root / "tests" / f"test_h{i:02d}.py").write_text(
            f"from ledgerly.handlers.h{i:02d} import handle_{i:02d}\n\n\ndef test_h{i:02d}():\n    assert handle_{i:02d}({{'port': 1}})['port'] == 1\n",
            encoding="utf-8")
    return root
