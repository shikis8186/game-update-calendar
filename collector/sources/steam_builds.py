"""Steam の公開ビルド（public ブランチ）の番号と更新時刻を取得する。

ゲーム本体のデータが更新されると、ビルド番号と更新時刻（秒単位）が変わる。
これを「ダウンロードが発生した」ことの確定判定に使う。

取得方法:
  1. SteamCMD（Valve 公式のコマンドラインツール）。GitHub Actions ではこちらを使う。
  2. 1 が使えない場合は api.steamcmd.net（SteamCMD と同じ情報を返す Web API）。
"""
from __future__ import annotations

import re
import subprocess
import time

from ..common import FetchError, log, request_json

_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Valve の KeyValues（VDF）形式を辞書に変換する。"""
    tokens = [(m.group(1), m.group(2)) for m in _TOKEN_RE.finditer(text)]
    pos = 0

    def parse_obj() -> dict:
        nonlocal pos
        obj: dict = {}
        while pos < len(tokens):
            key, brace = tokens[pos]
            if brace == "}":
                pos += 1
                return obj
            if key is None:
                raise ValueError("VDF の形式が不正です")
            pos += 1
            if pos >= len(tokens):
                raise ValueError("VDF が途中で終わっています")
            val, vbrace = tokens[pos]
            if vbrace == "{":
                pos += 1
                obj[key] = parse_obj()
            elif val is not None:
                pos += 1
                obj[key] = val.replace('\\"', '"').replace("\\\\", "\\")
            else:
                raise ValueError("VDF の形式が不正です")
        return obj

    return parse_obj()


def extract_app_blocks(output: str, appids: list[int]) -> dict[int, dict]:
    """SteamCMD の出力から、各アプリの情報ブロック `"570" { ... }` を取り出す。"""
    result: dict[int, dict] = {}
    for appid in appids:
        m = re.search(r'^\s*"%d"\s*\n\s*\{' % appid, output, re.M)
        if not m:
            continue
        i = output.index("{", m.start())
        depth = 0
        while i < len(output):
            c = output[i]
            if c == '"':
                # 文字列の中の波かっこは数えない
                i += 1
                while i < len(output) and output[i] != '"':
                    i += 2 if output[i] == "\\" else 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    parsed = parse_vdf(output[m.start(): i + 1])
                    result[appid] = parsed.get(str(appid), {})
                    break
            i += 1
    return result


def _public_branch(info: dict) -> dict | None:
    branch = ((info.get("depots") or {}).get("branches") or {}).get("public")
    if not branch:
        return None
    try:
        buildid = int(branch["buildid"])
        updated = int(branch["timeupdated"])
    except (KeyError, TypeError, ValueError):
        return None
    now = int(time.time())
    if buildid <= 0 or not (1262304000 <= updated <= now + 86400):
        return None  # 2010年より前や未来の時刻は異常値として扱う
    return {"buildid": buildid, "timeupdated": updated}


def via_steamcmd(steamcmd: str, appids: list[int]) -> dict[int, dict]:
    args = [steamcmd, "+@ShutdownOnFailedCommand", "0", "+@NoPromptForPassword", "1",
            "+login", "anonymous", "+app_info_update", "1"]
    for appid in appids:
        args += ["+app_info_print", str(appid)]
    args.append("+quit")
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)
    blocks = extract_app_blocks(proc.stdout, appids)
    out: dict[int, dict] = {}
    for appid, info in blocks.items():
        b = _public_branch(info)
        if b:
            out[appid] = b
    return out


def via_web_api(appid: int) -> dict | None:
    d = request_json(f"https://api.steamcmd.net/v1/info/{appid}", timeout=30)
    if d.get("status") != "success":
        return None
    info = (d.get("data") or {}).get(str(appid)) or {}
    return _public_branch(info)


def get_build_info(appids: list[int], steamcmd: str | None) -> tuple[dict[int, dict], dict[int, str], list[str]]:
    """戻り値: (appid → {buildid, timeupdated}, appid → 取得元, 警告の一覧)"""
    builds: dict[int, dict] = {}
    origin: dict[int, str] = {}
    warnings: list[str] = []
    if steamcmd:
        try:
            builds = via_steamcmd(steamcmd, appids)
            for a in builds:
                origin[a] = "SteamCMD"
        except (OSError, subprocess.SubprocessError) as e:
            warnings.append(f"SteamCMD の実行に失敗しました（{e}）。Web API で取得します")
        missing = [a for a in appids if a not in builds]
        if missing:
            log.info("SteamCMD で取得できなかったアプリ: %s", missing)
            warnings.append(f"SteamCMD で取得できなかったアプリ（{', '.join(map(str, missing))}）は Web API で取得しました")
    for appid in appids:
        if appid in builds:
            continue
        try:
            b = via_web_api(appid)
        except FetchError as e:
            warnings.append(f"アプリ {appid} のビルド情報を取得できませんでした（{e}）")
            continue
        if b:
            builds[appid] = b
            origin[appid] = "api.steamcmd.net"
        else:
            warnings.append(f"アプリ {appid} のビルド情報が見つかりませんでした")
    return builds, origin, warnings
