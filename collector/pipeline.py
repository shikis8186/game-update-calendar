"""全情報源からデータを集め、カレンダー用の JSON（docs/data/events.json）を書き出す。

ある情報源の取得に失敗しても、ほかの情報源の更新は続ける。
失敗した情報源の予定は前回の正しいデータをそのまま残し、画面には「取得失敗」と表示する。
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .common import FetchError, from_ts, log, now_jst, parse_iso, to_iso
from .linking import build_items as steam_build_items
from .linking import cluster, link
from .model import Item
from .sources import hoyoverse, lol, nikke, steam_builds, steam_news

PAST_DAYS = 120     # 過去何日分を残すか
FUTURE_DAYS = 240   # 先何日分まで載せるか


@dataclass
class SourceResult:
    key: str
    label: str
    games: list[str]
    ok: bool = True
    message: str = ""
    items: list[Item] = field(default_factory=list)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def run(root: Path, steamcmd: str | None = None, now: datetime | None = None) -> dict:
    now = now or now_jst()
    since = now - timedelta(days=PAST_DAYS)
    until = now + timedelta(days=FUTURE_DAYS)
    config = tomllib.loads((root / "config" / "games.toml").read_text(encoding="utf-8"))
    games = config["games"]
    state_path = root / "state" / "state.json"
    out_path = root / "docs" / "data" / "events.json"
    state = load_json(state_path, {})
    previous = load_json(out_path, {})
    results: list[SourceResult] = []

    # ---------------------------------------------------------------- Steam
    steam_games = [g for g in games if g.get("steam")]
    appids = [g["steam"]["appid"] for g in steam_games]
    sstate = state.setdefault("steam", {})
    build_res = SourceResult("steambuild", "Steam ビルド更新", [g["id"] for g in steam_games])
    if appids:
        builds, origin, warnings = steam_builds.get_build_info(appids, steamcmd)
        for appid, b in builds.items():
            st = sstate.setdefault(str(appid), {"history": []})
            hist = st["history"]
            if not hist or hist[-1]["buildid"] != b["buildid"]:
                if not any(h["buildid"] == b["buildid"] for h in hist):
                    hist.append({**b, "first_seen": to_iso(now)})
                    log.info("Steam %s: 新しいビルド %s", appid, b["buildid"])
            st["checked_at"] = to_iso(now)
            st["origin"] = origin.get(appid, "")
        if warnings:
            build_res.message = " / ".join(warnings)
        if len(builds) < len(appids):
            build_res.ok = False
        results.append(build_res)

    for g in steam_games:
        appid = g["steam"]["appid"]
        res = SourceResult(f"steam:{appid}", "Steam のお知らせ", [g["id"]])
        try:
            events = steam_news.fetch_events(appid, since)
            res.items = steam_news.to_items(g["id"], appid, events, now, since)
        except FetchError as e:
            res.ok, res.message = False, str(e)
        results.append(res)

    # ---------------------------------------------------------------- HoYoverse
    hoyo_games = [g for g in games if g.get("hoyoverse")]
    branches: dict = {}
    if hoyo_games:
        launch_res = SourceResult("hoyoplay", "HoYoPlay（公式ランチャー）", [g["id"] for g in hoyo_games])
        try:
            branches = hoyoverse.fetch_branches()
        except FetchError as e:
            launch_res.ok, launch_res.message = False, str(e)
        results.append(launch_res)
    hstate = state.setdefault("hoyoverse", {})
    for g in hoyo_games:
        cfg = g["hoyoverse"]
        res = SourceResult(f"hoyoverse:{cfg['launcher_id']}", "HoYoLAB の公式お知らせ", [g["id"]])
        gstate = hstate.setdefault(cfg["launcher_id"], {})
        try:
            notices = hoyoverse.fetch_notices(cfg["hoyolab_gid"])
            versions = hoyoverse.collect_versions(notices)
            res.items = hoyoverse.build_items(g["id"], cfg, branches.get(cfg["launcher_id"]), versions,
                                              gstate, now, since)
        except FetchError as e:
            res.ok, res.message = False, str(e)
            # お知らせが取れなくても、ランチャーで見えたバージョンの履歴は記録しておく（予定は前回分を表示）
            try:
                hoyoverse.build_items(g["id"], cfg, branches.get(cfg["launcher_id"]), {}, gstate, now, since)
            except FetchError:
                pass
        results.append(res)

    # ---------------------------------------------------------------- LoL
    for g in games:
        if not (g.get("lol") or {}).get("enabled"):
            continue
        res = SourceResult("lol", "Riot 公式（スケジュール・パッチノート）", [g["id"]])
        try:
            schedule = lol.fetch_schedule()
            notes = lol.fetch_notes()
            try:
                live = lol.fetch_live_patches()
            except FetchError as e:
                live = None
                res.message = f"配信済みバージョンの確認に失敗（{e}）"
            res.items = lol.build_items(g["id"], schedule, notes, live, now, since)
        except FetchError as e:
            res.ok, res.message = False, str(e)
        results.append(res)

    # ---------------------------------------------------------------- NIKKE
    for g in games:
        if not (g.get("nikke") or {}).get("enabled"):
            continue
        res = SourceResult("nikke", "NIKKE 公式サイトのお知らせ", [g["id"]])
        try:
            res.items = nikke.build_items(g["id"], nikke.fetch_notices(since), now, since)
        except FetchError as e:
            res.ok, res.message = False, str(e)
        results.append(res)

    # ---------------------------------------------------------------- 失敗した情報源は前回分を残す
    prev_items = previous.get("items") or []
    carried: list[dict] = []
    for r in results:
        if not r.ok and r.key != "steambuild":
            old = [i for i in prev_items if i.get("source_key") == r.key]
            for i in old:
                i["stale"] = True
            carried.extend(old)

    # ---------------------------------------------------------------- Steam ビルドとの突き合わせ
    all_items: list[Item] = [i for r in results for i in r.items]
    for g in steam_games:
        appid = g["steam"]["appid"]
        st = sstate.get(str(appid)) or {}
        hist = st.get("history") or []
        builds = steam_build_items(g["id"], appid, hist)
        coverage_from = from_ts(hist[0]["timeupdated"]) if hist else None
        checked_at = parse_iso(st["checked_at"]) if st.get("checked_at") else None
        game_items = [i for i in all_items if i.game == g["id"]]
        unmatched = link(game_items, builds, coverage_from, checked_at)
        # お知らせの取得に失敗して前回分を表示している場合、そこに含まれるビルド更新は二重に載せない
        carried_basis = " ".join(" ".join(i.get("basis", [])) for i in carried if i.get("game") == g["id"])
        all_items.extend(b for b in unmatched
                         if not any(f"ビルドID {h['buildid']}" in carried_basis and f"ビルドID {h['buildid']}" in " ".join(b.basis)
                                    for h in hist))
    for g in games:
        cluster([i for i in all_items if i.game == g["id"]])

    # ---------------------------------------------------------------- 出力
    final = [i for i in all_items if not i.merged and (i.end or i.start) >= since and i.start <= until]
    final.sort(key=lambda i: (i.start, i.game))
    status_state = state.setdefault("source_status", {})
    sources_out = []
    for r in results:
        st = status_state.setdefault(r.key, {})
        if r.ok:
            st["last_success"] = to_iso(now)
        sources_out.append({
            "key": r.key, "label": r.label, "games": r.games, "ok": r.ok,
            "message": r.message, "last_success": st.get("last_success"),
        })
    data = {
        "generated_at": to_iso(now),
        "games": [{"id": g["id"], "name": g["name"], "color": g["color"], "text_color": g["text_color"]}
                  for g in games],
        "items": [i.to_json() for i in final] + carried,
        "sources": sources_out,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return data
