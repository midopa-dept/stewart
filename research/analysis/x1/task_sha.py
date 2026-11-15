#!/usr/bin/env python3
"""과제 sha(실행 코드와 무관한 과제 정의 sha) — 평가 담당. 사용: python3 -B task_sha.py <평가기 판 폴더>
과제 정의 sha = json.dumps(과제 dict, sort_keys, ensure_ascii=False, bytes→{"__b": sha256}, 함수→{"__fn": 이름}) 의 sha256.
묶음 sha = "ID:정의 sha\n" 줄을 ID 순으로 이은 글의 sha256. 출력은 묶음 sha 와 과제 수뿐(내용·ID 별 sha 를 내지 않음).
(2026-11-10 점검에서 쓴 임시 계산과 같은 식 — h1.1 aa97ed44…, v2.0.x bf0d62eb…, v1.2.x 64565974… 가 이 식의 값)"""
import hashlib
import importlib.util as u
import json
import sys

sys.dont_write_bytecode = True
d = sys.argv[1]
sys.path.insert(0, d)
s = u.spec_from_file_location("re", d + "/run_eval.py")
m = u.module_from_spec(s)
s.loader.exec_module(m)


def enc(o):
    if isinstance(o, bytes):
        return {"__b": hashlib.sha256(o).hexdigest()}
    if callable(o):
        return {"__fn": getattr(o, "__name__", "?")}
    raise TypeError(type(o))


ts = m.load_tasks()
lines = "".join(f"{t['id']}:{hashlib.sha256(json.dumps(t, sort_keys=True, ensure_ascii=False, default=enc).encode()).hexdigest()}\n"
                for t in sorted(ts, key=lambda t: t["id"]))
print(hashlib.sha256(lines.encode()).hexdigest(), len(ts), getattr(m, "EVAL_VERSION", "?"))
