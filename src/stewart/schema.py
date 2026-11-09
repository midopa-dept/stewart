"""작은 JSON 스키마 검사기(type·properties·required·items·enum·minimum·maximum·
additionalProperties·minItems 정도만). 외부 의존성을 피하려고 직접 둔다."""

from __future__ import annotations

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(v, t: str) -> bool:
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(v, s: dict, path: str = "$") -> list[str]:
    errs: list[str] = []
    t = s.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        if not any(_is_type(v, x) for x in ts):
            return [f"{path}: {t} 가 아니다 ({type(v).__name__})"]
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: {s['enum']} 중 하나가 아니다 ({v!r})")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errs.append(f"{path}: {s['minimum']} 보다 작다")
        if "maximum" in s and v > s["maximum"]:
            errs.append(f"{path}: {s['maximum']} 보다 크다")
    if isinstance(v, dict):
        props = s.get("properties") or {}
        for k in s.get("required") or []:
            if k not in v:
                errs.append(f"{path}: '{k}' 가 없다")
        for k, val in v.items():
            if k in props:
                errs += validate(val, props[k], f"{path}.{k}")
            elif s.get("additionalProperties") is False:
                errs.append(f"{path}: 정의되지 않은 키 '{k}'")
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errs.append(f"{path}: 항목이 {s['minItems']} 개보다 적다")
        if "items" in s:
            for i, x in enumerate(v):
                errs += validate(x, s["items"], f"{path}[{i}]")
    return errs
