"""League of Legends の情報を取得する。

使う情報源:
  - Riot 公式のパッチ配信スケジュール（予定日。米国太平洋時間）
  - 公式サイト（日本語）のパッチノート一覧
  - Riot 公式のゲームデータ配信元 Data Dragon（実際に配信されたバージョンの確認）
LoL の定期パッチは毎回 Riot クライアントでのダウンロードが必要。
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime

from ..common import JST, FetchError, request_json, request_text
from ..model import DL_YES, KIND_PATCH, ST_ANNOUNCED, ST_CONFIRMED, ST_SCHEDULED, Item

SCHEDULE_URL = "https://support.riotgames.com/en-us/league-of-legends/gameplay/patch-schedule-league-of-legends"
NOTES_URL = "https://www.leagueoflegends.com/ja-jp/news/tags/patch-notes/"
SITE = "https://www.leagueoflegends.com"
DDRAGON_URL = "https://ddragon.leagueoflegends.com/api/versions.json"

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], start=1)}
RE_ROW = re.compile(r"(?<![\d.])(\d{2})\.(\d{1,2})\s*\|\s*([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})")
RE_NOTE_PATCH = re.compile(r"(\d{2})\.(\d{1,2})(?!\d)")


def fetch_schedule() -> list[tuple[tuple[int, int], datetime]]:
    page = request_text(SCHEDULE_URL)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " | ", text))
    text = re.sub(r"(\s*\|\s*)+", " | ", text)
    rows = []
    for m in RE_ROW.finditer(text):
        month = MONTHS.get(m.group(3))
        if not month:
            continue
        try:
            day = datetime(int(m.group(5)), month, int(m.group(4)), tzinfo=JST)
        except ValueError:
            continue
        rows.append(((int(m.group(1)), int(m.group(2))), day))
    if len(rows) < 10:
        raise FetchError("LoL のパッチスケジュール表を読み取れませんでした（ページの構成が変わった可能性）")
    return rows


def fetch_notes() -> dict[tuple[int, int], dict]:
    page = request_text(NOTES_URL)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise FetchError("LoL のパッチノート一覧を読み取れませんでした")
    data = json.loads(m.group(1))
    found: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if "publishedAt" in o and "title" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    notes: dict[tuple[int, int], dict] = {}
    for o in found:
        title = str(o.get("title") or "")
        url = ((o.get("action") or {}).get("payload") or {}).get("url") or ""
        pm = RE_NOTE_PATCH.search(title)
        if not pm or "patch" not in url:
            continue
        key = (int(pm.group(1)), int(pm.group(2)))
        notes.setdefault(key, {"title": title, "url": SITE + url if url.startswith("/") else url,
                               "published": o.get("publishedAt")})
    if not notes:
        raise FetchError("LoL のパッチノートが見つかりませんでした")
    return notes


def fetch_live_patches() -> set[tuple[int, int]]:
    """Data Dragon のバージョン（例: 16.19.1）を パッチ番号（26.19）に変換する。"""
    versions = request_json(DDRAGON_URL)
    if not isinstance(versions, list) or not versions:
        raise FetchError("Data Dragon のバージョン一覧が取得できませんでした")
    out = set()
    for v in versions:
        m = re.match(r"^(\d+)\.(\d+)\.\d+$", str(v))
        if m:
            out.add((int(m.group(1)) + 10, int(m.group(2))))
    return out


def build_items(game_id: str, schedule, notes, live: set[tuple[int, int]] | None,
                now: datetime, since: datetime) -> list[Item]:
    items: list[Item] = []
    for (yy, nn), day in schedule:
        if day.date() < since.date():
            continue
        label = f"{yy}.{nn:02d}"
        note = notes.get((yy, nn))
        title = note["title"] if note else f"パッチ {yy}.{nn}"
        it = Item(id=f"lol-{yy}-{nn}", game=game_id, kind=KIND_PATCH, title=title, start=day,
                  all_day=True, dl=DL_YES, source_key="lol")
        it.note = "日付は Riot 公式の予定日（米国太平洋時間）です。日本サーバーへの配信時刻は公式に告知されません。"
        it.add_basis(f"Riot 公式パッチスケジュール: パッチ {label} は {day:%Y/%m/%d}（太平洋時間）")
        it.add_basis("LoL の定期パッチは Riot クライアントでのダウンロードが必要")
        if live is not None and (yy, nn) in live:
            it.status = ST_CONFIRMED
            it.add_basis(f"Riot 公式データ（Data Dragon）でバージョン {yy - 10}.{nn} の配信を確認")
        else:
            it.status = ST_SCHEDULED if day.date() > now.date() else ST_ANNOUNCED
        it.add_source("Riot 公式パッチスケジュール", SCHEDULE_URL)
        if note:
            it.add_source(f"パッチノート（{note['title']}）", note["url"])
        items.append(it)
    return items
