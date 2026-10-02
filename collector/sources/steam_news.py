"""Steam のお知らせ（イベント・ニュース）を取得して予定に変換する。

Steam のお知らせには運営が付けた「種類」（パッチノート・大型アップデート・ニュースなど）があるが、
付け方は運営会社によってばらばら（パッチノートを「ニュース」で出す運営も多い）。
そのため種類とタイトルの語句の両方で分類し、ダウンロードの有無は最終的に
ビルド更新の実データ（steam_builds）と突き合わせて確定させる。
"""
from __future__ import annotations

import re
from datetime import datetime

from ..common import FetchError, from_ts, request_json
from ..model import (DL_LIKELY, DL_UNKNOWN, KIND_EVENT, KIND_MAINT, KIND_MAJOR, KIND_NEWS,
                     KIND_PATCH, ST_ANNOUNCED, ST_SCHEDULED, UPDATE_KINDS, Item)
from ..textparse import bbcode_to_text, excerpt, find_datetimes, norm
from .noise import is_noise

EVENTS_URL = "https://store.steampowered.com/events/ajaxgetpartnereventspageable/"

TYPE_LABEL = {
    10: "ゲームリリース", 11: "配信", 12: "パッチノート", 13: "アップデート", 14: "大型アップデート",
    15: "DLCリリース", 16: "リリース予定", 17: "大会配信", 18: "開発者配信", 19: "配信",
    20: "セール", 21: "アイテムセール", 22: "ゲーム内ボーナス", 23: "ゲーム内ドロップ", 24: "ゲーム内特典",
    25: "ゲーム内チャレンジ", 26: "ゲーム内コンテスト", 28: "ニュース", 29: "ベータ版", 30: "コンテンツ追加",
    31: "無料体験", 32: "シーズン開始", 33: "シーズン更新", 34: "クロスプロモーション", 35: "ゲーム内イベント",
}
IN_GAME_TYPES = {22, 23, 24, 25, 26, 30, 32, 33, 35}
STREAM_TYPES = {11, 17, 18, 19}
NEWS_TYPES = {10, 15, 16, 20, 21, 29, 31, 34}

# 誤って「アップデート」と判定するより「お知らせ」と判定するほうが安全
# （ダウンロードの有無はビルド更新の実データで別に検知されるため、取りこぼしにはならない）
RE_NOT_UPDATE = re.compile(
    r"不正|処分|制裁|違反|BAN\b|チート|cheat|セール|sale|販売|グッズ|コラボカフェ|募集|アンケート|"
    r"ポイントショップ|紐づけ|紐付け|対処方法|不具合|既知の問題|\bissues?\b|トレーラー|trailer|"
    r"ガイド|guide|レポート|当選|選定結果|everything new|まとめ|紹介|preview",
    re.I,
)
RE_EVENT_STRONG = re.compile(r"バトルパス|battle\s?pass|キャンペーン|campaign|コンテスト|contest", re.I)
RE_LEADING_TAGS = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")
RE_MAINT = re.compile(r"メンテナンス|maintenance", re.I)
RE_MAJOR = re.compile(
    r"大型アップデート|major update|シーズン\s?\d+|season\s?\d+|ver(?:sion)?\.?\s?\d+\.\d+|"
    r"新キャラクター|追加キャラクター|参戦|カードパック|拡張パック|新章|新シーズン",
    re.I,
)
RE_PATCH = re.compile(
    r"パッチノート|patch\s?notes?|パッチ|patch|アップデート|update|hotfix|ホットフィックス|"
    r"クライアント更新|バージョンアップ",
    re.I,
)
RE_EVENT = re.compile(
    r"イベント|開催|キャンペーン|フェス|大会|トーナメント|バトルパス|event|tournament|campaign|"
    r"festival|battle\s?pass",
    re.I,
)


def classify(title: str, event_type: int) -> str:
    # 先頭の [Updated] [Completed] [10/01] などの付記は分類に使わない
    t = RE_LEADING_TAGS.sub("", norm(title))
    if event_type == 14:
        return KIND_MAJOR
    if event_type in (12, 13):
        return KIND_PATCH
    if event_type in IN_GAME_TYPES or event_type in STREAM_TYPES:
        return KIND_EVENT
    if event_type in NEWS_TYPES:
        return KIND_NEWS
    if RE_NOT_UPDATE.search(t):
        return KIND_NEWS
    if RE_MAINT.search(t):
        return KIND_MAINT
    if RE_EVENT_STRONG.search(t):
        return KIND_EVENT
    if RE_MAJOR.search(t):
        return KIND_MAJOR
    if RE_PATCH.search(t):
        return KIND_PATCH
    if RE_EVENT.search(t):
        return KIND_EVENT
    return KIND_NEWS


