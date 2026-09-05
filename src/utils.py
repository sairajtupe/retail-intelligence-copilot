"""Utility helpers."""
from __future__ import annotations
import hashlib, json, math, re
from datetime import date, datetime, timedelta
from typing import Any

def today() -> date:
    return date.today()

def parse_date(v: str) -> date | None:
    if v is None:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(v.strip(), fmt).date()
        except Exception:
            continue
    return None

def safe_div(num: float, den: float, eps: float = 1e-9) -> float:
    return num / max(den, eps)

def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default

def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def text_hash(texts, dim: int = 256) -> list[float]:
    """Deterministic bag-of-words embedding via hashing (L2-normalised).

    Accepts a single string or an iterable of strings.
    """
    if isinstance(texts, str):
        texts = [texts]
    vec = [0.0] * dim
    for t in texts:
        for tok in re.findall(r"[a-zA-Z0-9]+", t.lower()):
            idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % dim
            vec[idx] += 1.0
    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / mag for x in vec]

def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)

def jaccard_tokens(a: str, b: str) -> float:
    sa = set(re.findall(r"[a-zA-Z0-9]+", a.lower()))
    sb = set(re.findall(r"[a-zA-Z0-9]+", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)

def as_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    return [] if v is None else [v]

def fmt_num(x: Any, digits: int = 2) -> str:
    """Friendly number formatting for evidence text."""
    f = safe_float(x)
    if abs(f) >= 1_000_000:
        return f"{f/1_000_000:.2f}M"
    if abs(f) >= 1_000:
        return f"{f:,.0f}"
    if f == int(f):
        return f"{int(f)}"
    return f"{f:.{digits}f}"

def _indian_digits(v: float, decimals: int = 0) -> str:
    """Indian digit grouping (last 3 digits then groups of 2): 12,34,567."""
    s = f"{v:,.{decimals}f}"
    intpart, _, frac = s.partition(".")
    intpart = intpart.replace(",", "")
    if len(intpart) > 3:
        head, tail = intpart[-3:], intpart[:-3]
        groups = []
        while tail:
            groups.insert(0, tail[-2:])
            tail = tail[:-2]
        grouped = ",".join(groups + [head])
    else:
        grouped = intpart
    return f"{grouped}.{frac}" if decimals > 0 else grouped


def fmt_money(x: Any, digits: int = 2) -> str:
    f = safe_float(x)
    v = abs(f)
    if v >= 10_000_000:
        return f"₹{f/10_000_000:.2f}Cr"
    if v >= 100_000:
        return f"₹{f/100_000:.1f}L"
    if v >= 1000:
        return "₹" + _indian_digits(v, 0)
    if f == int(f):
        return f"₹{int(f):,d}"
    return "₹" + _indian_digits(v, digits)

def steady_id(*parts: Any) -> str:
    """Deterministic stable identifier for evidence/issue ids."""
    raw = "|".join(str(p) for p in parts if p is not None)
    return hashlib.md5(raw.encode()).hexdigest()[:12]

def overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end

def month_start(d: date) -> date:
    return d.replace(day=1)

def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, 28)
    return d.replace(year=y, month=m, day=day)
