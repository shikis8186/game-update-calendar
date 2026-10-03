"""Shadowverse: Worlds Beyond の公式サイト（日本語）のニュースから、メンテナンスとアップデートを取得する。

Steam のお知らせには、メンテナンスやアップデートの告知がほとんど載らないため、こちらを使う。
公式サイトがニュース一覧の表示に使っている API を、サイトと同じ条件で読み出す。
  - 「アップデート」分類の記事だけを読む（メンテナンス・バージョンアップ・アップデートのお知らせ）
  - メンテナンス   : 本文の「メンテナンス期間2026/9/29 14:00 ~ 17:00」を予定に使う
  - アップデート   : 本文の「2026/10/2 11:30頃にアップデートを行いました」「データのダウンロードが行われます」
  - バージョンアップ: 「Ver.1.9.11の公開について」（アプリ本体の更新）
このゲームは追加データをゲーム内でダウンロードするため、Steam のビルド更新がないことを
「ダウンロードなし」の根拠にはしない（config の downloads_outside_steam）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..common import FetchError, fmt_dt, from_ts, request_json
from ..model import (DL_LIKELY, DL_UNKNOWN, DL_YES, KIND_MAINT, KIND_MAJOR, KIND_PATCH, ST_ANNOUNCED,
                     ST_CONFIRMED, ST_SCHEDULED, Item)
from ..textparse import excerpt, find_datetimes, html_to_text, norm

SITE = "https://shadowverse-wb.com"
LIST_URL = SITE + "/web/Information/index"
DETAIL_URL = SITE + "/web/Information/detail"
HEADERS = {"Referer": SITE + "/ja/news/"}
UPDATE_CATEGORY = 2  # 公式サイトの「アップデート」タブ

RE_DL = re.compile(r"データのダウンロードが行われます|ダウンロードが(?:必要|行われ)|アップデートが必要")
RE_DONE = re.compile(r"メンテナンスを終了いたしました|アップデートを行いました|公開いたしました")
RE_MAINT_PERIOD = re.compile(r"メンテナンス(?:期間|日時|時間)")
RE_UPDATE_AT = re.compile(r"アップデート(?:日時)?|アップデートを(?:行|実施)")


def detail_url(info_id: str) -> str:
    return f"{SITE}/ja/news/detail/?id={info_id}"


def fetch_list(since: datetime, max_pages: int = 6) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        d = request_json(LIST_URL, headers=HEADERS, params={
            "page_num": page, "lang": "ja", "category": UPDATE_CATEGORY,
            "filters": "type_name[contains]アップデート",
        })
        if (d.get("data_headers") or {}).get("result_code") != 1:
            raise FetchError("シャドバ公式サイトのニュース一覧の応答が想定外です")
        data = d.get("data") or {}
        batch = data.get("information_list") or []
        for it in batch:
            if it.get("id") and it.get("title") and it.get("display_start_time"):
                out.append({"id": it["id"], "title": it["title"], "type": it.get("type_name") or "",
                            "posted": from_ts(it["display_start_time"])})
        if not batch or page >= int(data.get("max_page_num") or 1) or min(i["posted"] for i in out) < since:
            break
    if not out:
        raise FetchError("シャドバ公式サイトのニュースが空です")
    return out


def fetch_text(info_id: str) -> str:
    d = request_json(DETAIL_URL, headers=HEADERS, params={"id": info_id, "lang": "ja"})
    if (d.get("data_headers") or {}).get("result_code") != 1:
        raise FetchError("シャドバ公式サイトのニュース本文の応答が想定外です")
    return norm(html_to_text((d.get("data") or {}).get("message") or ""))


def _hit_near(text: str, posted: datetime, label_re: re.Pattern) -> tuple | None:
    """label_re の直後（30 文字以内）に書かれた日時を探す。"""
    for h in find_datetimes(text, posted):
        if h.until or not h.has_time:
            continue
        before = text[max(0, h.pos - 30): h.pos]
        if label_re.search(before):
            return h
    return None


def build_items(game_id: str, notices: list[dict], now: datetime, since: datetime,
                get_text=fetch_text) -> list[Item]:
    items: list[Item] = []
    for n in notices:
        if n["posted"] < since:
            continue
        title = norm(n["title"]).strip()
        kind = None
        if n["type"] == "メンテナンス" or "メンテナンス" in title:
            kind = KIND_MAINT
        elif n["type"] == "バージョンアップ" or re.search(r"Ver\.?\s?\d+(?:\.\d+)+.{0,4}公開", title):
            kind = KIND_PATCH
        elif "アップデート" in title:
            kind = KIND_PATCH
        elif "カードパック" in title and ("発売" in title or n["type"] == "新商品"):
            kind = KIND_MAJOR
        if kind is None:
            continue
        text = get_text(n["id"])
        if kind == KIND_PATCH and n["type"] != "バージョンアップ" and not (RE_DL.search(text) or "アップデートを行" in text):
            continue  # 「〇〇へのアップデートについて」（端末OSの話など）はゲームの更新ではない
        it = Item(id=f"svwb-{n['id']}", game=game_id, kind=kind, title=n["title"].strip(), start=n["posted"],
                  source_key="shadowverse_wb", posted=n["posted"])
        it.add_basis(f"シャドバ公式サイトのお知らせ（{fmt_dt(n['posted'])} 掲載）")

        if kind == KIND_MAINT:
            h = _hit_near(text, n["posted"], RE_MAINT_PERIOD) or next(
                (x for x in find_datetimes(title, n["posted"]) if x.has_time), None)
            if h:
                it.start, it.end = h.start, h.end
                it.add_basis(f"告知に記載のメンテナンス期間「{h.raw.strip()}」")
            upd = _hit_near(text, n["posted"], RE_UPDATE_AT)
            if upd and not h:
                it.start = upd.start
        elif kind == KIND_PATCH and n["type"] != "バージョンアップ":
            upd = _hit_near(text, n["posted"], re.compile(r"ため、\s*$|アップデート日時")) or next(
                (x for x in find_datetimes(text, n["posted"]) if x.has_time and not x.until), None)
            if upd:
                it.start = upd.start
                it.add_basis(f"告知に記載のアップデート日時「{upd.raw.strip()}」")
        elif kind == KIND_MAJOR:
            hit = next((x for x in find_datetimes(title + "\n" + text, n["posted"]) if not x.until), None)
            if hit:
                it.start, it.all_day = hit.start, not hit.has_time

        if RE_DL.search(text):
            it.dl = DL_YES
            it.add_basis("公式告知に「データのダウンロードが行われます」などダウンロードの記載あり")
        elif n["type"] == "バージョンアップ":
            it.dl = DL_YES
            it.add_basis("アプリ本体の新しいバージョンの公開（ストア・Steam での更新）")
        elif kind == KIND_MAJOR:
            it.dl = DL_LIKELY
            it.add_basis("新カードパックの追加（カードデータのダウンロードを伴うのが通例）")
        else:
            it.dl = DL_UNKNOWN

        if RE_DONE.search(text):
            it.status = ST_CONFIRMED
            it.add_basis("公式告知で実施済み（終了・公開）を確認")
        else:
            it.status = ST_SCHEDULED if it.start > now else ST_ANNOUNCED
        it.excerpt = excerpt(text) if text else None
        it.add_source(f"シャドバ公式サイト「{n['title'].strip()}」", detail_url(n["id"]))
        if (it.end or it.start) >= since and it.start <= now + timedelta(days=240):
            items.append(it)
    return items
