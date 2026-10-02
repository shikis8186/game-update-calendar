"""HoYoverse（原神・崩壊：スターレイル・ゼンレスゾーンゼロ）の情報を取得する。

使う情報源:
  - HoYoPlay（公式ランチャー）の配信データ … 現在のバージョン、事前ダウンロードの有無、実際のダウンロードサイズ
  - HoYoLAB の公式お知らせ（日本語）      … バージョンアップのメンテナンス日時、事前ダウンロード開始日時
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..common import FetchError, fmt_dt, fmt_size, from_ts, parse_iso, request_json, to_iso
from ..model import (DL_YES, KIND_MAJOR, KIND_PRE, ST_ANNOUNCED, ST_CONFIRMED, ST_SCHEDULED, Item)
from ..textparse import excerpt, find_datetimes, html_to_text, norm

LAUNCHER_ID = "VYTpXlbWo8"  # HoYoPlay（グローバル版）
BRANCHES_URL = "https://sg-hyp-api.hoyoverse.com/hyp/hyp-connect/api/getGameBranches"
PATCH_BUILD_URL = "https://sg-downloader-api.hoyoverse.com/downloader/sophon_chunk/api/getPatchBuild"
NEWS_URL = "https://bbs-api-os.hoyolab.com/community/post/wapi/getNewsList"
POST_URL = "https://bbs-api-os.hoyolab.com/community/post/wapi/getPostFull"
HL_HEADERS = {"x-rpc-language": "ja-jp", "Referer": "https://www.hoyolab.com/", "Origin": "https://www.hoyolab.com"}

RE_TAG = re.compile(r"^\d+\.\d+\.\d+$")
RE_VER = re.compile(r"Ver\.?\s?(\d+\.\d+)", re.I)
RE_PRE_NOTICE = re.compile(r"バージョンアップ.{0,6}お知らせ|アップデートメンテナンス|メンテナンス予告|アップデート予告")
RE_RELEASE_NOTICE = re.compile(r"「([^」]+)」.{0,4}(?:正式リリース|アップデートについて|アップデート詳細)")
RE_SKIP_NOTICE = re.compile(r"クラウド版|不具合|補償|延長|イベント")
RE_DURATION = re.compile(r"(?:およそ|約)\s*(\d+(?:\.\d+)?)\s*時間")
RE_PRE_NOW = re.compile(r"(?:ただいま|現在).{0,25}事前ダウンロード.{0,15}(?:可能|開放|開始)")
RE_PC_SIZE = re.compile(r"PC[^\n。]{0,30}?(?:約)?\s*(\d+(?:\.\d+)?)\s*GB")


def vkey(tag: str) -> tuple[int, int]:
    parts = tag.split(".")
    return int(parts[0]), int(parts[1])


def vstr(tag: str) -> str:
    major, minor = vkey(tag)
    return f"{major}.{minor}"


# ------------------------------------------------------------------ ランチャー

def fetch_branches() -> dict[str, dict]:
    d = request_json(BRANCHES_URL, params={"launcher_id": LAUNCHER_ID})
    if d.get("retcode") != 0:
        raise FetchError(f"HoYoPlay の応答エラー: {d.get('message')}")
    out: dict[str, dict] = {}
    for gb in (d.get("data") or {}).get("game_branches") or []:
        gid = (gb.get("game") or {}).get("id")
        main = gb.get("main") or {}
        if not gid or not RE_TAG.match(str(main.get("tag", ""))):
            continue
        pre = gb.get("pre_download") or None
        if pre and not RE_TAG.match(str(pre.get("tag", ""))):
            pre = None
        out[gid] = {"main": main, "pre_download": pre}
    if not out:
        raise FetchError("HoYoPlay の配信データにゲームが含まれていません")
    return out


def patch_sizes(branch: dict, from_tag: str) -> dict[str, int]:
    """branch（main または pre_download）へ from_tag から更新するときのダウンロードサイズ。"""
    params = {k: branch[k] for k in ("branch", "package_id", "password", "tag") if k in branch}
    d = request_json(PATCH_BUILD_URL, method="POST", params=params, body="")
    if d.get("retcode") != 0:
        raise FetchError(f"HoYoverse のサイズ情報の取得に失敗: {d.get('message')}")
    sizes: dict[str, int] = {}
    for mf in (d.get("data") or {}).get("manifests") or []:
        stat = ((mf.get("stats") or {}).get(from_tag)) or {}
        if "compressed_size" in stat:
            sizes[mf.get("matching_field", "")] = int(stat["compressed_size"])
    return sizes


def describe_size(sizes: dict[str, int], from_tag: str) -> str | None:
    if "game" not in sizes:
        return None
    total = sizes["game"] + sizes.get("ja-jp", 0)
    voice = "＋日本語音声" if "ja-jp" in sizes else ""
    return f"{fmt_size(total)}（PC・Ver.{vstr(from_tag)}から更新・本体{voice}）"


# ------------------------------------------------------------------ お知らせ

def fetch_notices(hoyolab_gid: int) -> list[dict]:
    d = request_json(NEWS_URL, params={"gids": hoyolab_gid, "page_size": 40, "type": 1}, headers=HL_HEADERS)
    if d.get("retcode") != 0:
        raise FetchError(f"HoYoLAB の応答エラー: {d.get('message')}")
    posts = []
    for it in (d.get("data") or {}).get("list") or []:
        p = it.get("post") or {}
        if p.get("post_id") and p.get("subject") and p.get("created_at"):
            posts.append({"id": str(p["post_id"]), "subject": p["subject"], "posted": from_ts(p["created_at"])})
    if not posts:
        raise FetchError("HoYoLAB のお知らせが空です")
    return posts


def fetch_post_text(post_id: str) -> str:
    d = request_json(POST_URL, params={"post_id": post_id, "read": 1, "scene": 1}, headers=HL_HEADERS)
    if d.get("retcode") != 0:
        raise FetchError(f"HoYoLAB の記事取得エラー: {d.get('message')}")
    post = ((d.get("data") or {}).get("post") or {}).get("post") or {}
    return norm(html_to_text(post.get("content") or ""))


def post_url(post_id: str) -> str:
    return f"https://www.hoyolab.com/article/{post_id}"


@dataclass
class PreNotice:
    post: dict
    maint_start: datetime | None = None
    maint_raw: str | None = None
    duration_h: float | None = None
    pre_start: datetime | None = None
    pre_raw: str | None = None
    pre_now: bool = False
    pc_size_gb: str | None = None
    text: str = ""


def parse_pre_notice(post: dict, text: str) -> PreNotice:
    """バージョンアップ予告の本文から、メンテナンス開始・事前DL開始・PC容量を読み取る。"""
    pn = PreNotice(post=post, text=text)
    # 原神は UTC+8、スターレイル等は JST で書かれている。書かれていない場合は JST とみなす
    for h in find_datetimes(text, post["posted"]):
        if h.until or not h.has_time:
            continue
        before = text[max(0, h.pos - 30): h.pos]
        after = text[h.endpos: h.endpos + 30]
        if "事前ダウンロード" in before + after and pn.pre_start is None:
            pn.pre_start, pn.pre_raw = h.start, h.raw.strip()
        elif (("メンテナンス" in after or "バージョンアップ" in after or "メンテナンス" in before)
              and "公開" not in after and "補償" not in before + after and pn.maint_start is None):
            pn.maint_start, pn.maint_raw = h.start, h.raw.strip()
    m = RE_DURATION.search(text)
    if m:
        pn.duration_h = float(m.group(1))
    pn.pre_now = bool(RE_PRE_NOW.search(text))
    m = RE_PC_SIZE.search(text)
    if m:
        pn.pc_size_gb = m.group(1)
    return pn


@dataclass
class VersionInfo:
    ver: str
    name: str | None = None
    pre: PreNotice | None = None
    release_post: dict | None = None
    sources: list[tuple[str, str]] = field(default_factory=list)


def collect_versions(notices: list[dict], fetch_text=fetch_post_text) -> dict[str, VersionInfo]:
    versions: dict[str, VersionInfo] = {}
    for p in sorted(notices, key=lambda x: x["posted"]):
        s = norm(p["subject"])
        m = RE_VER.search(s)
        if not m or RE_SKIP_NOTICE.search(s):
            continue
        v = m.group(1)
        info = versions.setdefault(v, VersionInfo(ver=v))
        rel = RE_RELEASE_NOTICE.search(s)
        if rel:
            info.name = info.name or rel.group(1)
            info.release_post = info.release_post or p
            info.sources.append((f"HoYoLAB「{p['subject']}」", post_url(p["id"])))
        elif RE_PRE_NOTICE.search(s):
            # 日時の変更などで告知が出し直された場合は、新しい告知の内容を使う（古い順に処理している）
            parsed = parse_pre_notice(p, fetch_text(p["id"]))
            if parsed.maint_start or info.pre is None:
                info.pre = parsed
            info.sources.append((f"HoYoLAB「{p['subject']}」", post_url(p["id"])))
    return versions


# ------------------------------------------------------------------ 予定の作成

def build_items(game_id: str, cfg: dict, branch: dict | None, versions: dict[str, VersionInfo],
                state: dict, now: datetime, since: datetime) -> list[Item]:
    """state はこのゲームの検知履歴（ランチャーで見えたバージョン・事前DL）。この関数内で更新する。"""
    supplement_only = bool(cfg.get("supplement_only"))
    key = f"hoyoverse:{cfg['launcher_id']}"
    items: list[Item] = []

    main_tag = pre_tag = main_from = None
    main_sizes: dict[str, int] = {}
    pre_sizes: dict[str, int] = {}
    if branch:
        main = branch["main"]
        main_tag = main["tag"]
        hist = state.setdefault("history", [])
        if not hist or hist[-1]["tag"] != main_tag:
            hist.append({"tag": main_tag, "first_seen": to_iso(now)})
        prev = sorted((t for t in main.get("diff_tags") or [] if RE_TAG.match(t)), key=vkey)
        if prev:
            main_from = prev[-1]
            try:
                main_sizes = patch_sizes(main, main_from)
            except FetchError:
                main_sizes = {}
        pre = branch.get("pre_download")
        if pre:
            pre_tag = pre["tag"]
            seen = state.setdefault("pre_seen", {}).setdefault(pre_tag, {"first_seen": to_iso(now)})
            seen["last_seen"] = to_iso(now)
            try:
                pre_sizes = patch_sizes(pre, main_tag)
            except FetchError:
                pre_sizes = {}

    seen_tags = [h["tag"] for h in state.get("history", [])]

    def released(v: str) -> bool:
        # 今回ランチャーを取得できなかった場合も、過去に検知したバージョンで判定する
        return any(vkey(t) >= vkey(v + ".0") for t in seen_tags)

    known = set(versions)
    for v, info in versions.items():
        pn = info.pre
        start = (pn.maint_start if pn and pn.maint_start else
                 info.release_post["posted"] if info.release_post else None)
        if start is None:
            continue
        if supplement_only and start < now - timedelta(days=2):
            continue  # 配信後の情報は Steam 側（お知らせ・ビルド更新）を使う
        end = start + timedelta(hours=pn.duration_h) if pn and pn.duration_h and pn.maint_start else None
        title = f"Ver.{v}「{info.name}」バージョンアップ" if info.name else f"Ver.{v} バージョンアップ"
        it = Item(id=f"hoyo-{game_id}-v{v}", game=game_id, kind=KIND_MAJOR, title=title, start=start,
                  end=end, dl=DL_YES, source_key=key, posted=(pn.post["posted"] if pn else None))
        if pn and pn.maint_start:
            it.add_basis(f"公式告知: {pn.maint_raw} からバージョンアップメンテナンス（日本時間 {fmt_dt(pn.maint_start)}）")
            it.excerpt = excerpt(pn.text)
        elif info.release_post:
            it.add_basis(f"公式告知: Ver.{v} のリリース告知（{fmt_dt(info.release_post['posted'])} 掲載）")
        it.add_basis("バージョンアップではゲームクライアントの更新（ダウンロード）が必要")
        if released(v):
            it.status = ST_CONFIRMED
            latest = max(seen_tags, key=vkey)
            it.add_basis(f"公式ランチャー（HoYoPlay）の配信バージョン（{latest}）から、配信済みであることを確認")
            if main_tag and vstr(main_tag) == v and main_from:
                it.size = describe_size(main_sizes, main_from)
        else:
            it.status = ST_SCHEDULED if start > now else ST_ANNOUNCED
        if not it.size and pn and pn.pc_size_gb:
            it.size = f"約{pn.pc_size_gb}GB（公式告知の事前ダウンロード容量・PC）"
        for label, url in info.sources:
            it.add_source(label, url)
        if start >= since:
            items.append(it)

        # 事前ダウンロード
        pre_seen = (state.get("pre_seen") or {})
        observed = next((t for t in pre_seen if vstr(t) == v), None)
        if pn and (pn.pre_start or pn.pre_now) or observed:
            if pn and pn.pre_start:
                pstart, why = pn.pre_start, f"公式告知: 事前ダウンロード開始 {pn.pre_raw}"
            elif pn and pn.pre_now:
                pstart, why = pn.post["posted"], f"公式告知（{fmt_dt(pn.post['posted'])} 掲載）に「事前ダウンロードが可能」と記載"
            else:
                pstart = parse_iso(pre_seen[observed]["first_seen"])
                why = "公式ランチャーで事前ダウンロードの開始を検知（開始日時は検知した時刻）"
            pend = pn.maint_start if pn and pn.maint_start else None
            pre_item = Item(id=f"hoyo-{game_id}-pre{v}", game=game_id, kind=KIND_PRE,
                            title=f"Ver.{v} 事前ダウンロード", start=pstart, end=pend, dl=DL_YES,
                            source_key=key)
            pre_item.add_basis(why)
            if observed:
                pre_item.status = ST_CONFIRMED
                pre_item.add_basis(f"公式ランチャー（HoYoPlay）で事前ダウンロード（{observed}）の提供を確認")
            else:
                pre_item.status = ST_SCHEDULED if pstart > now else ST_ANNOUNCED
            if pre_tag and vstr(pre_tag) == v and main_tag:
                pre_item.size = describe_size(pre_sizes, main_tag)
            elif pn and pn.pc_size_gb:
                pre_item.size = f"約{pn.pc_size_gb}GB（公式告知・PC）"
            for label, url in info.sources:
                pre_item.add_source(label, url)
            if (pend or pstart) >= since:
                items.append(pre_item)

    # お知らせが見つからないままバージョンが変わった場合も、ランチャーの実データから記録する
    if main_tag and not supplement_only:
        v = vstr(main_tag)
        first = next((h for h in state.get("history", []) if vstr(h["tag"]) == v), None)
        if v not in known and first and len(state.get("history", [])) > 1:
            detected = parse_iso(first["first_seen"])
            it = Item(id=f"hoyo-{game_id}-v{v}", game=game_id, kind=KIND_MAJOR, title=f"Ver.{v} 配信",
                      start=detected, dl=DL_YES, status=ST_CONFIRMED, source_key=key)
            it.add_basis(f"公式ランチャー（HoYoPlay）の配信バージョンが {main_tag} に変わったことを検知（日時は検知した時刻）")
            if main_from:
                it.size = describe_size(main_sizes, main_from)
            if detected >= since:
                items.append(it)
    if pre_tag and vstr(pre_tag) not in known and not any(i.kind == KIND_PRE for i in items):
        first_seen = parse_iso(state["pre_seen"][pre_tag]["first_seen"])
        it = Item(id=f"hoyo-{game_id}-pre{vstr(pre_tag)}", game=game_id, kind=KIND_PRE,
                  title=f"Ver.{vstr(pre_tag)} 事前ダウンロード", start=first_seen, dl=DL_YES,
                  status=ST_CONFIRMED, source_key=key)
        it.add_basis("公式ランチャーで事前ダウンロードの開始を検知（開始日時は検知した時刻）")
        if main_tag:
            it.size = describe_size(pre_sizes, main_tag)
        items.append(it)
    return items