def short(title: str, limit: int = 40) -> str:
    return title if len(title) <= limit else title[: limit - 1] + "…"


def fetch_events(appid: int, since: datetime) -> list[dict]:
    events: list[dict] = []
    since_ts = int(since.timestamp())
    for page in range(4):
        d = request_json(
            EVENTS_URL,
            params={"clan_accountid": 0, "appid": appid, "offset": page * 100, "count": 100, "l": "japanese"},
        )
        if d.get("success") != 1 or not isinstance(d.get("events"), list):
            raise FetchError(f"Steam のお知らせ（アプリ {appid}）の応答が想定外です")
        batch = d["events"]
        events.extend(batch)
        if len(batch) < 100 or min(int(e.get("rtime32_start_time") or 0) for e in batch) < since_ts:
            break
    return events


def to_items(game_id: str, appid: int, events: list[dict], now: datetime, since: datetime) -> list[Item]:
    items: list[Item] = []
    title_dated: set[str] = set()
    for e in events:
        ab = e.get("announcement_body") or {}
        if ab.get("hidden"):
            continue
        gid = str(e.get("gid") or "")
        title = (ab.get("headline") or e.get("event_name") or "").strip()
        if not gid or not title or is_noise(title):
            continue
        etype = int(e.get("event_type") or 0)
        posted = from_ts(ab.get("posttime") or e.get("rtime32_start_time"))
        kind = classify(title, etype)
        url = f"https://store.steampowered.com/news/app/{appid}/view/{gid}"
        label = TYPE_LABEL.get(etype, "お知らせ")

        start, end, all_day, from_title = posted, None, False, False
        basis = [f"Steamのお知らせ（種類: {label}）"]
        if etype in (12, 13, 14):
            # アップデート系の種類では、運営が設定した開始日時が配信日時を表す（掲載より後のことがある）
            st = int(e.get("rtime32_start_time") or 0)
            if st and from_ts(st) > posted:
                start = from_ts(st)
        elif etype in IN_GAME_TYPES or etype in STREAM_TYPES:
            st = int(e.get("rtime32_start_time") or 0)
            en = int(e.get("rtime32_end_time") or 0)
            if st:
                start = from_ts(st)
            if en and en > st:
                end = from_ts(en)
        elif kind in UPDATE_KINDS or kind == KIND_EVENT:
            # タイトルに日付が書かれていれば、その日付を予定日として使う（「〇日まで」は期限なので使わない）
            hits = [h for h in find_datetimes(norm(title), posted) if not h.until]
            if hits:
                h = hits[0]
                if h.start.date() > posted.date() or h.has_time:
                    start, end = h.start, h.end
                    all_day = not h.has_time
                    from_title = True
                    basis.append(f"告知タイトルに記載の日付「{h.raw.strip()}」")
        if start < since and (end is None or end < since):
            continue

        dl = DL_LIKELY if kind in (KIND_MAJOR, KIND_PATCH) else DL_UNKNOWN
        status = ST_SCHEDULED if start > now else ST_ANNOUNCED
        body = bbcode_to_text(ab.get("body") or "")
        item = Item(
            id=f"steam-{appid}-{gid}",
            game=game_id,
            kind=kind,
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            dl=dl,
            status=status,
            basis=basis,
            excerpt=excerpt(body) if body else None,
            source_key=f"steam:{appid}",
            posted=posted,
            steam_event_type=etype,
        )
        item.add_source(f"Steamのお知らせ「{short(title)}」", url)
        items.append(item)
        if from_title:
            title_dated.add(item.id)
    return dedupe_title_dated(items, title_dated)


def dedupe_title_dated(items: list[Item], title_dated: set[str]) -> list[Item]:
    """タイトルの日付から作った予定のうち、同じ日・同じ種類のもの（例: 参戦日の告知が2本）は1件にまとめる。"""
    out: list[Item] = []
    seen: dict[tuple, Item] = {}
    for it in sorted(items, key=lambda x: x.posted or x.start):
        key = (it.game, it.kind, it.start.date()) if it.id in title_dated else None
        if key and key in seen:
            first = seen[key]
            for s in it.sources:
                first.add_source(s["label"], s["url"])
            continue
        if key:
            seen[key] = it
        out.append(it)
    return out
