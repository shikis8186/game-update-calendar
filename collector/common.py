"""共通ユーティリティ: HTTP 通信、日時、サイズ表記。

外部ライブラリを使わず標準ライブラリだけで動くようにしている
（GitHub Actions でもローカルでも、そのまま実行できるようにするため）。
"""
from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
UTC8 = timezone(timedelta(hours=8))

log = logging.getLogger("collector")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 GameUpdateCalendar/1.0"
)


class FetchError(Exception):
    """取得や検証に失敗したことを表す。呼び出し側はこの情報源を「失敗」として扱う。"""


def request_text(
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    headers: dict | None = None,
    body: dict | str | None = None,
    timeout: int = 30,
    retries: int = 3,
) -> str:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read()
                if res.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode(res.headers.get_content_charset() or "utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_error = e
            if 400 <= e.code < 500 and e.code != 429:
                break  # 再試行しても結果は変わらない
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_error = e
        time.sleep(2 * (attempt + 1))
    raise FetchError(f"{method} {url} に失敗: {last_error}")


def request_json(url: str, **kwargs) -> dict:
    text = request_text(url, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise FetchError(f"{url} の応答が JSON ではありません: {text[:120]!r}") from e


# ---------------------------------------------------------------- 日時

def now_jst() -> datetime:
    return datetime.now(JST)


def from_ts(ts: int | str) -> datetime:
    return datetime.fromtimestamp(int(ts), JST)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(JST).isoformat(timespec="minutes")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)


def fmt_dt(dt: datetime) -> str:
    """画面の判定根拠に載せる日時表記（日本時間）。"""
    return dt.astimezone(JST).strftime("%Y/%m/%d %H:%M")


# ---------------------------------------------------------------- サイズ

def fmt_size(num_bytes: int) -> str:
    """Windows のエクスプローラーと同じ 1GB = 1024^3 バイトで表記する。"""
    gib = num_bytes / 1024**3
    if gib >= 1:
        return f"約{gib:.1f}GB"
    return f"約{max(1, round(num_bytes / 1024**2))}MB"
