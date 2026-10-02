"""カレンダーに載せない定期的なお知らせ（不正利用者の処分リストなど）の判定。"""
from __future__ import annotations

import re

from ..textparse import norm

_NOISE = re.compile(
    r"不正(?:プログラム|行為|利用).{0,20}(?:処分|制裁|措置)|制裁リスト|利用制限措置.{0,10}リスト|"
    r"違法プログラム使用に伴う|BAN\s*(?:list|wave)|disciplinary action",
    re.I,
)


def is_noise(title: str) -> bool:
    return bool(_NOISE.search(norm(title)))
