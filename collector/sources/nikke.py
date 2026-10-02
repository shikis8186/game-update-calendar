"""勝利の女神：NIKKE の情報を取得する（公式サイト nikke-jp.com の「お知らせ」）。

公式サイトが表示に使っているお知らせ配信 API を、サイトと同じ条件で読み出す。
アップデートは「M月D日のアップデートについて」という告知が数日前に掲載され、
メンテナンス終了後に本文が「メンテナンスが終了し…」と書き換えられる。
"""
from __future__ import annotations

import re
from datetime import datetime

from ..common import FetchError, from_ts, request_json
from ..model import (DL_LIKELY, DL_UNKNOWN, DL_YES, KIND_MAINT, KIND_NEWS, KIND_PATCH, ST_ANNOUNCED,
                     ST_CONFIRMED, ST_SCHEDULED, Item)
from ..textparse import excerpt, find_datetimes, html_to_text, norm
from .noise import is_noise

CMS = "https://na-community.playerinfinite.com/api/gpts.information_feeds_svr.InformationFeedsSvr/"
HEADERS = {
    "X-GameId": "16", "X-AreaId": "na", "X-Source": "pc_web", "X-Language": "ja",
    "Content-Type": "application/json;charset=utf-8",
    "Origin": "https://nikke-jp.com", "Referer": "https://nikke-jp.com/",
}
NEWS_LABEL, NOTICE_LABEL = 309, 892  # 「ニュース」欄の「お知らせ」

RE_UPDATE = re.compile(r"^(\d{1,2})月(\d{1,2})日\s*(?:\([^)]*\))?\s*の?\s*(臨時メンテナンス|メンテナンス|アップデート)について")
RE_IMPROVE = re.compile(r"^(\d{1,2})月(\d{1,2})日の?アップデートによる改善事項")
RE_CLIENT = re.compile(r"クライアント.{0,20}(?:アップデート|更新)|最新バージョンをダウンロード|最新のクライアント")
RE_DONE = re.compile(r"メンテナンスが終了|サーバー(?:を)?再開(?:され|いたし|し)")


def detail_url(content_id: str) -> str:
    return f"https://nikke-jp.com/newsdetail.html?content_id={content_id}"


def fetch_notices(since: datetime, max_pages: int = 8) -> list[dict]:
    """1 回に取れるのは最大 20 件なので、掲載日が since より古くなるまでページを進める。"""
    out: list[dict] = []
    offset = 0
    for _ in range(max_pages):
        d = request_json(CMS + "GetContentByLabel", method="POST", headers=HEADERS, body={
            "gameid": "16", "language": ["ja"], "offset": offset, "get_num": 20,
            "primary_label_id": NEWS_LABEL, "secondary_label_id": NOTICE_LABEL,
        })
        if d.get("code") != 0:
            raise FetchError(f"NIKKE 公式サイトの応答エラー: {d.get('msg')}")
        data = d.get("data") or {}
        batch = data.get("info_content") or []
        for it in batch:
            if it.get("content_id") and it.get("title") and it.get("pub_timestamp"):
                out.append({"id": it["content_id"], "title": it["title"], "posted": from_ts(it["pub_timestamp"])})
        if not batch or data.get("is_finish") or min(from_ts(i["pub_timestamp"]) for i in batch) < since:
            break
        offset = int(data.get("next_offset") or offset + len(batch))
    if not out:
        raise FetchError("NIKKE 公式サイトのお知らせが空です")
    return out


def fetch_text(content_id: str) -> str:
    d = request_json(CMS + "GetContentInfoById", method="POST", headers=HEADERS,
                     body={"content_id": content_id, "language": "ja"})
    if d.get("code") != 0:
        raise FetchError(f"NIKKE のお知らせ本文の取得エラー: {d.get('msg')}")
    data = d.get("data") or {}
    return norm(html_to_text(data.get("content") or data.get("content_part") or ""))


def build_items(game_id: str, notices: list[dict], now: datetime, since: datetime,
                get_text=fetch_text) -> list[Item]:
    items: list[Item] = []
    by_date: dict = {}
    for n in sorted(notices, key=lambda x: x["posted"]):
        title = norm(n["title"]).strip()
        if is_noise(title) or n["posted"] < since:
            continue
        m = RE_UPDATE.match(title)
        if m:
            hits = find_datetimes(title, n["posted"])
            if not hits:
                continue
            day = hits[0].start
            text = get_text(n["id"])
            kind = KIND_PATCH if m.group(3) == "アップデート" else KIND_MAINT
            it = Item(id=f"nikke-{n['id']}", game=game_id, kind=kind, title=n["title"].strip(),
                      start=day, all_day=True, source_key="nikke", posted=n["posted"])
            it.add_basis(f"NIKKE 公式サイトのお知らせ（{n['posted']:%Y/%m/%d} 掲載）")
            # 本文に同じ日の時刻（例: 9月17日(水)11:00～15:00）があれば使う
            for h in find_datetimes(text, n["posted"]):
                if h.has_time and not h.until and h.start.date() == day.date():
                    it.start, it.end, it.all_day = h.start, h.end, False
                    it.add_basis(f"本文に記載の日時「{h.raw.strip()}」")
                    break
            if RE_CLIENT.search(text):
                it.dl = DL_YES
                it.add_basis("公式告知に「ゲームクライアントがアップデートされる」旨の記載あり")
            elif kind == KIND_PATCH:
                it.dl = DL_LIKELY
                it.add_basis("アップデートの告知（クライアント更新の明記はなし）")
            else:
                it.dl = DL_UNKNOWN
            if RE_DONE.search(text):
                it.status = ST_CONFIRMED
                it.add_basis("公式告知でメンテナンス終了（実施済み）を確認")
            else:
                it.status = ST_SCHEDULED if it.start > now else ST_ANNOUNCED
            it.excerpt = excerpt(text) if text else None
            it.add_source(f"NIKKE 公式サイト「{n['title'].strip()}」", detail_url(n["id"]))
            by_date[(kind, day.date())] = it
            items.append(it)
            continue
        m = RE_IMPROVE.match(title)
        if m:
            hits = find_datetimes(title, n["posted"])
            target = by_date.get((KIND_PATCH, hits[0].start.date())) if hits else None
            if target:
                target.add_source(f"NIKKE 公式サイト「{n['title'].strip()}」", detail_url(n["id"]))
                continue
        it = Item(id=f"nikke-{n['id']}", game=game_id, kind=KIND_NEWS, title=n["title"].strip(),
                  start=n["posted"], status=ST_ANNOUNCED, source_key="nikke", posted=n["posted"])
        it.add_basis("NIKKE 公式サイトのお知らせ")
        it.add_source(f"NIKKE 公式サイト「{n['title'].strip()}」", detail_url(n["id"]))
        items.append(it)
    return items
