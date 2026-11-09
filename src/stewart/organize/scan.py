"""큰 폴더 훑기·집계. 결과는 줄마다 JSON 한 항목(jsonl)으로 root 밖 기록 폴더에 남기고, 모델에는 요약만 준다.

항목: {rel, key, type file|dir|link, size, mtime_ns, ext, kind, depth, to_delete, n(폴더: 안의 파일 수)}
- 심볼릭 링크는 따라가지 않는다(링크 자체만 적음). 내용은 읽지 않는다.
- root/_to_delete/ 안은 훑되 to_delete=true 로 표시한다(계획의 src 로는 받지 않는다).
- .git 폴더는 안으로 들어가지 않고 한 항목으로만 적는다.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import names

SCAN_MAX = 2_000_000
SIZE_BUCKETS = ((0, "0"), (1 << 10, "<1KB"), (1 << 20, "<1MB"), (10 << 20, "<10MB"), (100 << 20, "<100MB"),
                (1 << 30, "<1GB"), (float("inf"), "≥1GB"))
VIDEO = {"mkv", "mp4", "avi", "mov", "wmv", "m4v", "ts", "webm", "flv", "mpg", "mpeg", "m2ts"}
AUDIO = {"mp3", "flac", "m4a", "aac", "wav", "ogg", "opus", "wma", "ape", "alac"}
IMAGE = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif", "raw", "cr2", "nef", "arw", "dng"}
DOC = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "hwp", "hwpx", "odt", "rtf", "epub"}
TEXT = {"txt", "md", "csv", "tsv", "json", "xml", "yaml", "yml", "toml", "ini", "log", "html", "htm", "py", "js", "ts",
        "sh", "c", "h", "cpp", "java", "go", "rs", "rb", "srt", "ass", "smi", "vtt", "tex", "sql", "cfg", "conf"}
ARCHIVE = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "iso", "dmg"}


def ext_of(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot and stem and len(ext) <= 10 else ""


def kind_of(ext: str) -> str:
    for k, s in (("video", VIDEO), ("audio", AUDIO), ("image", IMAGE), ("doc", DOC), ("text", TEXT), ("archive", ARCHIVE)):
        if ext in s:
            return k
    return "other"


def bucket(size: int) -> str:
    if size == 0:
        return "0"
    for lim, label in SIZE_BUCKETS[1:]:
        if size < lim:
            return label
    return SIZE_BUCKETS[-1][1]


BUCKET_ORDER = {label: i for i, (_, label) in enumerate(SIZE_BUCKETS)}


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024
    return str(n)


def scan(root: Path, out_path: Path, limit: int = SCAN_MAX) -> dict:
    """root 를 훑어 out_path(jsonl)에 쓰고 요약(dict)을 돌려준다."""
    root = Path(root)
    fold = names.case_insensitive(root)
    t0 = time.monotonic()
    st = {"files": 0, "dirs": 0, "links": 0, "bytes": 0, "errors": 0, "to_delete_files": 0, "to_delete_bytes": 0,
          "stopped": False}
    top = defaultdict(lambda: [0, 0])          # 최상위 이름 → [파일 수, 바이트]
    exts: Counter = Counter()
    ext_bytes: Counter = Counter()
    years: Counter = Counter()
    kinds: Counter = Counter()
    largest: list[tuple[int, str]] = []
    deepest = (0, "")
    empty_dirs = 0
    clash: dict[str, set] = defaultdict(set)   # 같은 폴더 안 열쇠 → 원래 이름들(NFC·대소문자만 다른 이름)
    base_names: Counter = Counter()             # 다른 폴더에 같은 파일 이름(열쇠)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_items = 0
    with open(out_path, "w", encoding="utf-8", errors="surrogateescape") as out:

        def emit(rec: dict):
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

        def walk(d: Path, rel: str, depth: int, in_del: bool) -> tuple[int, int]:
            """(안의 파일 수, 바이트)"""
            nonlocal n_items, deepest, empty_dirs
            try:
                entries = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                st["errors"] += 1
                return 0, 0
            if not entries and rel:
                empty_dirs += 1
            nf = nb = 0
            for e in entries:
                if n_items >= limit:
                    st["stopped"] = True
                    break
                n_items += 1
                r = f"{rel}/{e.name}" if rel else e.name
                k = names.key(r, fold)
                clash[names.key(rel, fold) + "\0" + names.key(e.name, fold)].add(e.name)
                td = in_del or (not rel and names.key(e.name, fold) == names.key(names.TO_DELETE, fold))
                try:
                    s = e.stat(follow_symlinks=False)
                except OSError:
                    st["errors"] += 1
                    continue
                if e.is_symlink():
                    st["links"] += 1
                    emit({"rel": r, "key": k, "type": "link", "size": 0, "mtime_ns": s.st_mtime_ns, "ext": "",
                          "kind": "link", "depth": depth, "to_delete": td})
                    continue
                if e.is_dir(follow_symlinks=False):
                    st["dirs"] += 1
                    if e.name == ".git":
                        cf, cb = 0, 0          # 안으로 들어가지 않는다
                    else:
                        cf, cb = walk(Path(e.path), r, depth + 1, td)
                    emit({"rel": r, "key": k, "type": "dir", "size": cb, "mtime_ns": s.st_mtime_ns, "ext": "",
                          "kind": "dir", "depth": depth, "to_delete": td, "n": cf, "opaque": e.name == ".git"})
                    nf, nb = nf + cf, nb + cb
                    continue
                ext = ext_of(e.name)
                kind = kind_of(ext)
                size = s.st_size
                emit({"rel": r, "key": k, "type": "file", "size": size, "mtime_ns": s.st_mtime_ns, "ext": ext,
                      "kind": kind, "depth": depth, "to_delete": td})
                nf, nb = nf + 1, nb + size
                st["files"] += 1
                st["bytes"] += size
                if td:
                    st["to_delete_files"] += 1
                    st["to_delete_bytes"] += size
                t = top[r.split("/", 1)[0] if "/" in r else "(맨 위 파일)"]
                t[0] += 1
                t[1] += size
                exts[ext or "(없음)"] += 1
                ext_bytes[ext or "(없음)"] += size
                kinds[kind] += 1
                years[datetime.fromtimestamp(s.st_mtime_ns / 1e9).strftime("%Y")] += 1
                base_names[names.key(e.name, fold)] += 1
                largest.append((size, r))
                if len(largest) > 200:
                    largest.sort(reverse=True)
                    del largest[20:]
                if depth > deepest[0]:
                    deepest = (depth, r)
            return nf, nb

        walk(root, "", 1, False)
    largest.sort(reverse=True)
    clashes = [sorted(v) for v in clash.values() if len(v) > 1]
    summary = {
        "root": str(root), "case_insensitive": fold, "items": n_items, **st, "empty_dirs": empty_dirs,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "top": sorted(([k, v[0], v[1]] for k, v in top.items()), key=lambda x: -x[2])[:40],
        "ext": [[e, c, ext_bytes[e]] for e, c in exts.most_common(30)],
        "kind": kinds.most_common(), "year": sorted(years.items()),
        "largest": [[r, s] for s, r in largest[:20]], "deepest": list(deepest),
        "name_clashes": clashes[:20], "name_clash_count": len(clashes),
        "same_name_elsewhere": [[k, c] for k, c in base_names.most_common(10) if c > 1],
    }
    out_path.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def summary_text(sid: str, s: dict) -> str:
    L = [f"scan {sid}: 파일 {s['files']:,}개 {human(s['bytes'])}, 폴더 {s['dirs']:,}개, 링크 {s['links']}개, "
         f"빈 폴더 {s['empty_dirs']}개, 읽기 오류 {s['errors']}개, {s['elapsed_s']}초"
         + (" — 상한에 닿아 멈춤(결과가 더 있다)" if s["stopped"] else "")]
    L.append(f"대소문자 구분 없는 파일 시스템: {'예' if s['case_insensitive'] else '아니오'}")
    if s["to_delete_files"]:
        L.append(f"이미 {names.TO_DELETE}/ 안: 파일 {s['to_delete_files']}개 {human(s['to_delete_bytes'])} (계획의 src 로 못 씀)")
    L.append("\n최상위(파일 수, 크기):")
    L += [f"  {k}  {c:,}  {human(b)}" for k, c, b in s["top"][:25]]
    L.append("\n확장자(파일 수, 크기):")
    L.append("  " + ", ".join(f"{e} {c:,}/{human(b)}" for e, c, b in s["ext"][:30]))
    L.append("종류: " + ", ".join(f"{k} {c:,}" for k, c in s["kind"]))
    L.append("수정 연도: " + ", ".join(f"{y} {c:,}" for y, c in s["year"]))
    L.append("\n큰 파일:")
    L += [f"  {human(sz)}  {r}" for r, sz in s["largest"][:20]]
    if s["deepest"][1]:
        L.append(f"\n가장 깊은 경로({s['deepest'][0]}단): {s['deepest'][1]}")
    if s["name_clash_count"]:
        L.append(f"같은 폴더 안 이름 겹침(정규화·대소문자만 다름) {s['name_clash_count']}건: "
                 + "; ".join(" | ".join(x) for x in s["name_clashes"][:5]))
    if s["same_name_elsewhere"]:
        L.append("여러 폴더에 같은 파일 이름: " + ", ".join(f"{k} ×{c}" for k, c in s["same_name_elsewhere"]))
    L.append(f"\n목록은 scan_read(id='{sid}'), 다른 단면은 scan_stats(id='{sid}', group_by=…) 로 본다.")
    return "\n".join(L)


def iter_items(path: Path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        for line in f:
            yield json.loads(line)


def select(path: Path, fold: bool, under: str = "", glob: str = "", type_: str = "all", include_deleted: bool = True):
    from ..fswalk import path_rx
    u = names.key(under, fold) if under else ""
    rx = path_rx(names.key(glob, fold)) if glob else None
    for it in iter_items(path):
        if type_ != "all" and it["type"] != type_:
            continue
        if not include_deleted and it["to_delete"]:
            continue
        k = it["key"]
        if u and not (k == u or k.startswith(u + "/")):
            continue
        if rx:
            rel_k = k[len(u) + 1:] if u else k
            if not rx.match(rel_k):
                continue
        yield it


def line_of(it: dict) -> str:
    d = datetime.fromtimestamp(it["mtime_ns"] / 1e9).strftime("%Y-%m-%d")
    if it["type"] == "dir":
        return f"{it['rel']}/\t{human(it['size'])}\t{d}\t폴더 {it.get('n', 0):,}개 파일" + ("  [_to_delete]" if it["to_delete"] else "")
    tag = "  [_to_delete]" if it["to_delete"] else ""
    return f"{it['rel']}\t{human(it['size'])}\t{d}\t{it['kind']}{tag}"


def stats(path: Path, fold: bool, group_by: str, under: str = "", depth: int = 1, top: int = 50) -> list[tuple[str, int, int]]:
    g: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    u = names.key(under, fold) if under else ""
    for it in select(path, fold, under=under, type_="file"):
        if group_by == "ext":
            k = it["ext"] or "(없음)"
        elif group_by == "kind":
            k = it["kind"]
        elif group_by in ("year", "month"):
            k = datetime.fromtimestamp(it["mtime_ns"] / 1e9).strftime("%Y" if group_by == "year" else "%Y-%m")
        elif group_by == "size_bucket":
            k = bucket(it["size"])
        else:  # dir
            rel = it["rel"]
            if u:
                rel = rel.split("/", u.count("/") + 1)[-1]
            parts = rel.split("/")[:-1]
            k = "/".join(parts[:depth]) or "(바로 아래 파일)"
            if u:
                k = under.strip("/") + "/" + k if parts else k
        g[k][0] += 1
        g[k][1] += it["size"]
    rows = [(k, v[0], v[1]) for k, v in g.items()]
    if group_by == "size_bucket":
        rows.sort(key=lambda r: BUCKET_ORDER.get(r[0], 99))
    elif group_by in ("year", "month"):
        rows.sort(key=lambda r: r[0])
    else:
        rows.sort(key=lambda r: (-r[2], r[0]))
    return rows[:top]
