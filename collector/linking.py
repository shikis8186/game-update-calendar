"""告知どうし・告知と実データ（Steam のビルド更新）を突き合わせて、予定を確定させる。

考え方:
  - ビルドが更新された時刻 = ゲーム本体のダウンロードが発生した時刻（確定）。
  - その前後 24 時間以内（日付だけの予定は、その日の前後 12 時間以内）にあるアップデート系の告知は、
    そのビルド更新のこととして 1 件にまとめる。
  - ビルドを監視していた期間内なのに前後 24 時間にビルド更新がない告知は「ダウンロードなし」と確定する。
  - 監視を始める前の告知は確定できないので、元の判定（見込み・不明）のまま残す。
  - 実データで確定できないゲームでも、24 時間以内の同じアップデートに関する告知（メンテナンス告知と
    パッチノートなど）は 1 件にまとめる。
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from .common import JST, fmt_dt, from_ts
from .model import (DL_LIKELY, DL_NO, DL_UNKNOWN, DL_YES, KIND_MAINT, KIND_NEWS, KIND_PATCH,
                    KIND_PRIORITY, ST_ANNOUNCED, ST_CONFIRMED, ST_SCHEDULED, UPDATE_KINDS, Item)

WINDOW = timedelta(hours=24)
CLUSTER_GAP = timedelta(hours=6)
ALLDAY_MARGIN = timedelta(hours=12)
MAINT_MARGIN = timedelta(hours=2)
DL_RANK = {DL_YES: 3, DL_LIKELY: 2, DL_UNKNOWN: 1, DL_NO: 0}
ST_RANK = {ST_CONFIRMED: 2, ST_SCHEDULED: 1, ST_ANNOUNCED: 0}


def build_items(game_id: str, appid: int, history: list[dict]) -> list[Item]:
    """記録したビルド更新を、日本時間の日付ごとに 1 件の「クライアント更新」にまとめる。"""
    by_day: dict = defaultdict(list)
    for b in history:
        t = from_ts(b["timeupdated"])
        by_day[t.astimezone(JST).date()].append((t, b["buildid"]))
    items = []
    for _, builds in sorted(by_day.items()):
        builds.sort()
        it = Item(id=f"steambuild-{appid}-{builds[-1][1]}", game=game_id, kind=KIND_PATCH,
                  title="クライアント更新", start=builds[0][0], dl=DL_YES, status=ST_CONFIRMED,
                  source_key="steambuild")
        for t, bid in builds:
            it.add_basis(f"Steam の公開ビルドが更新されました（ビルドID {bid}、{fmt_dt(t)}）")
        it.add_source("Steam ストアページ", f"https://store.steampowered.com/app/{appid}/")
        items.append(it)
    return items


RE_PATCH_WORD = re.compile(r"パッチ|patch|アップデート|update|バージョンアップ|ver\.?\s?\d", re.I)


RE_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
RE_HEADLINE = re.compile(r"シーズン\s?\d+.{0,20}開幕")


def _priority(item: Item, ref: datetime) -> tuple:
    japanese = 0 if RE_JAPANESE.search(item.title) else 1  # 日本語の公式告知のタイトルを優先して表示する
    headline = 0 if RE_HEADLINE.search(item.title) else 1  # 「シーズン5開幕」のような内容が分かるタイトルを優先する
    explicit_update = 0 if item.steam_event_type in (12, 13, 14) else 1
    patch_word = 0 if RE_PATCH_WORD.search(item.title) else 1
    return (KIND_PRIORITY[item.kind], japanese, headline, explicit_update, patch_word, abs(item.start - ref))


def _day_bounds(item: Item) -> tuple[datetime, datetime]:
    day = item.start.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day + timedelta(days=1)


def absorb(primary: Item, other: Item) -> None:
    """other を primary にまとめる（other は画面に出さない）。"""
    other.merged = True
    primary.add_basis(f"関連する告知:「{other.title}」")
    for line in other.basis[1:]:
        primary.add_basis(line)
    for s in other.sources:
        primary.add_source(s["label"], s["url"])
    if DL_RANK[other.dl] > DL_RANK[primary.dl]:
        primary.dl = other.dl
    if ST_RANK[other.status] > ST_RANK[primary.status]:
        primary.status = other.status
    primary.size = primary.size or other.size
    primary.excerpt = primary.excerpt or other.excerpt
    # メンテナンス告知の時間帯の前後 2 時間以内にある告知なら、その時間帯をアップデートの時間帯として使う
    if (other.kind == KIND_MAINT and other.end and not primary.end and not primary.all_day
            and other.start - MAINT_MARGIN <= primary.start <= other.end + MAINT_MARGIN):
        primary.start, primary.end = other.start, other.end


def _drop_pending_note(item: Item) -> None:
    """確定したら「配信後に確定します」という注記は不要になるので消す。"""
    item.basis = [line for line in item.basis if "配信後に Steam のビルド更新で確定" not in line]


def _distance(item: Item, ref: Item) -> timedelta | None:
    if item.end and item.start <= ref.start <= item.end:
        return timedelta(0)
    if item.all_day:
        # 日付だけの予定（例: シーズン開幕日）は、開始前夜や翌朝の更新もあり得るため前後 12 時間まで同じ更新とみなす
        lo, hi = _day_bounds(item)
        if lo <= ref.start < hi:
            return timedelta(0)
        if lo - ALLDAY_MARGIN <= ref.start <= hi + ALLDAY_MARGIN:
            return min(abs(ref.start - lo), abs(ref.start - hi))
        return None
    d = abs(item.start - ref.start)
    return d if d <= WINDOW else None


def _judge_after(item: Item) -> datetime:
    """この時刻まで監視してビルド更新がなければ「ダウンロードなし」と判断できる。"""
    if item.all_day:
        return _day_bounds(item)[1] + ALLDAY_MARGIN
    return item.start + WINDOW


def link(items: list[Item], builds: list[Item], coverage_from: datetime | None,
         checked_at: datetime | None, judge_no_download: bool = True) -> list[Item]:
    """items（同じゲームの告知）と builds（同じゲームのビルド更新）を突き合わせる。
    どの告知とも結び付かなかったビルド更新を返す（そのまま「クライアント更新」として載せる）。
    judge_no_download=False のゲーム（追加データをゲーム内でダウンロードするもの）は、
    ビルド更新がないことを「ダウンロードなし」の根拠にしない。"""
    candidates = [i for i in items if i.kind in UPDATE_KINDS and not i.merged]
    unmatched: list[Item] = []
    for b in builds:
        near = [i for i in candidates if not i.merged and _distance(i, b) is not None]
        if not near:
            unmatched.append(b)
            continue
        near.sort(key=lambda i: _priority(i, b.start))
        primary = near[0]
        for other in near[1:]:
            absorb(primary, other)
        primary.dl, primary.status = DL_YES, ST_CONFIRMED
        _drop_pending_note(primary)
        for line in b.basis:
            primary.add_basis(line)
        for s in b.sources:
            primary.add_source(s["label"], s["url"])
        # カレンダーの日時は、実際にダウンロードが発生した時刻（ビルド更新）にする
        in_window = primary.end and primary.start <= b.start <= primary.end
        if not in_window:
            if primary.posted and primary.posted.astimezone(JST).date() != b.start.astimezone(JST).date():
                primary.add_basis(f"告知の掲載は {fmt_dt(primary.posted)}")
            primary.start, primary.end, primary.all_day = b.start, None, False
        b.merged = True

    # 監視期間内で、前後にビルド更新がなかった告知は「ダウンロードなし」と確定する
    if judge_no_download and coverage_from and checked_at:
        for i in candidates:
            if i.merged or i.status == ST_CONFIRMED or i.dl == DL_YES:
                continue
            if coverage_from <= i.start and _judge_after(i) <= checked_at:
                i.dl = DL_NO
                _drop_pending_note(i)
                span = "その日の前後12時間" if i.all_day else "前後24時間"
                i.add_basis(f"{span}に Steam のビルド更新がないため、ゲーム本体のダウンロードは発生していません")
                if i.kind != KIND_MAINT:
                    i.kind = KIND_NEWS  # ダウンロードを伴わない告知はアップデート扱いにしない
    return unmatched


def _same_update(a: Item, b: Item) -> bool:
    same_day = a.start.astimezone(JST).date() == b.start.astimezone(JST).date()
    return same_day or abs(a.start - b.start) <= CLUSTER_GAP


def cluster(items: list[Item]) -> None:
    """同じゲームで同じ日（または 6 時間以内）に出た、同じアップデートに関する告知を 1 件にまとめる。"""
    cands = sorted((i for i in items if i.kind in UPDATE_KINDS and not i.merged), key=lambda i: i.start)
    group: list[Item] = []

    def flush() -> None:
        if len(group) > 1:
            ref = group[0]
            group.sort(key=lambda i: _priority(i, ref.start))
            for other in group[1:]:
                absorb(group[0], other)
        group.clear()

    for it in cands:
        if group and (it.game != group[0].game or not _same_update(group[0], it)
                      or it.status != group[0].status and ST_SCHEDULED in (it.status, group[0].status)):
            flush()
        group.append(it)
    flush()
