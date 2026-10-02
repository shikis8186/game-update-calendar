"""カレンダーに載せる 1 件分の予定（Item）の定義。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .common import JST, to_iso

# 予定の種類
KIND_MAJOR = "major"   # 大型アップデート（バージョン更新・新キャラクター・新シーズンなど）
KIND_PATCH = "patch"   # パッチ・クライアント更新
KIND_MAINT = "maint"   # メンテナンス
KIND_EVENT = "event"   # ゲーム内イベント・配信番組
KIND_PRE = "pre"       # 事前ダウンロード期間
KIND_NEWS = "news"     # その他のお知らせ
UPDATE_KINDS = (KIND_MAJOR, KIND_PATCH, KIND_MAINT)

# ダウンロードの有無
DL_YES = "yes"         # あり（実データで確認済み、または公式告知に明記）
DL_LIKELY = "likely"   # あり見込み（告知の種類から推定。配信後に確定させる）
DL_NO = "no"           # なし（実データで更新がないことを確認、または告知に明記）
DL_UNKNOWN = "unknown"

# 状態
ST_CONFIRMED = "confirmed"  # 実際に配信・実施されたことを確認済み
ST_SCHEDULED = "scheduled"  # 公式に予定が告知されている（未来の日付）
ST_ANNOUNCED = "announced"  # 告知の掲載のみ（実施の確認手段がない）

KIND_PRIORITY = {KIND_MAJOR: 0, KIND_PATCH: 1, KIND_MAINT: 2, KIND_PRE: 3, KIND_EVENT: 4, KIND_NEWS: 5}


@dataclass
class Item:
    id: str
    game: str
    kind: str
    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    dl: str = DL_UNKNOWN
    status: str = ST_ANNOUNCED
    size: str | None = None
    basis: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # {"label": ..., "url": ...}
    excerpt: str | None = None
    note: str | None = None
    source_key: str = ""
    # 内部処理用（JSON には出さない）
    posted: datetime | None = None
    steam_event_type: int | None = None
    merged: bool = False

    def add_source(self, label: str, url: str | None) -> None:
        if url and any(s.get("url") == url for s in self.sources):
            return
        self.sources.append({"label": label, "url": url})

    def add_basis(self, text: str) -> None:
        if text not in self.basis:
            self.basis.append(text)

    def to_json(self) -> dict:
        d: dict = {
            "id": self.id,
            "game": self.game,
            "kind": self.kind,
            "title": self.title,
            "all_day": self.all_day,
            "dl": self.dl,
            "status": self.status,
            "basis": self.basis,
            "sources": self.sources,
            "source_key": self.source_key,
        }
        if self.all_day:
            d["start"] = self.start.astimezone(JST).date().isoformat()
            if self.end:
                d["end"] = self.end.astimezone(JST).date().isoformat()
        else:
            d["start"] = to_iso(self.start)
            if self.end:
                d["end"] = to_iso(self.end)
        for key in ("size", "excerpt", "note"):
            value = getattr(self, key)
            if value:
                d[key] = value
        return d
