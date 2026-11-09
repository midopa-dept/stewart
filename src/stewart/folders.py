"""서고 폴더 목록(색인): 기존 폴더 중 어디인가를 고르게 한다.

입력 파일: 한 줄에 서고 최상위 기준 상대 폴더 하나(예: "Variety/乃木坂46/乃木坂工事中"). 파일 이름은 없다.
- exists(): 제안 폴더가 목록에 있는지(없으면 new_folder)
- candidates(): 파일 이름·힌트에서 뽑은 낱말(로마자 낱말, 한중일 글자 2글자 묶음)이 폴더 경로에 얼마나 겹치는지로
  후보 몇십 개만 고른다. 모델 문맥 한도를 지키려고 목록 전체를 넘기지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_SEP = re.compile(r"[\s._\-\[\]()【】「」『』（）〈〉《》,!?！？~〜:：;'\"`+&/\\|#@]+")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힣豈-﫿]")
_NOISE = {"mp3", "mp4", "mkv", "avi", "ts", "flac", "jpg", "png", "gif", "webm", "1080p", "720p", "480p", "2160p",
          "x264", "x265", "h264", "aac", "bd", "hdtv", "bdrip", "320k", "kbps", "web", "dl", "the", "and", "disc", "cd",
          "ep", "vol", "feat", "ver", "version", "original", "album", "single"}


def _norm_text(s: str) -> str:
    return _SEP.sub(" ", unicodedata.normalize("NFKC", s).casefold()).strip()


def keys(text: str) -> set[str]:
    """비교용 낱말: 로마자·숫자 낱말(3자 이상 또는 숫자 섞인 2자 이상)과 한중일 글자 2글자 묶음."""
    out: set[str] = set()
    for tok in _norm_text(text).split():
        if _CJK.search(tok):
            cjk = "".join(c for c in tok if _CJK.match(c))
            out.update(cjk[i : i + 2] for i in range(len(cjk) - 1))
            rest = re.sub(_CJK, " ", tok).split()
            out.update(r for r in rest if len(r) >= 2)
        elif (len(tok) >= 3 or (len(tok) >= 2 and re.search(r"\d", tok) and re.search(r"[a-z]", tok))) \
                and tok not in _NOISE and not re.fullmatch(r"\d{1,3}", tok):
            out.add(tok)
    return out


def norm_path(p: str) -> str:
    return "/".join(re.sub(r"\s+", " ", unicodedata.normalize("NFKC", x)).strip().casefold()
                    for x in p.replace("\\", "/").split("/") if x.strip())


GROUP_ROOTS = ["Concerts", "Clips", "MusicVideos", "Variety", "Photos"]


class FolderIndex:
    def __init__(self, paths: list[str]):
        seen: dict[str, str] = {}
        for p in paths:
            p = p.strip().strip("/")
            if not p or p.startswith("#"):
                continue
            # 부모 폴더도 목록에 넣는다(목록이 잎 폴더만 있을 때 대비)
            parts = p.split("/")
            for i in range(1, len(parts) + 1):
                sub = "/".join(parts[:i])
                seen.setdefault(norm_path(sub), sub)
        self.paths = list(seen.values())
        self._norm = set(seen)
        self._keys = [keys(p) for p in self.paths]
        self._seg_keys = [[keys(s) for s in p.split("/")[1:]] for p in self.paths]  # 최상위 이름 제외
        # 칸 글자 수(공백·기호 뺀 NFKC). "통째로 맞음" 표시는 3글자 이상인 칸만 센다
        self._seg_len = [[len(_SEP.sub("", unicodedata.normalize("NFKC", s))) for s in p.split("/")[1:]] for p in self.paths]
        self._depth = [p.count("/") for p in self.paths]
        # 그룹 폴더: GROUP_ROOTS 바로 아래 폴더 이름 → [최상위/그룹 경로]
        self.groups: dict[str, list[str]] = {}
        for p in self.paths:
            parts = p.split("/")
            if len(parts) == 2 and parts[0] in GROUP_ROOTS and not parts[1].startswith("_"):
                self.groups.setdefault(parts[1], []).append(p)

    @classmethod
    def load(cls, path: Path) -> "FolderIndex":
        return cls(path.read_text(encoding="utf-8").splitlines())

    def __len__(self) -> int:
        return len(self.paths)

    def exists(self, dest: str) -> bool:
        return norm_path(dest) in self._norm

    def children(self, path: str) -> list[str]:
        """목록에 있는 바로 아래 폴더들."""
        base = norm_path(path)
        return [p for p in self.paths if norm_path(p).rpartition("/")[0] == base]

    def find_groups(self, text: str, top: int = 3) -> list[str]:
        """글(파일 이름·검색 결과)에 나온 그룹 이름을 많이 나온 순으로. 짧은 로마자(3자 미만)는 대소문자까지 같고
        앞뒤가 끊긴 낱말일 때만 센다(IU·BoA 오탐 방지)."""
        nfkc = unicodedata.normalize("NFKC", text)
        low = nfkc.casefold()
        found = []
        for g in self.groups:
            gn = unicodedata.normalize("NFKC", g)
            if _CJK.search(gn):
                n = low.count(gn.casefold())
            elif len(re.sub(r"[^A-Za-z0-9]", "", gn)) < 3:
                n = len(re.findall(r"(?<![A-Za-z0-9])" + re.escape(gn) + r"(?![A-Za-z0-9])", nfkc))
            else:
                n = len(re.findall(r"(?<![a-z0-9])" + re.escape(gn.casefold()) + r"(?![a-z0-9])", low))
            if n:
                found.append((-n, -len(gn), g))
        found.sort()
        # 더 긴 이름에 포함된 짧은 이름(嵐×乃木坂46 ⊃ 乃木坂46)은 둘 다 둔다 — 실제로 나온 횟수로 정렬
        return [g for _, _, g in found[:top]]

    def group_folders(self, groups: list[str]) -> list[str]:
        return [p for g in groups for p in self.groups.get(g, [])]

    def candidates(self, text: str, roots: list[str] | None = None, k: int = 20) -> list[str]:
        return [p for p, _, _ in self.scored(text, roots, k)]

    def scored(self, text: str, roots: list[str] | None = None, k: int = 20) -> list[tuple[str, float, bool]]:
        """[(경로, 점수, 3글자 이상인 폴더 이름 한 칸이 통째로 맞았는지)] 점수 높은 순."""
        q = keys(text)
        if not q:
            return []
        roots_cf = {r.casefold() for r in roots or []}
        scored = []
        for p, pk, d, segs, slen in zip(self.paths, self._keys, self._depth, self._seg_keys, self._seg_len):
            if roots_cf and p.split("/")[0].casefold() not in roots_cf:
                continue
            hit = q & pk
            if not hit:
                continue
            # 겹친 낱말 길이 합 + 겹친 비율. 같은 점수면 얕은 폴더(그룹·작품 단위)를 먼저.
            s = sum(len(h) for h in hit) + 3 * len(hit) / len(pk | q) * 10
            # 폴더 이름 한 칸이 통째로 이름에 들어 있으면 가산(그룹·작품 폴더를 위로). 마지막 칸이면 더.
            full = [bool(sk) and sk <= q for sk in segs]
            s += 8 * sum(full) + (12 if full and full[-1] else 0)
            strong = any(f and n >= 3 for f, n in zip(full, slen))
            scored.append((-s, d, p, strong))
        scored.sort()
        return [(p, -ns, f) for ns, _, p, f in scored[:k]]
