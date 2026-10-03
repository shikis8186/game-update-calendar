"""ストリートファイター6 の公式サイト（Buckler's Boot Camp）から、メンテナンスとアップデートを取得する。

「お知らせ」の「アップデート・メンテナンス」分類を読む。
  - メンテナンスのお知らせ: 1 日〜数日前に掲載。「JST 2026年9月8日(火) 12:00〜16:00」のように時間帯が書かれている
  - アップデートのお知らせ: メンテナンス後に掲載（実施済みの確認に使う）
SF6 はサーバー側だけの更新も多い（例: 2026/9/8 は Steam のゲーム本体の更新なし）。
ダウンロードの有無は告知の文面では決めず、Steam のビルド更新との突き合わせで判定する。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from ..common import FetchError, fmt_dt, from_ts, request_text
from ..model import DL_UNKNOWN, KIND_MAINT, ST_ANNOUNCED, ST_CONFIRMED, ST_SCHEDULED, Item
from ..textparse import excerpt, find_datetimes, html_to_text, norm

LIST_URL = "https://www.streetfighter.com/6/buckler/ja-jp/information/update_maintenance/{page}"
DETAIL_URL = "https://www.streetfighter.com/6/buckler/ja-jp/information/detail/{slug}"
HEADERS = {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ja"}

RE_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
RE_JST_LINE = re.compile(r"JST\s*(20\d{2}年\d{1,2}月\d{1,2}日[^\n]*?\d{1,2}:\d{2}\s*[〜~\-]\s*\d{1,2}:\d{2})")


def fetch_notices(since: datetime, max_pages: int = 3) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        html_text = request_text(LIST_URL.format(page=page), headers=HEADERS)
        m = RE_NEXT.search(html_text)
        if not m:
            raise FetchError("SF6 公式サイトのお知らせを読み取れませんでした（ページの構成が変わった可能性）")
        props = (json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}
        batch = props.get("info_list") or []
        for it in batch:
            if it.get("slug") and it.get("title") and it.get("releaseDate"):
                out.append({"slug": it["slug"], "title": it["title"], "posted": from_ts(it["releaseDate"]),
                            "text": norm(html_to_text(it.get("body") or ""))})
        if not batch or page >= int(props.get("max_page") or 1) or min(i["posted"] for i in out) < since:
            break
    if not out:
        raise FetchError("SF6 公式サイトのお知らせが空です")
    return out


def _window(text: str, posted: datetime):
    m = RE_JST_LINE.search(text)
    if not m:
        return None
    hits = find_datetimes(m.group(1), posted)
    return hits[0] if hits and hits[0].has_time else None


def build_items(game_id: str, notices: list[dict], now: datetime, since: datetime) -> list[Item]:
    # 同じ日のメンテナンスについての「メンテナンスのお知らせ」と「アップデートのお知らせ」を 1 件にまとめる
    groups: dict = {}
    for n in sorted(notices, key=lambda x: x["posted"]):
        w = _window(n["text"], n["posted"])
        if not w:
            continue
        g = groups.setdefault(w.start.date(), {"window": w, "maint": None, "update": None})
        if "アップデート" in n["title"] and "アップデートされました" in n["text"]:
            g["update"] = n
        else:
            g["maint"] = n
            g["window"] = w  # 予定の時間帯はメンテナンスのお知らせの記載を優先する
    items: list[Item] = []
    for day, g in sorted(groups.items()):
        w = g["window"]
        if w.start < since:
            continue
        main = g["update"] or g["maint"]
        title = f"{main['title']}（{day.month}月{day.day}日 {w.start:%H:%M}〜{w.end:%H:%M}）" if w.end else main["title"]
        it = Item(id=f"sf6-{main['slug']}", game=game_id, kind=KIND_MAINT, title=title, start=w.start, end=w.end,
                  dl=DL_UNKNOWN, source_key="sf6_official", posted=main["posted"])
        for n in (g["maint"], g["update"]):
            if n:
                it.add_basis(f"SF6 公式（Buckler's Boot Camp）「{n['title']}」（{fmt_dt(n['posted'])} 掲載）")
        it.add_basis(f"告知に記載のメンテナンス日時「JST {w.raw.strip()}」")
        if g["update"]:
            it.status = ST_CONFIRMED
            it.add_basis("公式告知でアップデートの実施を確認")
        else:
            it.status = ST_SCHEDULED if w.start > now else ST_ANNOUNCED
        it.add_basis("ダウンロードの有無は Steam のゲーム本体の更新で判定します（SF6 はサーバー側だけの更新もあるため）")
        it.excerpt = excerpt(main["text"])
        for n in (g["maint"], g["update"]):
            if n:
                it.add_source(f"SF6 公式「{n['title']}」", DETAIL_URL.format(slug=n["slug"]))
        items.append(it)
    return items
