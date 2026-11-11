"""미디어 분류: 파일 이름 규칙 전처리 → 애매한 것만 모델에 N개씩 묶어 넘긴다(dry-run, 제안만).

서고 구조(demos/media/library-layout.md, 예시 서고 규칙): Anime·Shows·Movies·Variety·Concerts·Clips·MusicVideos·
Music·Photos·Comics·Archives. 규칙은 이름에서 **확실히 알 수 있는 것만** 정하고, 이름에 없는 폴더 이름(그룹·앨범·
서고의 한국어 제목 등)은 지어내지 않는다. 그런 것은 힌트를 붙여 모델에 넘긴다.

규칙이 정하는 것:
- 받는 중·분할 압축 → _incomplete/, 시스템 파일 → _junk/
- 서고식 이름 "제목 (연도) - SxxEyy - [원본].ext" → <Anime|Shows>/제목 (연도)/Season NN (원본 이름의 표시로 애니/실사 판단,
  판단이 안 서면 폴더 이름만 힌트로 모델에)
- 서고식 영화 "제목 (연도)[ - [원본]].ext"(제목에 " - "·「」『』【】가 없을 때) → Movies/제목 (연도)
- 앨범 폴더를 이름으로 알 수 없는 음악 낱곡(트랙 번호) → Music/ (최상위만, 낮은 신뢰도)
- "가수 - 곡" 낱곡(트랙 번호 없음) → Music/일반
- 앨범 아트 일반 이름(cover·folder·AlbumArt*·back·front) → Music/ (최상위만), 숫자만인 이미지 → _review/
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import nostore as NS

from . import engine
from .config import Backend, Profile
from .folders import FolderIndex
from .websearch import SearxClient, korean_query, make_queries, result_terms
from .record import Recorder

VIDEO = {"mkv", "mp4", "avi", "mov", "webm", "m4v", "wmv", "ts", "mpg", "mpeg", "flv", "m2ts"}
SUB = {"srt", "smi", "ass", "ssa", "vtt", "sub", "idx"}
AUDIO = {"mp3", "flac", "m4a", "ogg", "wav", "aac", "opus", "wma", "alac", "ape"}
IMAGE = {"jpg", "jpeg", "heic", "heif", "png", "dng", "cr2", "cr3", "arw", "nef", "gif", "webp", "bmp"}
ARCHIVE = {"rar", "zip", "7z", "tar", "gz"}
LIBRARY_ROOTS = ["Anime", "Shows", "Movies", "Variety", "Concerts", "Clips", "MusicVideos", "Music", "Photos", "Comics", "Archives"]
ROOTS = set(LIBRARY_ROOTS) | {"_review", "_incomplete", "_junk"}

_ANIME_HINT = re.compile(r"(?i)raws|\bBD\b|BDRIP|blu-?ray|hi10p|\bAVC\b|AACx2|session\.\d|\]\[|fansub|subsplease|horriblesubs")
_SHOWS_HINT = re.compile(r"(?i)HDTV|WEB-?DL|WEBRip|YYeTs|\.E\d{2,3}\.\d{6}|\bE\d{2,3}end\b|-TEA\b")
_GENERIC_ART = re.compile(r"(?i)^(cover|folder|front|back|albumart\w*)$")
_PLACEHOLDER = re.compile(r"(?i)^(?:.*\b(?:unknown|untitled|unnamed|misc|miscellaneous|various|n/?a|tbd|placeholder|no ?name)\b.*"
                          r"|.*(?:알 ?수 ?없|미상|모름|불명|미분류|무제).*|(?:none|tmp|temp|other|others|기타|임시|없음))$")


def _kind(ext: str) -> str:
    return ("video" if ext in VIDEO else "subtitle" if ext in SUB else "audio" if ext in AUDIO
            else "image" if ext in IMAGE else "archive" if ext in ARCHIVE else "other")


def _ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    return (stem, ext.lower()) if dot and stem else (name, "")


def original_name(name: str) -> str:
    """서고식 이름 "… - [원본 이름].ext" 에서 원본 이름(+확장자)만 꺼낸다. 없으면 그대로."""
    m = re.match(r"^.*? - \[(.*)\](\.[^.\]]+)$", name)
    return m.group(1) + m.group(2) if m else name


def _r(dest: str, reason: str, conf: float, rule: str) -> dict:
    return {"destination": dest, "reason": reason, "confidence": conf, "rule": rule}


def classify(name: str) -> tuple[dict | None, dict]:
    """(규칙 결과 또는 None, 모델에 줄 힌트)."""
    stem, ext = _ext(name)
    kind = _kind(ext)
    hints: dict = {"종류": kind, "확장자": ext or "-"}
    low = name.lower()

    # 1) 받는 중·분할 압축, 시스템 파일
    if re.search(r"\.(part|crdownload|!qb|aria2|download)$", low) or re.search(r"\.part\d+\.rar$|\.r\d{2}$|\.(zip|7z|rar)\.\d{3}$", low):
        return _r("_incomplete/", "받는 중이거나 분할 압축 파일 확장자다.", 0.98, "incomplete"), hints
    if low in {"thumbs.db", "desktop.ini", ".ds_store"} or low.startswith("._"):
        return _r("_junk/", "시스템 파일이다.", 0.97, "junk"), hints

    if kind in ("video", "subtitle"):
        # 2) 서고식 시리즈 이름: 제목 (연도) - SxxEyy - [원본]
        m = re.match(r"^(?P<t>.+?) \((?P<y>(?:19|20)\d{2})\) - S(?P<s>\d{2})E(?P<e>\d{2,3})\b", stem)
        if m:
            folder = f"{m['t']} ({m['y']})/Season {m['s']}"
            anime, shows = bool(_ANIME_HINT.search(name)), bool(_SHOWS_HINT.search(name))
            if anime != shows:
                root = "Anime" if anime else "Shows"
                what = "애니(원본 이름에 BD·raws 등 애니 릴리스 표시)" if anime else "실사(원본 이름에 HDTV·방송일 등 표시)"
                return _r(f"{root}/{folder}", f"서고식 이름에 제목·연도·시즌 {int(m['s'])}이 있고 {what}다.", 0.85,
                          f"series-{root}"), hints
            hints.update({"폴더 후보": folder, "판단할 것": "최상위가 Anime인지 Shows인지"})
            return None, hints
        # 3) 서고식 영화 이름: 제목 (연도)[ - [원본]]
        m = re.match(r"^(?P<t>[^\[]+?) \((?P<y>(?:19|20)\d{2})\)(?: - \[.*\])?$", stem)
        if m and kind == "video" and " - " not in m["t"] and not re.search(r"[「」『』【】]", m["t"]):
            return _r(f"Movies/{m['t']} ({m['y']})", "서고식 '제목 (연도)' 이름이고 회차 표기가 없어 영화로 본다.", 0.8, "movie"), hints
        if m:
            hints.update({"제목 후보": m["t"], "연도": m["y"]})

    if kind == "audio" and not re.search(r"(?i)raws|commentary", name):
        # 4) 음악: 트랙 번호가 있으면 앨범 곡(앨범 폴더는 이름으로 알 수 없음), 없으면 "가수 - 곡" 낱곡
        if re.match(r"^(\d{1,2}-)?\d{1,3}[ .\-_]", stem) or re.search(r"-\d{2}-", stem):
            if re.search(r"-\d{2}-", stem) and not re.match(r"^\d", stem):
                return _r("Music/일반", "'가수-트랙-곡' 형식의 낱곡이라 낱곡 모음으로 본다(번호 붙은 모음 폴더는 이름으로 모름).",
                          0.5, "music-loose"), hints
            return _r("Music/", "트랙 번호가 있는 앨범 곡이다. 가수·앨범 폴더는 이름만으로 알 수 없다(최상위만 제안).",
                      0.3, "music-album-unknown"), hints
        if re.match(r"^.+? - .+$", stem):
            return _r("Music/일반", "'가수 - 곡'(또는 '곡 - 가수') 형식의 낱곡이다.", 0.6, "music-loose"), hints

    if kind == "image":
        if _generic_image(stem):
            return _r("_review/", "이름에 단서가 없는 일반 이미지 이름이라 검색하지 않고 검토로 보낸다.", 0.1, "image-generic"), hints
        if _GENERIC_ART.match(stem):
            return _r("Music/", "앨범 아트에 흔한 일반 이름이다. 어느 앨범인지는 이름으로 알 수 없다(최상위만 제안).",
                      0.3, "image-albumart"), hints
        if re.fullmatch(r"\d{1,4}", stem):
            return _r("_review/", "숫자만으로 된 이미지 이름이라 판단할 수 없다.", 0.1, "image-number"), hints

    # 애매함 → 모델 힌트
    y = re.search(r"(?:^|\D)((?:19|20)\d{2})(?:\D|$)", stem)
    if y and "연도" not in hints:
        hints["연도 후보"] = y.group(1)
    e = re.search(r"(?i)(?:^|[ ._-])(?:E|EP)\s?(\d{1,3})(?:[ ._)\]-]|$)", stem)
    if e:
        hints["에피소드 후보"] = int(e.group(1))
    if stem.startswith("[") and kind in ("video", "audio"):
        hints["형식"] = "팬섭/raws 릴리스 이름([그룹] 제목 …)"
    orig = original_name(name)
    if orig != name:
        hints["원본 이름"] = orig
    return None, hints


_GENERIC_IMAGE = re.compile(r"(?i)^(\d{1,6}|(img|dsc|dscn|dscf|pxl|mm|scan|image|photo|screenshot)[ _-]?\d[\d_-]*"
                            r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                            r"|.*\bcopy\b.*|다운로드.*|download.*|無題.*|untitled.*)$")


def _generic_image(stem: str) -> bool:
    """단서 없는 일반 이미지 이름: 숫자, IMG·DSC·Scan·Screenshot+숫자, uuid, copy, 다운로드, 깨진 이름(라틴-1 글자가
    둘 이상이고 영문 낱말이 없는 것). 앨범 아트 이름(cover 등)은 따로 다룬다."""
    s = stem.strip()
    if _GENERIC_IMAGE.match(s):
        return True
    latin1 = len(re.findall(r"[\u00c0-\u00ff\u0100-\u017f]", s))
    return latin1 >= 2 and not re.search(r"[A-Za-z]{3,}", re.sub(r"[\u00c0-\u017f]", " ", s))


def _genre(name: str) -> str:
    if _ANIME_HINT.search(name) or re.match(r"^\[[^\]]*(raws?|subs?|moozzi)", name, re.I):
        return "애니"
    if _SHOWS_HINT.search(name):
        return "드라마"
    return ""


def episode_numbers(name: str) -> set[int]:
    """이름에 나온 회차 후보 숫자(해상도·코덱·연도·날짜·비트레이트 숫자는 뺀다)."""
    s = re.sub(r"(?i)\d{3,4}x\d{3,4}|\d{3,4}p|[xh]\.?26[45]|10bit|hi10p?|\d+(\.\d+)?\+\d+(\.\d+)?|\d+k\b|aacx\d|"
               r"\b(19|20)\d{2}\b|\b\d{6,8}\b|\d{4}-\d{2}-\d{2}", " ", name)
    return {int(x) for x in re.findall(r"(?<!\d)\d{1,3}(?!\d)", s)}


def fix_episode_season(dest: str, name: str, folders: FolderIndex) -> tuple[str, str] | None:
    """제안이 …/Season NN인데 NN이 이름의 회차 번호와 같고 그 시즌 폴더가 목록에 없으면:
    같은 작품의 기존 시즌 폴더가 하나뿐이면 그 폴더로, 여럿이면 _review로. (새 목적지, 사유) 또는 None."""
    m = re.match(r"^((?:Anime|Shows)/[^/]+)/Season (\d+)/?$", dest.strip())
    if not m or folders.exists(dest) or int(m.group(2)) not in episode_numbers(name):
        return None
    seasons = [p for p in folders.children(m.group(1)) if re.fullmatch(r"(?i)season \d+", p.rsplit("/", 1)[1])]
    if len(seasons) == 1:
        return seasons[0], f"회차 번호 {int(m.group(2))}를 시즌으로 쓴 것으로 보여 유일한 기존 시즌 폴더로 바꿈"
    if len(seasons) > 1:
        return "_review/", f"회차 번호 {int(m.group(2))}를 시즌으로 쓴 것으로 보이고 기존 시즌 폴더가 여럿이라 검토로 보냄"
    return None


def check_destination(dest: str) -> str | None:
    """구조 밖 최상위 폴더, 지어낸 자리 표시 하위 폴더, 빈 이름 등을 찾는다. 문제 없으면 None."""
    parts = [p for p in dest.strip().strip("/").split("/") if p]
    if not parts:
        return "목적지가 비었다"
    if parts[0] not in ROOTS:
        return f"구조에 없는 최상위 폴더 '{parts[0]}'"
    if parts[0].startswith("_") and len(parts) > 1:
        return f"특수 목적지 {parts[0]}/ 아래에 하위 폴더 '{parts[1]}'를 만들었다"
    for p in parts[1:]:
        if _PLACEHOLDER.match(p) or p in {".", ".."}:
            return f"지어낸 자리 표시 폴더 이름 '{p}'"
        if re.search(r"<[^<>]*>", p):
            return f"꺾쇠 자리 표시가 남은 폴더 이름 '{p}'"
    return None


def read_input(path: Path) -> tuple[list[str], list[str | None]]:
    """한 줄에 파일 이름 하나. "파일 이름<TAB>정답 폴더" 형식이면 정답도 읽는다(채점용, 모델에는 안 보낸다).
    같은 이름이 여러 줄에 있어도 줄마다 따로 둔다."""
    names, truth = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, tab, ans = line.partition("\t")
        names.append(name.strip())
        truth.append(ans.strip() if tab and ans.strip() else None)
    return names, truth


def norm_dest(d: str) -> str:
    """비교용: 첫 최상위 폴더 앞은 떼고, 공백·대소문자·끝 / 정리."""
    parts = [p for p in d.replace("\\", "/").split("/") if p]
    for i, p in enumerate(parts):
        if p in ROOTS:
            parts = parts[i:]
            break
    return "/".join(re.sub(r"\s+", " ", p).strip().casefold() for p in parts)


def indeterminate(names: list[str], truth: list[str | None]) -> list[str | None]:
    """줄마다 '이름만으로 판별 불가' 사유(없으면 None): 같은 이름에 정답이 서로 다른 줄, 일반 이름 이미지(cover·folder·숫자만)."""
    answers: dict[str, set] = {}
    for n, t in zip(names, truth):
        if t:
            answers.setdefault(n.casefold(), set()).add(norm_dest(t))
    out = []
    for n in names:
        stem, ext = _ext(n)
        if len(answers.get(n.casefold(), ())) > 1:
            out.append("같은 이름에 정답이 여럿")
        elif _kind(ext) == "image" and (_GENERIC_ART.match(stem) or re.fullmatch(r"\d{1,4}", stem)):
            out.append("일반 이름 이미지")
        else:
            out.append(None)
    return out


def _tally(rows: list[dict]) -> dict:
    b = {"n": len(rows), "exact": 0, "top": 0, "review": 0, "wrong": 0}
    for r in rows:
        b["exact"] += r["exact"]
        b["top"] += r["top"]
        b["review"] += r["review"]
        b["wrong"] += (not r["exact"] and not r["review"])
    return b


def score(items: list[dict], truth: list[str | None], names: list[str] | None = None) -> dict:
    names = names or [it["file"] for it in items]
    indet = indeterminate(names, truth)
    rows = []
    for it, t, ind in zip(items, truth, indet):
        if t is None:
            continue
        nt, pr = norm_dest(t), norm_dest(it["destination"])
        cand = it.get("candidates")
        rows.append({"file": it["file"], "pred": it["destination"], "truth": t, "source": it["source"],
                     "exact": pr == nt, "top": pr.split("/")[0] == nt.split("/")[0], "review": pr == "_review",
                     "cand_hit": None if cand is None else any(norm_dest(c) == nt for c in cand),
                     "new_folder": it.get("new_folder"),
                     "indeterminate": ind, "outside": nt.split("/")[0] not in {r.casefold() for r in ROOTS}})
    srcs = sorted({r["source"] for r in rows})
    by = {"전체": _tally(rows), **{s: _tally([r for r in rows if r["source"] == s]) for s in srcs}}
    det = [r for r in rows if not r["indeterminate"]]
    by_det = {"전체": _tally(det), **{s: _tally([r for r in det if r["source"] == s]) for s in srcs}}
    a, d = by["전체"], by_det["전체"]

    def pct(x, n):
        return f"{x}/{n} ({x / n:.0%})" if n else "-"

    line = (f"전체 {a['n']}개: 정확 {pct(a['exact'], a['n'])}, 최상위 {pct(a['top'], a['n'])}, _review {a['review']}, 틀림 {a['wrong']} | "
            f"판별 불가 {a['n'] - d['n']}개 제외 {d['n']}개: 정확 {pct(d['exact'], d['n'])}, 최상위 {pct(d['top'], d['n'])}, "
            f"_review {d['review']}, 틀림 {d['wrong']}")
    outside = sum(r["outside"] for r in rows)
    if outside:
        line += f" | 정답 폴더가 구조 문서 밖 {outside}개"
    withc = [r for r in rows if r["cand_hit"] is not None]
    if withc:
        line += f" | 모델에 넘긴 줄 중 후보에 정답이 든 것 {sum(r['cand_hit'] for r in withc)}/{len(withc)}"
    return {"summary_line": line, "by_source": by, "by_source_determinable": by_det, "outside_layout": outside,
            "indeterminate": [{"file": r["file"], "why": r["indeterminate"], "truth": r["truth"]} for r in rows if r["indeterminate"]],
            "rows": rows}


def _batch_task(layout: str, batch: list[tuple[str, dict]], cands: dict[str, list[str]] | None = None,
                web: int = 0) -> str:
    lines = []
    for i, (name, h) in enumerate(batch, 1):
        lines.append(f"{i}. {name}  |  " + ", ".join(f"{k}={v}" for k, v in h.items()))
        if cands is not None:
            c = cands.get(name) or []
            lines.append("   기존 폴더 후보: " + (" ; ".join(c) if c else "(겹치는 기존 폴더 없음)"))
    extra = ""
    if cands is not None:
        extra += ("- 서고에 이미 있는 폴더 후보를 파일마다 붙였다. 맞는 후보가 있으면 그 경로를 글자 그대로 쓴다. "
                  "후보에 맞는 것이 없을 때만 새 폴더를 제안하고 new_folder를 true로 한다.\n")
    if web:
        extra += (f"- 이름만으로 모르면 web_search 도구로 찾아본다(for_file에 그 파일 이름을 넣고, 파일당 최대 {web}회). "
                  "결과를 보고 검색어를 바꿔 다시 찾아도 된다. 다 판단했으면 도구 없이 최종 JSON을 낸다.\n")
    return ("아래는 서고 구조다.\n\n" + layout.strip() + "\n\n"
            "다음 파일들은 이름 규칙으로 자동 분류하지 못한 것이다. 파일마다 목적지 폴더를 제안하라.\n"
            + ("- 파일 이름(과 전처리 힌트)으로 판단한다.\n" if web else
               "- 파일 이름(과 전처리 힌트)만으로 판단한다. 도구를 부를 필요 없이 바로 최종 JSON을 낸다.\n") +
            "- 목록의 파일 이름을 file에 원문 그대로, 목록의 파일만 빠짐없이 쓴다.\n"
            "- destination은 서고 최상위 기준 상대 폴더(예: Movies/제목 (연도)). 하위 폴더 이름을 모르면 아는 데까지만 쓰고"
            "(예: Music/), 판단이 안 서면 _review/. Unknown 같은 자리 표시 이름을 지어내지 않는다.\n"
            "- 그룹·작품을 알아볼 수 있으면 아는 지식으로 분류하되, 근거가 약하면 confidence를 낮게.\n"
            "- reason은 한국어 한 문장.\n" + extra + "\n"
            "파일 목록(이름 | 전처리 힌트):\n" + "\n".join(lines) + "\n")


def _cell(s) -> str:
    return str(s).replace("|", "\\|")


# 파일 종류별로 "후보 강함"에 셀 수 있는 최상위: 영상 후보에 Music 앨범 폴더가 우연히 맞아도 세지 않는다
KIND_ROOTS = {
    "video": {"Anime", "Shows", "Movies", "Variety", "Concerts", "Clips", "MusicVideos"},
    "subtitle": {"Anime", "Shows", "Movies", "Variety", "Concerts", "Clips", "MusicVideos"},
    "audio": {"Music", "Archives", "Anime"},
    "image": {"Photos", "Comics", "Music"},
    "archive": {"Archives"},
}


def _weak(sc: list[tuple[str, float, bool]], kind: str = "", top: int = 3) -> bool:
    """후보가 없거나, 위쪽 후보 중 (파일 종류에 맞는 최상위이면서) 3글자 이상인 폴더 이름 한 칸이 통째로 맞은 것이
    없으면 약하다(검색 대상)."""
    roots = KIND_ROOTS.get(kind)
    ok = [(p, s, f) for p, s, f in sc if roots is None or p.split("/")[0] in roots]
    return not ok or not any(f for _, _, f in ok[:top])


def search_candidates(folders: FolderIndex, client: SearxClient, name: str, text: str, k: int, max_q: int,
                      log, kind: str = "") -> tuple[list[str], dict]:
    """약한 후보만 검색해 결과 낱말로 후보를 다시 뽑는다. (후보, 기록)
    검색어: 정리한 이름 → (첫 검색이 후보를 하나도 못 올렸고 애니·드라마로 보이면) "<제목> 애니|드라마" → 가장 긴 덩어리
    → + wiki, 파일당 max_q개까지. 좋은 후보가 나오면 멈춘다."""
    sc = folders.scored(text, k=k)
    orig = [p for p, _, _ in sc]
    info: dict = {"weak": _weak(sc, kind), "searches": [], "improved": False}
    if not info["weak"]:
        return orig, info
    results: list[dict] = []
    t0 = time.monotonic()
    info["genre"] = _genre(name)
    queue = make_queries(name)
    used: list[str] = []
    while queue and len(used) < max_q:
        q = queue.pop(0)
        if q in used:
            continue
        used.append(q)
        r = client.search(q)
        results += r["results"]
        keep = [x for x in r["results"] if not NS.is_brave(x.get("engines"))]   # Brave 결과 제목·주소는 기록 안 함
        info["searches"].append({"query": q, "n": len(r["results"]), "elapsed_s": r["elapsed_s"], "error": r["error"],
                                 "cached": bool(r.get("cached")),
                                 "titles": [x["title"][:80] for x in keep[:5]],
                                 **({"brave_results": len(r["results"]) - len(keep)} if len(keep) < len(r["results"]) else {})})
        log({"file": name, **info["searches"][-1], "urls": [x["url"] for x in keep],
             "attempts": r.get("attempts", [])})
        terms = result_terms(results)
        sc2 = folders.scored(text + " " + terms, k=k) if terms else sc
        raised = bool({p for p, _, _ in sc2} - set(orig))
        if not _weak(sc2, kind):
            sc, info["improved"] = sc2, True
            break
        if terms:
            sc = sc2
        # 한국어 두 번째 검색어는 첫 검색이 후보를 하나도 올리지 못했을 때만
        if len(used) == 1 and not raised and info["genre"]:
            kq = korean_query(name, info["genre"])
            if kq:
                queue.insert(0, kq)
                info["korean_query"] = kq
    info["terms"] = result_terms(results)[:400]
    info["search_s"] = round(time.monotonic() - t0, 1)
    info["top_titles"] = list(dict.fromkeys(x["title"][:80] for x in results if x["title"]))[:5]
    info["_result_text"] = " ".join(x["title"] + " " + x["content"] for x in results)
    # 검색 낱말의 잡음이 원래 후보를 밀어내지 않게: 원래 후보 상위 절반을 먼저 두고 검색으로 찾은 후보를 덧붙인다.
    merged = list(dict.fromkeys(orig[: (k + 1) // 2] + [p for p, _, _ in sc]))[:k]
    return merged, info


def run_media(profile: Profile, backend: Backend, input_path: Path, runs_dir: Path, batch_size: int, rules_only: bool,
              use_original: bool = False, folders: FolderIndex | None = None, k: int = 15,
              web_candidates: bool = False, web_cache: Path | None = None) -> int:
    lines, truth = read_input(input_path)
    names = [original_name(n) for n in lines] if use_original else lines
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = runs_dir / f"media-{stamp}{'-orig' if use_original else ''}"
    while out_dir.exists():  # -n 반복에서 같은 초에 시작하는 경우
        time.sleep(1)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = runs_dir / f"media-{stamp}{'-orig' if use_original else ''}"
    out_dir.mkdir(parents=True)
    print(f"stewart: media 기록 {out_dir}", file=sys.stderr)

    results: dict[str, dict] = {}  # 이름별 1회만 분류(같은 이름 여러 줄은 같은 제안)
    pending: list[tuple[str, dict]] = []
    for n in dict.fromkeys(names):
        r, h = classify(n)
        if r:
            results[n] = {"file": n, **r, "source": "rule"}
        else:
            pending.append((n, h))

    layout_p = profile.workdir / "library-layout.md"
    layout = layout_p.read_text(encoding="utf-8") if layout_p.exists() else "(서고 구조 문서 없음)"
    cands: dict[str, list[str]] | None = None
    websum = None
    forced_n: dict[str, int] = {}
    if folders is not None:
        cands = {}
        client = None
        webinfo: dict[str, dict] = {}
        if web_candidates:
            wc = engine.web_search_config(profile)
            if not wc or not wc["url"]:
                raise SystemExit("웹 검색 후보 단계: 검색 주소가 없다(STEWART_SEARXNG_URL)")
            client = SearxClient(wc["url"], wc.get("categories", ""), wc["timeout_s"], cache_path=web_cache)
            wlog = (out_dir / "web.jsonl").open("a", encoding="utf-8")
            evlog = (out_dir / "events.jsonl").open("a", encoding="utf-8")

            def log(e: dict) -> None:
                e = {"ts": datetime.now().isoformat(timespec="milliseconds"), **e}
                wlog.write(json.dumps(e, ensure_ascii=False) + "\n")
                wlog.flush()
                evlog.write(json.dumps({"ts": e["ts"], "kind": "web_search", "stage": "candidates", **e},
                                       ensure_ascii=False) + "\n")
                evlog.flush()
        for n, h in pending:
            text = " ".join([n] + [str(v) for kk, v in h.items() if kk not in ("종류", "확장자")])
            gtext = text
            if client:
                cands[n], info = search_candidates(folders, client, n, text, k, wc["max_per_file"] or 3, log, h.get("종류", ""))
                if info["searches"]:
                    h["웹 검색 결과 제목"] = " / ".join(info["top_titles"][:3]) or "(결과 없음)"
                gtext = text + " " + info.pop("_result_text", "")
                webinfo[n] = info
                print(f"검색 {n[:40]!r}: {len(info['searches'])}회 {'개선' if info['improved'] else ''}", file=sys.stderr)
            else:
                cands[n] = folders.candidates(text, k=k)
            # 이름·검색 결과에 그룹 이름이 나오면 그룹 폴더(Concerts/<그룹> 등)를 후보에 반드시 넣는다
            groups = folders.find_groups(gtext)
            forced = [p for p in folders.group_folders(groups) if p not in cands[n]]
            if groups:
                h["그룹 후보"] = ", ".join(groups)
            if forced:
                keep = max(0, k - len(forced))
                cands[n] = cands[n][:keep] + forced if len(cands[n]) > keep else cands[n] + forced
            forced_n[n] = len(forced)
        if client:
            wlog.close()
            evlog.close()
            websum = {"client": client.summary(), "files_weak": sum(1 for i in webinfo.values() if i["weak"]),
                      "files_searched": sum(1 for i in webinfo.values() if i["searches"]),
                      "files_improved": sum(1 for i in webinfo.values() if i["improved"]),
                      "searches_per_file": dict(sorted(Counter(len(i["searches"]) for i in webinfo.values()).items())),
                      "search_s_total": round(sum(i.get("search_s", 0) for i in webinfo.values()), 1),
                      "per_file": webinfo}
    web = 0 if web_candidates else engine.web_search_enabled(profile)
    if web_candidates:  # 검색은 후보 단계에서 끝냈다 — 모델에는 검색 도구를 주지 않는다
        profile = dataclasses.replace(profile, tools=[t for t in profile.tools if t != "web_search"])
    batches = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
    bstats = []
    if not rules_only:
        for bi, batch in enumerate(batches, 1):
            rec = Recorder(out_dir, f"batch{bi:02d}")
            s = engine.run(profile, backend, _batch_task(layout, batch, cands, web), rec)
            got = {}
            outp = rec.dir / "output.json"
            if outp.exists():
                for it in json.loads(outp.read_text(encoding="utf-8")).get("items", []):
                    got.setdefault(it.get("file"), it)
            want = {n for n, _ in batch}
            for n, h in batch:
                if n in got:
                    it = got[n]
                    results[n] = {"file": n, "destination": it["destination"], "reason": it["reason"],
                                  "confidence": it["confidence"], "source": "model", "hints": h}
                else:
                    results[n] = {"file": n, "destination": "_review/", "reason": "모델 답에 없어 검토로 보냄.",
                                  "confidence": 0.0, "source": "fallback", "hints": h}
            bstats.append({"batch": bi, "files": len(batch), "dir": rec.dir.name, "stop_reason": s["stop_reason"],
                           "steps": s["steps"], "elapsed_s": round(s["elapsed_s"], 1), "parse_failures": s["parse_failures"],
                           "format_failures": s["format_failures"], "language_retries": s["language_retries"],
                           "missing": len(want - set(got)), "extra": sorted(set(got) - want),
                           "gen_tok_per_s": s["gen_tok_per_s"], "web_searches": len(s.get("web_searches") or [])})
            print(json.dumps(bstats[-1], ensure_ascii=False), file=sys.stderr)
    else:
        for n, h in pending:
            results[n] = {"file": n, "destination": "_review/", "reason": "규칙으로 정하지 못했다(모델 미사용).",
                          "confidence": 0.0, "source": "rules-only", "hints": h}

    for n, it in results.items():
        if folders is not None:
            fx = fix_episode_season(it["destination"], n, folders)
            if fx:
                it.update({"original_destination": it["destination"], "destination": fx[0],
                           "source": it["source"] + "→season", "warning": fx[1],
                           "reason": f"[검사] {fx[1]}. (원래 근거: {it['reason']})"})
                continue
        if it["source"] == "model" and it["destination"].strip("/").split("/")[0] == "_junk":
            it.update({"original_destination": it["destination"], "destination": "_review/", "source": "model→review",
                       "warning": "모델은 _junk를 고를 수 없다(규칙만)",
                       "reason": f"[검사] 모델은 _junk를 고를 수 없어 검토로 바꿈. (원래 근거: {it['reason']})"})
            continue
        problem = check_destination(it["destination"])
        if problem:
            it.update({"original_destination": it["destination"], "destination": "_review/",
                       "confidence": min(float(it["confidence"]), 0.3), "source": it["source"] + "→review",
                       "warning": problem, "reason": f"[검사] {problem} → 검토로 바꿈. (원래 근거: {it['reason']})"})
    for n, it in results.items():
        if folders is not None:
            special = it["destination"].strip("/").split("/")[0].startswith("_")
            it["new_folder"] = (not special) and not folders.exists(it["destination"])
        if cands is not None and n in cands:
            it["candidates"] = cands[n]
        if websum and n in websum["per_file"]:
            it["web"] = websum["per_file"][n]
    items = [dict(results[n]) for n in names]
    by_src: dict[str, int] = {}
    for it in items:
        by_src[it["source"]] = by_src.get(it["source"], 0) + 1
    summary = {"input": str(input_path), "use_original": use_original, "lines": len(names), "unique_names": len(results),
               "folder_index": len(folders) if folders is not None else None, "candidates_k": k if folders is not None else None,
               "new_folder_lines": sum(1 for it in items if it.get("new_folder")), "web_search_per_file": web,
               "web_candidates": {kk: v for kk, v in websum.items() if kk != "per_file"} if websum else None,
               "group_forced_files": sum(1 for v in forced_n.values() if v) if folders is not None else None,
               "by_source": by_src, "batch_size": batch_size, "batches": bstats, "backend": backend.name,
               "model": backend.model, "precision": backend.precision,
               "model_time_s": round(sum(b["elapsed_s"] for b in bstats), 1)}
    (out_dir / "result.json").write_text(json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# media 분류 제안(dry-run) — {input_path.name}{' (원본 이름만)' if use_original else ''}, {len(names)}줄\n",
          f"- 출처별(줄 기준): {by_src}, 모델 묶음 {len(bstats)}개(크기 {batch_size}), 모델 시간 합 {summary['model_time_s']}초\n",
          "| # | 파일 | 목적지 | 신뢰도 | 출처 | 근거 |", "|---|---|---|---|---|---|"]
    md += [f"| {i} | {_cell(it['file'])} | {_cell(it['destination'])} | {it['confidence']:.2f} | "
           f"{it['source']}{'/' + it['rule'] if 'rule' in it else ''}{' ⚠' + it['warning'] if 'warning' in it else ''}"
           f"{' 🆕새 폴더' if it.get('new_folder') else ''} | {_cell(it['reason'])} |"
           for i, it in enumerate(items, 1)]
    if any(truth):
        sc = score(items, truth, names)
        (out_dir / "score.json").write_text(json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
        md += ["", "## 정답 대조", "", f"- {sc['summary_line']}", ""]
        for title, tbl in (("전체", sc["by_source"]), ("판별 불가 제외", sc["by_source_determinable"])):
            md += [f"### {title}", "", "| 출처 | 개수 | 정확 일치 | 최상위 일치 | _review | 틀림 |", "|---|---|---|---|---|---|"]
            md += [f"| {k} | {v['n']} | {v['exact']} | {v['top']} | {v['review']} | {v['wrong']} |" for k, v in tbl.items()] + [""]
        md += ["### 줄별 대조", "", "| # | 파일 | 제안 | 정답 | 출처 | 결과 | 판별 불가 |", "|---|---|---|---|---|---|---|"]
        md += [f"| {i} | {_cell(r['file'])} | {_cell(r['pred'])} | {_cell(r['truth'])} | {r['source']} | "
               f"{'정확' if r['exact'] else '_review' if r['review'] else '최상위만' if r['top'] else '틀림'} | {r['indeterminate'] or ''} |"
               for i, r in enumerate(sc["rows"], 1)]
        print(sc["summary_line"])
    (out_dir / "result.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("lines", "unique_names", "by_source", "model_time_s")}, ensure_ascii=False))
    print(f"result: {out_dir / 'result.md'}")
    out = {"dir": str(out_dir), "model_time_s": summary["model_time_s"], "new_folder_lines": summary["new_folder_lines"],
           "web_requests": (summary["web_candidates"] or {}).get("client", {}).get("requests")}
    if any(truth):
        out.update(all=sc["by_source"]["전체"], determinable=sc["by_source_determinable"]["전체"],
                   model=sc["by_source"].get("model"), cand_hit=[r["cand_hit"] for r in sc["rows"] if r["cand_hit"] is not None].count(True))
    return out
