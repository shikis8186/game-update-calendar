"""告知文のタイトル・本文から日時を読み取る。

推測で日付を作らないよう、はっきり日付として書かれている書式だけを読み取る。
対応する書式の例:
  2026/09/23 06:00 (UTC+8)      2026年10月13日      2026.9.23
  9月17日(木)11:00～15:00        [10/01]              10/01 … (JST 14:00 - 17:30)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo

from .common import JST

_WD = r"(?:\s*[(（]\s*[月火水木金土日](?:曜日?)?\s*[)）])?"
_SUFFIX = (
    _WD
    + r"(?:\s*(?P<H>\d{1,2})\s*[:：]\s*(?P<M>\d{2}))?"
    + r"(?:\s*(?:[~～〜\-－–]|から)\s*"
    + r"(?:(?P<m2>\d{1,2})\s*[/月]\s*(?P<d2>\d{1,2})\s*日?" + _WD + r"\s*)?"
    + r"(?P<H2>\d{1,2})\s*[:：]\s*(?P<M2>\d{2}))?"
    + r"(?:\s*[(（]\s*(?P<tz>UTC\s*[+＋]\s*[89]|GMT\s*[+＋]\s*[89]|JST|日本時間)\s*[)）])?"
)

# 年あり（2026/09/23, 2026年10月13日, 2026.9.23, 2026-09-23）
_RE_YMD = re.compile(
    r"(?<!\d)(?P<y>20\d{2})\s*[/.年\-]\s*(?P<m>\d{1,2})\s*[/.月\-]\s*(?P<d>\d{1,2})(?!\d)\s*日?" + _SUFFIX
)
# 年なし（9月17日）
_RE_MD_JA = re.compile(r"(?<![\d/.])(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日" + _SUFFIX)
# 年なし（10/01）。バージョン番号や時刻と間違えないよう、前後に数字・記号がないものに限る
_RE_MD_SLASH = re.compile(r"(?<![\d/.:])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?![\d/])" + _SUFFIX)
# 「(JST 14:00 - 17:30)」「(UTC 05:00 - 07:00)」のように日付と離れて書かれた時間帯
_RE_PAREN_RANGE = re.compile(
    r"[(（]\s*(?P<tz>JST|日本時間|UTC\s*[+＋]\s*\d{1,2}|GMT\s*[+＋]\s*\d{1,2}|UTC|GMT)\s*"
    r"(?P<H>\d{1,2})\s*[:：]\s*(?P<M>\d{2})\s*[~～〜\-－–]\s*"
    r"(?P<H2>\d{1,2})\s*[:：]\s*(?P<M2>\d{2})\s*[)）]"
)
_RE_UNTIL = re.compile(r"^\s*(?:まで|迄|until)", re.I)


@dataclass
class DateHit:
    start: datetime
    end: datetime | None
    has_time: bool
    until: bool          # 「〇日まで」のように期限を表している
    pos: int
    endpos: int
    raw: str


def _tz_of(label: str | None, default: tzinfo) -> tzinfo:
    if not label:
        return default
    label = label.replace(" ", "").replace("＋", "+").upper()
    if label in ("JST", "日本時間"):
        return JST
    m = re.fullmatch(r"(?:UTC|GMT)(?:\+(\d{1,2}))?", label)
    if m:
        return timezone(timedelta(hours=int(m.group(1) or 0)))
    return default


def _infer_year(month: int, day: int, ref: date) -> int | None:
    """年の書かれていない日付は、基準日（告知の掲載日）に最も近い年として解釈する。"""
    best: tuple[int, int] | None = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            diff = abs((date(y, month, day) - ref).days)
        except ValueError:
            continue
        if best is None or diff < best[0]:
            best = (diff, y)
    return best[1] if best else None


def _make(y: int, m: int, d: int, hh: str | None, mm: str | None, tz: tzinfo) -> datetime | None:
    try:
        base = datetime(y, m, d, tzinfo=tz)
    except ValueError:
        return None
    if hh is None:
        return base
    h, mi = int(hh), int(mm or 0)
    if h > 24 or mi > 59:
        return None
    return base + timedelta(hours=h, minutes=mi)


def find_datetimes(text: str, ref: datetime, default_tz: tzinfo = JST) -> list[DateHit]:
    hits: list[DateHit] = []
    taken: list[tuple[int, int]] = []
    ref_date = ref.astimezone(default_tz).date() if ref.tzinfo else ref.date()

    def overlaps(a: int, b: int) -> bool:
        return any(a < e and s < b for s, e in taken)

    for regex in (_RE_YMD, _RE_MD_JA, _RE_MD_SLASH):
        for m in regex.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            g = m.groupdict()
            month, day = int(g["m"]), int(g["d"])
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            year = int(g["y"]) if g.get("y") else _infer_year(month, day, ref_date)
            if year is None:
                continue
            tz = _tz_of(g.get("tz"), default_tz)
            start = _make(year, month, day, g.get("H"), g.get("M"), tz)
            if start is None:
                continue
            end = None
            if g.get("H2"):
                if g.get("m2"):
                    m2, d2 = int(g["m2"]), int(g["d2"])
                    y2 = year if (m2, d2) >= (month, day) else year + 1
                else:
                    y2, m2, d2 = year, month, day
                end = _make(y2, m2, d2, g["H2"], g["M2"], tz)
                if end is not None and end < start:
                    end = end + timedelta(days=1)  # 23:00～翌2:00 のような表記
            hits.append(
                DateHit(
                    start=start.astimezone(JST),
                    end=end.astimezone(JST) if end else None,
                    has_time=g.get("H") is not None,
                    until=bool(_RE_UNTIL.match(text[m.end():m.end() + 6])),
                    pos=m.start(),
                    endpos=m.end(),
                    raw=m.group(0),
                )
            )
            taken.append((m.start(), m.end()))

    hits.sort(key=lambda h: h.pos)
    # 日付と離れて書かれた「(JST 14:00 - 17:30)」は、時刻のない最初の日付に当てはめる
    pr = _RE_PAREN_RANGE.search(text)
    if pr:
        tz = _tz_of(pr["tz"], default_tz)
        for h in hits:
            if not h.has_time:
                local = h.start.astimezone(default_tz)
                d0 = datetime(local.year, local.month, local.day, tzinfo=tz)
                h.start = (d0 + timedelta(hours=int(pr["H"]), minutes=int(pr["M"]))).astimezone(JST)
                h.end = (d0 + timedelta(hours=int(pr["H2"]), minutes=int(pr["M2"]))).astimezone(JST)
                if h.end < h.start:
                    h.end += timedelta(days=1)
                h.has_time = True
                h.raw = f"{h.raw.strip()} {pr.group(0)}"
                break
    return hits


def context(text: str, hit: DateHit, before: int = 60, after: int = 60) -> str:
    return text[max(0, hit.pos - before): hit.endpos + after]


def norm(s: str) -> str:
    """全角数字・全角記号を半角にそろえる（日付の読み取り前に必ず通す）。"""
    import unicodedata

    return unicodedata.normalize("NFKC", s or "")


_TAG_RE = re.compile(r"<[^>]+>")
_BB_RE = re.compile(r"\[/?[a-zA-Z0-9*]+(?:=[^\]]*)?\]")


def html_to_text(s: str) -> str:
    import html as _html

    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t　]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def bbcode_to_text(s: str) -> str:
    """Steam のお知らせ本文（BBCode）から文字だけを取り出す。"""
    s = re.sub(r"\[img[^\]]*\].*?\[/img\]", "", s, flags=re.S | re.I)
    s = re.sub(r"\[previewyoutube[^\]]*\].*?\[/previewyoutube\]", "", s, flags=re.S | re.I)
    s = _BB_RE.sub("", s)
    s = re.sub(r"\{STEAM_CLAN_IMAGE\}\S*", "", s)
    return html_to_text(s)


def excerpt(text: str, limit: int = 140) -> str:
    one = re.sub(r"\s+", " ", text).strip()
    return one if len(one) <= limit else one[: limit - 1] + "…"
