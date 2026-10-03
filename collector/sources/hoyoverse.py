"""HoYoverse（原神・崩壊：スターレイル・ゼンレスゾーンゼロ）の情報を取得する。

使う情報源:
  - HoYoPlay（公式ランチャー）の配信データ … 現在のバージョン、事前ダウンロードの有無、実際のダウンロードサイズ
  - HoYoLAB の公式お知らせ（日本語）      … バージョンアップのメンテナンス日時、事前ダウンロード期間、
                                            バージョン以外の追加データ更新
  - HoYoLAB の公式情報投稿（日本語）      … 「Ver.7.1「…」は2026年9月23日にリリースされます」のような早めの予告

バージョンの書き方はゲームや時期で異なる（「Ver.7.1」「Luna Ⅷ」など）ため、両方に対応する。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..common import FetchError, fmt_dt, fmt_size, from_ts, parse_iso, request_json, to_iso
from ..model import DL_YES, KIND_MAJOR, KIND_PATCH, KIND_PRE, ST_ANNOUNCED, ST_CONFIRMED, ST_SCHEDULED, Item
from ..textparse import excerpt, find_datetimes, html_to_text, norm

LAUNCHER_ID = "VYTpXlbWo8"  # HoYoPlay（グローバル版）
BRANCHES_URL = "https://sg-hyp-api.hoyoverse.com/hyp/hyp-connect/api/getGameBranches"
PATCH_BUILD_URL = "https://sg-downloader-api.hoyoverse.com/downloader/sophon_chunk/api/getPatchBuild"
NEWS_URL = "https://bbs-api-os.hoyolab.com/community/post/wapi/getNewsList"
POST_URL = "https://bbs-api-os.hoyolab.com/community/post/wapi/getPostFull"
HL_HEADERS = {"x-rpc-language": "ja-jp", "Referer": "https://www.hoyolab.com/", "Origin": "https://www.hoyolab.com"}

RE_TAG = re.compile(r"^\d+\.\d+\.\d+$")
RE_VER = re.compile(r"Ver\.?\s?(\d+\.\d+)", re.I)
RE_NAMED_VER = re.compile(r"(?<![A-Za-z])([A-Z][a-z]+\s+[IVX]{1,5})(?![A-Za-z])")  # 例: Luna VIII（Ⅷ は NFKC で VIII）
RE_PRE_NOTICE = re.compile(
    r"バージョンアップ.{0,6}お知らせ|アップデートメンテナンス|メンテナンス予告|アップデート予告|"
    r"事前ダウンロード開始.{0,6}アップデートのお知らせ"
)
RE_RELEASE_NOTICE = re.compile(r"」.{0,4}(?:正式リリース|アップデートについて|アップデート詳細)")
RE_SKIP_NOTICE = re.compile(r"クラウド版|不具合|補償|延長|イベント")
RE_DURATION = re.compile(r"(?:およそ|約)\s*(\d+(?:\.\d+)?)\s*時間")
RE_PRE_NOW = re.compile(r"(?:ただいま|現在).{0,25}事前ダウンロード.{0,15}(?:可能|開放|開始)")
RE_PC_SIZE = re.compile(r"PC(?:版|端末)?[^。]{0,40}?(?:約|およそ)?\s*(\d+(?:\.\d+)?)\s*GB")
RE_LABEL_MAINT = re.compile(r"(?:バージョンアップ|メンテナンス)(?:日時|時間|期間)\s*】?\s*[:：・]?\s*$")
RE_AFTER_MAINT = re.compile(r"^\s*(?:より|から|に)?\s*(?:バージョンアップ|.{0,8}メンテナンス)")
RE_RANGE_TAIL = re.compile(r"[~〜\-]\s*$")
# バージョン以外の追加データ更新（例: 「Fate[UBW]コラボアップデートのお知らせ」）
RE_EXTRA_UPDATE = re.compile(r"アップデート(?:のお知らせ|について|実施のお知らせ|詳細)")
RE_EXTRA_SKIP = re.compile(r"ストア|ショップ|勲功|イベント詳細|クラウド版")
RE_DL_TEXT = re.compile(r"ダウンロード|データが更新|データを更新|再起動して最新")
RE_EXTRA_SIZE = re.compile(r"PC(?:版|端末)?(?:では)?\s*約?\s*【?\s*(\d+(?:\.\d+)?)\s*】?\s*(MB|GB)")
RE_EXTRA_DONE = re.compile(r"アップデートを行いました|更新しました|更新が完了")
# 早めの予告（例: 「Ver.7.1「冥府へのレクイエム」は2026年9月23日にリリースされます」）
RE_RELEASE_HINT = re.compile(
    r"(Ver\.?\s?\d+\.\d+|[A-Z][a-z]+\s+[IVX]{1,5})(?:\s*「[^」]{1,40}」)?\s*は\s*"
    r"(\d{4}年\d{1,2}月\d{1,2}日)\s*に?\s*(?:リリース|配信|アップデート)"
)
RE_HINT_POST = re.compile(r"まとめ|予告|リリース|プレビュー")


def vkey(tag: str) -> tuple[int, int]:
    parts = tag.split(".")
    return int(parts[0]), int(parts[1])


def vstr(tag: str) -> str:
    major, minor = vkey(tag)
    return f"{major}.{minor}"


def is_numeric(key: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+", key))


def version_of(text: str) -> tuple[str, str] | None:
    """お知らせのタイトルからバージョンを取り出す。戻り値は（キー, 表示名）。"""
    s = norm(text)
    m = RE_VER.search(s)
    if m:
        return m.group(1), f"Ver.{m.group(1)}"
    m = RE_NAMED_VER.search(s)
    if m:
        name = re.sub(r"\s+", " ", m.group(1))
        return name, name
    return None


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

def fetch_posts(hoyolab_gid: int, post_type: int, since: datetime, max_pages: int = 4) -> list[dict]:
    """post_type: 1 = お知らせ、3 = 情報。since より古くなるまでページを進める。"""
    posts: list[dict] = []
    last_id = ""
    for _ in range(max_pages):
        params = {"gids": hoyolab_gid, "page_size": 50, "type": post_type}
        if last_id:
            params["last_id"] = last_id
        d = request_json(NEWS_URL, params=params, headers=HL_HEADERS)
        if d.get("retcode") != 0:
            raise FetchError(f"HoYoLAB の応答エラー: {d.get('message')}")
        data = d.get("data") or {}
        batch = data.get("list") or []
        for it in batch:
            p = it.get("post") or {}
            if p.get("post_id") and p.get("subject") and p.get("created_at"):
                posts.append({"id": str(p["post_id"]), "subject": p["subject"], "posted": from_ts(p["created_at"])})
        last_id = data.get("last_id") or ""
        if not batch or data.get("is_last") or not last_id or posts[-1]["posted"] < since:
            break
    return posts


def fetch_notices(hoyolab_gid: int, since: datetime) -> list[dict]:
    posts = fetch_posts(hoyolab_gid, 1, since)
    if not posts:
        raise FetchError("HoYoLAB のお知らせが空です")
    return posts


def fetch_post_text(post_id: str) -> str:
    d = request_json(POST_URL, params={"post_id": post_id, "read": 1, "scene": 1}, headers=HL_HEADERS)
    if d.get("retcode") != 0:
        raise FetchError(f"HoYoLAB の記事取得エラー: {d.get('message')}")
    post = ((d.get("data") or {}).get("post") or {}).get("post") or {}
    text = html_to_text(post.get("content") or "")
    if len(text) < 30 and post.get("structured_content"):
        # 本文が構造化形式だけで保存されている投稿がある
        try:
            parts = json.loads(post["structured_content"])
            text = "".join(p.get("insert", "") for p in parts if isinstance(p.get("insert"), str))
        except (ValueError, TypeError, AttributeError):
            pass
    return norm(text)


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
    pre_end: datetime | None = None
    pre_now: bool = False
    pc_size_gb: str | None = None
    text: str = ""


def parse_pre_notice(post: dict, text: str) -> PreNotice:
    """バージョンアップ予告の本文から、メンテナンス開始・事前DL期間・PC容量を読み取る。"""
    pn = PreNotice(post=post, text=text)
    prev_was_pre = False
    # 原神は UTC+8、スターレイル・ゼンゼロは JST で書かれている。書かれていない場合は JST とみなす
    for h in find_datetimes(text, post["posted"]):
        if h.until or not h.has_time:
            continue
        before = text[max(0, h.pos - 40): h.pos]
        after = text[h.endpos: h.endpos + 40]
        if "補償" in before[-20:] + after[:20] or "公開" in after[:20]:
            prev_was_pre = False
            continue
        # 「【事前ダウンロード期間】A ~ B」の B は事前ダウンロードの終了
        if prev_was_pre and pn.pre_end is None and RE_RANGE_TAIL.search(before):
            pn.pre_end = h.start
            prev_was_pre = False
            continue
        if pn.pre_start is None and ("事前ダウンロード" in before[-25:] or "事前ダウンロード" in after[:15]):
            pn.pre_start, pn.pre_raw = h.start, h.raw.strip()
            prev_was_pre = True
            continue
        prev_was_pre = False
        if pn.maint_start is None and (RE_LABEL_MAINT.search(before) or RE_AFTER_MAINT.search(after)):
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
    label: str = ""
    name: str | None = None
    pre: PreNotice | None = None
    release_post: dict | None = None
    hint_day: datetime | None = None
    hint_text: str | None = None
    hint_post: dict | None = None
    sources: list[tuple[str, str]] = field(default_factory=list)


def _subtitle(subject: str, ver: str) -> str | None:
    for name in re.findall(r"「([^」]+)」", norm(subject)):
        if norm(name).strip() != ver:
            return name
    return None


def collect_versions(notices: list[dict], fetch_text=fetch_post_text) -> dict[str, VersionInfo]:
    versions: dict[str, VersionInfo] = {}
    for p in sorted(notices, key=lambda x: x["posted"]):
        s = norm(p["subject"])
        found = version_of(s)
        if not found or RE_SKIP_NOTICE.search(s):
            continue
        v, label = found
        info = versions.setdefault(v, VersionInfo(ver=v, label=label))
        if RE_PRE_NOTICE.search(s):
            # 日時の変更などで告知が出し直された場合は、新しい告知の内容を使う（古い順に処理している）
            parsed = parse_pre_notice(p, fetch_text(p["id"]))
            if parsed.maint_start or info.pre is None:
                info.pre = parsed
            info.name = info.name or _subtitle(s, v)
            info.sources.append((f"HoYoLAB「{p['subject']}」", post_url(p["id"])))
        elif RE_RELEASE_NOTICE.search(s):
            info.name = info.name or _subtitle(s, v)
            info.release_post = info.release_post or p
            info.sources.append((f"HoYoLAB「{p['subject']}」", post_url(p["id"])))
    return versions


def collect_release_hints(versions: dict[str, VersionInfo], info_posts: list[dict], now: datetime,
                          fetch_text=fetch_post_text) -> None:
    """情報投稿（予告番組のまとめなど）に書かれた、早めのリリース日を versions に加える。"""
    for p in sorted(info_posts, key=lambda x: x["posted"]):
        s = norm(p["subject"])
        if p["posted"] < now - timedelta(days=30) or not version_of(s) or not RE_HINT_POST.search(s):
            continue
        text = fetch_text(p["id"])
        for m in RE_RELEASE_HINT.finditer(s + "\n" + text):
            found = version_of(m.group(1))
            if not found:
                continue
            hits = find_datetimes(m.group(2), p["posted"])
            if not hits or not (p["posted"] <= hits[0].start + timedelta(days=1) <= p["posted"] + timedelta(days=60)):
                continue
            v, label = found
            info = versions.setdefault(v, VersionInfo(ver=v, label=label))
            info.hint_day, info.hint_text, info.hint_post = hits[0].start, m.group(0).strip(), p
            info.name = info.name or _subtitle(m.group(0), v)
            info.sources.append((f"HoYoLAB「{p['subject']}」", post_url(p["id"])))
            break


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

    def launcher_released(v: str) -> bool:
        # 今回ランチャーを取得できなかった場合も、過去に検知したバージョンで判定する
        return is_numeric(v) and any(vkey(t) >= vkey(v + ".0") for t in seen_tags)

    known = set(versions)
    for v, info in versions.items():
        pn = info.pre
        label = info.label or f"Ver.{v}"
        all_day = False
        if pn and pn.maint_start:
            start = pn.maint_start
        elif info.release_post:
            start = info.release_post["posted"]
        elif info.hint_day:
            start, all_day = info.hint_day, True
        else:
            start = None
        if start is not None:
            past_supplement = supplement_only and start < now - timedelta(days=2)
            end = start + timedelta(hours=pn.duration_h) if pn and pn.duration_h and pn.maint_start else None
            title = f"{label}「{info.name}」バージョンアップ" if info.name else f"{label} バージョンアップ"
            it = Item(id=f"hoyo-{game_id}-v{v.replace(' ', '')}", game=game_id, kind=KIND_MAJOR, title=title,
                      start=start, end=end, all_day=all_day, dl=DL_YES, source_key=key,
                      posted=(pn.post["posted"] if pn else None))
            if pn and pn.maint_start:
                it.add_basis(f"公式告知: {pn.maint_raw} からバージョンアップメンテナンス（日本時間 {fmt_dt(pn.maint_start)}）")
                it.excerpt = excerpt(pn.text)
            elif info.release_post:
                it.add_basis(f"公式告知: {label} のリリース告知（{fmt_dt(info.release_post['posted'])} 掲載）")
            elif info.hint_day:
                it.add_basis(f"公式投稿「{info.hint_post['subject']}」に「{info.hint_text}」と記載（時刻は後日の告知で確定）")
            it.add_basis("バージョンアップではゲームクライアントの更新（ダウンロード）が必要")
            if launcher_released(v):
                it.status = ST_CONFIRMED
                latest = max(seen_tags, key=vkey)
                it.add_basis(f"公式ランチャー（HoYoPlay）の配信バージョン（{latest}）から、配信済みであることを確認")
                if main_tag and vstr(main_tag) == v and main_from:
                    it.size = describe_size(main_sizes, main_from)
            elif info.release_post and info.release_post["posted"] <= now:
                it.status = ST_CONFIRMED
                it.add_basis("公式告知で正式リリース（配信済み）を確認")
            else:
                it.status = ST_SCHEDULED if start > now else ST_ANNOUNCED
            if not it.size and pn and pn.pc_size_gb:
                it.size = f"約{pn.pc_size_gb}GB（公式告知の事前ダウンロード容量・PC）"
            for src_label, url in info.sources:
                it.add_source(src_label, url)
            # Steam を主に使うゲームでは、配信後のバージョン更新は Steam 側（お知らせ・ビルド更新）で載せる
            if start >= since and not past_supplement:
                items.append(it)

        # 事前ダウンロード
        pre_seen = (state.get("pre_seen") or {})
        observed = next((t for t in pre_seen if is_numeric(v) and vstr(t) == v), None)
        if pn and (pn.pre_start or pn.pre_now) or observed:
            if pn and pn.pre_start:
                pstart, why = pn.pre_start, f"公式告知: 事前ダウンロード開始 {pn.pre_raw}"
            elif pn and pn.pre_now:
                pstart, why = pn.post["posted"], f"公式告知（{fmt_dt(pn.post['posted'])} 掲載）に「事前ダウンロードが可能」と記載"
            else:
                pstart = parse_iso(pre_seen[observed]["first_seen"])
                why = "公式ランチャーで事前ダウンロードの開始を検知（開始日時は検知した時刻）"
            pend = (pn.pre_end or pn.maint_start) if pn else None
            pre_item = Item(id=f"hoyo-{game_id}-pre{v.replace(' ', '')}", game=game_id, kind=KIND_PRE,
                            title=f"{label} 事前ダウンロード", start=pstart, end=pend, dl=DL_YES,
                            source_key=key)
            pre_item.add_basis(why)
            if pn and pn.pre_end:
                pre_item.add_basis(f"公式告知: 事前ダウンロードの終了 {fmt_dt(pn.pre_end)}")
            if observed:
                pre_item.status = ST_CONFIRMED
                pre_item.add_basis(f"公式ランチャー（HoYoPlay）で事前ダウンロード（{observed}）の提供を確認")
            else:
                pre_item.status = ST_SCHEDULED if pstart > now else ST_ANNOUNCED
            if pre_tag and is_numeric(v) and vstr(pre_tag) == v and main_tag:
                pre_item.size = describe_size(pre_sizes, main_tag)
            elif pn and pn.pc_size_gb:
                pre_item.size = f"約{pn.pc_size_gb}GB（公式告知・PC）"
            for src_label, url in info.sources:
                pre_item.add_source(src_label, url)
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


def build_extra_updates(game_id: str, cfg: dict, notices: list[dict], now: datetime, since: datetime,
                        fetch_text=fetch_post_text) -> list[Item]:
    """バージョン更新以外の追加データ更新（コラボ用データの配信など）。本文にダウンロードの記載があるものだけ載せる。"""
    key = f"hoyoverse:{cfg['launcher_id']}"
    items: list[Item] = []
    for p in notices:
        s = norm(p["subject"])
        if p["posted"] < since or version_of(s) or not RE_EXTRA_UPDATE.search(s) or RE_EXTRA_SKIP.search(s):
            continue
        text = fetch_text(p["id"])
        if not RE_DL_TEXT.search(text):
            continue  # 例: 誕生日プレゼントの内容更新（サーバー側の変更でダウンロードなし）
        hit = next((h for h in find_datetimes(text, p["posted"]) if h.has_time and not h.until), None)
        start = hit.start if hit else p["posted"]
        it = Item(id=f"hoyo-{game_id}-upd{p['id']}", game=game_id, kind=KIND_PATCH, title=p["subject"].strip(),
                  start=start, dl=DL_YES, source_key=key, posted=p["posted"])
        it.add_basis(f"HoYoLAB の公式お知らせ（{fmt_dt(p['posted'])} 掲載）")
        if hit:
            it.add_basis(f"告知に記載のアップデート日時「{hit.raw.strip()}」")
        it.add_basis("公式告知にデータ更新（ダウンロード）の記載あり")
        m = RE_EXTRA_SIZE.search(text)
        if m:
            it.size = f"約{m.group(1)}{m.group(2)}（公式告知・PC）"
        if RE_EXTRA_DONE.search(text):
            it.status = ST_CONFIRMED
            it.add_basis("公式告知で実施済みを確認")
        else:
            it.status = ST_SCHEDULED if start > now else ST_ANNOUNCED
        it.excerpt = excerpt(text)
        it.add_source(f"HoYoLAB「{p['subject'].strip()}」", post_url(p["id"]))
        items.append(it)
    return items
