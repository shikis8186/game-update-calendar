"""実行方法:  python -m collector [--steamcmd <steamcmd のパス>]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .pipeline import run


def main() -> int:
    parser = argparse.ArgumentParser(description="ゲームのアップデート情報を集めて docs/data/events.json を更新します")
    parser.add_argument("--steamcmd", help="SteamCMD の実行ファイルのパス（省略時は Web API で取得）")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="プロジェクトのフォルダ")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data = run(Path(args.root), steamcmd=args.steamcmd)
    failed = [s for s in data["sources"] if not s["ok"]]
    print(f"予定 {len(data['items'])} 件を書き出しました。")
    for s in data["sources"]:
        mark = "OK " if s["ok"] else "NG "
        print(f"  {mark}{s['label']}（{', '.join(s['games'])}）{(' ' + s['message']) if s['message'] else ''}")
    # 一部の情報源の失敗ではエラー終了にしない（前回のデータを残して公開を続けるため）。
    # すべて失敗した場合だけ、ネットワーク障害などとしてエラー終了する。
    return 1 if failed and len(failed) == len(data["sources"]) else 0


if __name__ == "__main__":
    sys.exit(main())
