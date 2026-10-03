"""鉄拳8 の公式サイトから、アップデートの版・配信日時を取得する。

Steam のお知らせにはアップデートの告知がほとんど載らないため、次の 2 つを使う。
  - 公式の「アップデート履歴」: 版（V3.02.02）・配信日・対象機種。過去分もすべて載っている
  - 鉄拳シリーズ公式ニュースの RSS: 「■ 適用日時」「■ メンテナンス日時」など時刻まで分かる告知（直近 10 件程度）
事前の予告は公式 X（旧 Twitter）だけで行われるため、公式サイトに載るのは適用の当日になる。
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..common import JST, FetchError, request_text
from ..model import DL_YES, KIND_MAJOR, KIND_PATCH, ST_CONFIRMED, Item
from ..textparse import excerpt, find_datetimes, html_to_text, norm

HISTORY_URL = "https://tk8.tekken-official.jp/update/history.php"
NEWS_RSS = "https://www.tekken-official.jp/tekken_news/?feed=rss2"

RE_HISTORY = re.compile(
    r'<li(?P<cls>[^>]*)>\s*<a href="(?P<url>[^"]+)"[^>]*>\s*<div class="txt">\s*'
    r'<p class="ver">\s*V(?P<ver>\d+(?:\.\d+)+)\s*</p>\s*<p class="date">[^<]*?(?P<date>\d{4}/\d{1,2}/\d{1,2})\s*</p>'
    r'(?P<rest>.*?)</ul>',
    re.S,
)
RE_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
RE_UPDATE_TITLE = re.compile(r"TEKKEN 8.*?(?:Update Data|アップデートデータ)\s*Ver\.?\s*(\d+(?:\.\d+)+)", re.I)
RE_APPLY = re.compile(r"■\s*適用日時\s*\[JST\]\s*([^\n]+)")
RE_MAINT = re.compile(r"■\s*メンテナンス日時\s*\[JST\]\s*([^\n]+)")


def fetch_history() -> list[dict]:
    page = request_text(HISTORY_URL)
    out = []
    for m in RE_HISTORY.finditer(page):
        platforms = re.findall(r"<li>([^<]+)</li>", m["rest"])
        y, mo, d = (int(x) for x in m["date"].split("/"))
        out.append({"ver": m["ver"], "day": datetime(y, mo, d, tzinfo=JST), "url": html.unescape(m["url"]),
                    "steam": any("Steam" in p for p in platforms), "character": "charaBg" in m["cls"]})
    if not out:
        raise FetchError("鉄拳8 のアップデート履歴を読み取れませんでした（ページの構成が変わった可能性）")
    return out


def fetch_news() -> dict[str, dict]:
    """版ごとの、適用日時・メンテナンス日時（RSS に残っている直近分だけ）。"""
    feed = request_text(NEWS_RSS)
    if "<rss" not in feed:
        raise FetchError("鉄拳シリーズ公式ニュースの RSS を読み取れませんでした")
    out: dict[str, dict] = {}
    for item in RE_ITEM.findall(feed):
        title = html.unescape((re.search(r"<title>(.*?)</title>", item, re.S) or [None, ""])[1])
        m = RE_UPDATE_TITLE.search(title)
        if not m:
            continue
        link = (re.search(r"<link>(.*?)</link>", item) or [None, ""])[1].strip()
        pub = re.search(r"<pubDate>(.*?)</pubDate>", item)
        posted = parsedate_to_datetime(pub.group(1)).astimezone(JST) if pub else None
        body = re.search(r"<content:encoded><!\[CDATA\[(.*?)\]\]>", item, re.S)
        text = norm(html_to_text(body.group(1))) if body else ""
        ja = text[text.find("以下内容にて"):] if "以下内容にて" in text else text  # 英語と日本語が併記されている
        out[m.group(1)] = {"title": title.split("/")[-1].strip(), "url": link, "posted": posted, "text": ja}
    return out


def build_items(game_id: str, history: list[dict], news: dict[str, dict], now: datetime,
                since: datetime) -> list[Item]:
    items: list[Item] = []
    for h in history:
        if h["day"] < since or not h["steam"]:
            continue
        ver = h["ver"]
        n = news.get(ver)
        title = n["title"] if n and n["title"] else f"アップデートデータ Ver.{ver}"
        it = Item(id=f"tk8-v{ver}", game=game_id, kind=KIND_MAJOR if h["character"] else KIND_PATCH, title=title,
                  start=h["day"], all_day=True, dl=DL_YES, status=ST_CONFIRMED, source_key="tekken8")
        it.add_basis(f"鉄拳8 公式のアップデート履歴: V{ver}（配信 {h['day']:%Y/%m/%d}・Steam 版を含む）")
        it.add_basis("公式に「アップデートデータを配信」と記載（ゲーム本体の更新）")
        if h["character"]:
            it.add_basis("追加キャラクターに対応するアップデート")
        if n:
            ref = n["posted"] or h["day"]
            apply_line = RE_APPLY.search(n["text"])
            if apply_line:
                hit = next((x for x in find_datetimes(apply_line.group(1), ref) if x.has_time), None)
                if hit and hit.start.date() == h["day"].date():
                    it.start, it.all_day = hit.start, False
                    it.add_basis(f"公式告知の適用日時「{apply_line.group(1).strip()}」")
            maint_line = RE_MAINT.search(n["text"])
            if maint_line:
                it.add_basis(f"メンテナンス日時（公式告知）: {maint_line.group(1).strip()}")
            it.excerpt = excerpt(n["text"])
            it.add_source(f"鉄拳シリーズ公式ニュース「{n['title']}」", n["url"])
        it.add_source("鉄拳8 公式 アップデート履歴", HISTORY_URL)
        if h["url"] and (not n or h["url"] != n["url"]):
            it.add_source(f"鉄拳シリーズ公式ニュース（V{ver}）", h["url"])
        items.append(it)
    return items
