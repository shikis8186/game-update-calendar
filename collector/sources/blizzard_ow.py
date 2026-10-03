"""オーバーウォッチの公式サイト（日本語）から、パッチの配信日とシーズンの開幕日を取得する。

Steam のお知らせには載らない告知（パッチノート、シーズン開幕の予告）を補うために使う。
  - パッチノート: 各パッチのタイトルに「2026年9月23日配信パッチ内容のおしらせ」と配信日が書かれている
  - ニュース    : 記事の本文に「10月7日開幕のシーズン5」のように開幕日が書かれていることがある
ダウンロードの有無は、Steam のビルド更新（実データ）との突き合わせで確定させる。
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta

from ..common import FetchError, JST, fmt_dt, request_text
from ..model import DL_LIKELY, KIND_MAJOR, KIND_PATCH, ST_ANNOUNCED, ST_SCHEDULED, Item
from ..textparse import excerpt, find_datetimes, html_to_text, norm

SITE = "https://overwatch.blizzard.com"
PATCH_URL = SITE + "/ja-jp/news/patch-notes/live/{y}/{m:02d}/"
NEWS_URL = SITE + "/ja-jp/news/"
NEWS_DAYS = 60  # ニュース記事の本文を読むのは、掲載から何日以内の記事か
NOTE_PENDING = "ダウンロードの有無は、配信後に Steam のビルド更新で確定します"

RE_PATCH = re.compile(
    r'<div class="anchor" id="(?P<anchor>patch-[\d-]+)"></div>\s*'
    r'<div class="PatchNotes-labels">\s*<div class="PatchNotes-date">(?P<posted>[^<]+)</div>\s*</div>\s*'
    r'<h3 class="PatchNotes-patchTitle">(?P<title>[^<]+)</h3>'
    r'(?P<body>.*?)(?=<div class="PatchNotes-patch|<div class="PatchNotesTop|\Z)',
    re.S,
)
RE_SECTION = re.compile(r'<h4 class="PatchNotes-sectionTitle">(.*?)</h4>', re.S)
RE_SEASON = re.compile(r"シーズン\s?(\d+)")
RE_CARD = re.compile(
    r'<a slot="gallery-items" href="(?P<href>/news/\d+/[^"]*)"[^>]*>.*?'
    r'<h3 slot="heading">(?P<title>.*?)</h3>.*?<blz-timestamp[^>]*timestamp="(?P<ts>[^"]+)"',
    re.S,
)
RE_ARTICLE = re.compile(r'<div class="article-content"[^>]*>(.*?)<div class="article-sidebar', re.S)
# 「10月7日開幕のシーズン5」「12月8日(火)より開始するシーズン6」
RE_OPEN_1 = re.compile(
    r"(?P<date>\d{1,2}月\d{1,2}日)(?:\s*\([^)]{1,8}\))?\s*(?:に|より|から)?\s*(?:開幕|開始|スタート)"
    r"(?:する|予定の|予定)?\s*の?\s*シーズン\s?(?P<n>\d+)"
)
# 「シーズン5は10月7日に開幕」「シーズン6「…」が12月8日(火)より開始」
RE_OPEN_2 = re.compile(
    r"シーズン\s?(?P<n>\d+)(?:\s*「[^」]{1,40}」)?\s*(?:は|が)\s*(?:日本時間\s*)?"
    r"(?P<date>(?:\d{4}年)?\d{1,2}月\d{1,2}日)(?:\s*\([^)]{1,8}\))?"
    r"(?:\s*(?:午前|午後)?\s*\d{1,2}(?::\d{2}|時))?\s*(?:に|より|から)?\s*(?:開幕|開始|スタート)"
)


def _month_starts(since: datetime, now: datetime) -> list[tuple[int, int]]:
    y, m = since.year, since.month
    out = []
    while (y, m) <= (now.year, now.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def parse_patch_page(page: str, url: str, now: datetime) -> list[Item]:
    items: list[Item] = []
    for m in RE_PATCH.finditer(page):
        title = html.unescape(m["title"]).strip()
        t = norm(title)
        if "配信" not in t or "パッチ" not in t:
            continue  # 「コンソール向け非許諾周辺機器に関するお知らせ」などはパッチではない
        posted_hits = find_datetimes(norm(m["posted"]), now)
        posted = posted_hits[0].start if posted_hits else now
        hits = find_datetimes(t, posted)
        if not hits:
            continue
        day = hits[0].start
        sections = [norm(html.unescape(re.sub(r"<[^>]+>", "", s))).strip() for s in RE_SECTION.findall(m["body"])]
        season = next((s for s in sections if RE_SEASON.search(s)), None)
        # 掲載日は日付だけで時刻がないため posted には入れない（判定根拠に日付だけを書く）
        it = Item(id=f"ow-{m['anchor']}", game="", kind=KIND_MAJOR if season else KIND_PATCH, title=title,
                  start=day, all_day=True, dl=DL_LIKELY, source_key="blizzard_ow")
        it.add_basis(f"Blizzard 公式パッチノート（{fmt_dt(posted)[:10]} 掲載）のタイトルに記載の配信日")
        if season:
            it.add_basis(f"新シーズンの開始を含むパッチ:「{season}」")
        it.add_basis(NOTE_PENDING)
        it.status = ST_SCHEDULED if day.date() > now.date() else ST_ANNOUNCED
        text = html_to_text(m["body"])
        it.excerpt = excerpt(norm(text)) if text else None
        it.add_source(f"Blizzard 公式パッチノート「{title}」", f"{url}#{m['anchor']}")
        items.append(it)
    return items


def fetch_patch_notes(since: datetime, now: datetime) -> list[Item]:
    items: list[Item] = []
    for y, m in _month_starts(since, now):
        url = PATCH_URL.format(y=y, m=m)
        page = request_text(url)
        if 'class="PatchNotes-list"' not in page:
            raise FetchError(f"オーバーウォッチのパッチノート（{y}年{m}月）の形式が想定と違います")
        items.extend(parse_patch_page(page, url, now))
    return items


def parse_news_list(page: str) -> list[dict]:
    cards = []
    for m in RE_CARD.finditer(page):
        try:
            ts = datetime.fromisoformat(m["ts"].replace("Z", "+00:00")).astimezone(JST)
        except ValueError:
            continue
        cards.append({"title": html.unescape(re.sub(r"<[^>]+>", "", m["title"])).strip(),
                      "url": SITE + "/ja-jp" + m["href"], "posted": ts})
    return cards


def find_season_openings(text: str, posted: datetime) -> list[tuple[int, datetime, str]]:
    """本文から（シーズン番号, 開幕日, 該当の文）を取り出す。掲載日から 7 日前〜120 日後の日付だけを採用する。"""
    t = norm(text)
    out: list[tuple[int, datetime, str]] = []
    for regex in (RE_OPEN_1, RE_OPEN_2):
        for m in regex.finditer(t):
            hits = find_datetimes(m["date"], posted)
            if not hits:
                continue
            day = hits[0].start
            if not (posted - timedelta(days=7) <= day <= posted + timedelta(days=120)):
                continue
            out.append((int(m["n"]), day, _sentence(t, m.start(), m.end())))
    return out


def _sentence(t: str, start: int, end: int, limit: int = 80) -> str:
    """一致した部分を含む 1 文を取り出す（判定根拠として画面に表示するため）。"""
    marks = "。!?\n"
    s = max(t.rfind(ch, 0, start) for ch in marks) + 1
    ends = [i for i in (t.find(ch, end) for ch in marks) if i != -1]
    e = min(ends) + 1 if ends else len(t)
    sentence = t[s:e].strip()
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def fetch_season_items(now: datetime, get=request_text) -> list[Item]:
    page = get(NEWS_URL)
    cards = parse_news_list(page)
    if not cards:
        raise FetchError("オーバーウォッチ公式ニュースの一覧を読み取れませんでした（ページの構成が変わった可能性）")
    seasons: dict[int, Item] = {}
    for c in sorted(cards, key=lambda x: x["posted"]):
        if c["posted"] < now - timedelta(days=NEWS_DAYS):
            continue
        article = get(c["url"])
        m = RE_ARTICLE.search(article)
        text = html_to_text(m.group(1)) if m else ""
        for n, day, sentence in find_season_openings(text, c["posted"]):
            it = Item(id=f"ow-season{n}", game="", kind=KIND_MAJOR, title=f"シーズン{n}開幕", start=day,
                      all_day=True, dl=DL_LIKELY, posted=c["posted"], source_key="blizzard_ow")
            it.add_basis(f"Blizzard 公式ニュース「{c['title']}」に「{sentence}」と記載")
            it.add_basis("新シーズンの開幕（大型アップデート）")
            it.add_basis(NOTE_PENDING)
            it.status = ST_SCHEDULED if day.date() > now.date() else ST_ANNOUNCED
            it.add_source(f"Blizzard 公式ニュース「{c['title']}」", c["url"])
            # 同じシーズンの告知が複数あれば、新しい記事の日付を採用する（日程変更に追従するため）
            seasons[n] = it
    return list(seasons.values())


def build_items(game_id: str, now: datetime, since: datetime) -> list[Item]:
    items = fetch_patch_notes(since, now) + fetch_season_items(now)
    for it in items:
        it.game = game_id
    return [i for i in items if i.start >= since]
