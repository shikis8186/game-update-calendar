"""LoL（公式スケジュール）と NIKKE（公式サイトのお知らせ）のテスト。"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from collector.common import JST, FetchError
from collector.model import DL_YES, KIND_NEWS, KIND_PATCH, ST_CONFIRMED, ST_SCHEDULED
from collector.sources import lol, nikke

SCHEDULE_HTML = """<html><body><h1>Patch Schedule - League of Legends</h1>
<table><tr><th>Patch</th><th>Scheduled Date (Pacific Time)</th></tr>
""" + "".join(
    f"<tr><td>26.{n:02d}</td><td>{d}</td></tr>" for n, d in [
        (1, "January 8, 2026 (Thursday)"), (2, "January 22, 2026 (Thursday)"), (3, "February 4, 2026"),
        (4, "February 19, 2026 (Thursday)"), (5, "March 4, 2026"), (6, "March 18, 2026"), (7, "April 1, 2026"),
        (8, "April 15, 2026"), (9, "April 29, 2026"), (10, "May 13, 2026"), (18, "September 10, 2026 (Thursday)"),
        (19, "September 23, 2026"), (20, "October 7, 2026"),
    ]) + "</table></body></html>"


class LolTest(unittest.TestCase):
    NOW = datetime(2026, 10, 2, 20, 0, tzinfo=JST)

    def test_schedule_parse(self):
        with mock.patch.object(lol, "request_text", return_value=SCHEDULE_HTML):
            rows = lol.fetch_schedule()
        self.assertIn(((26, 20), datetime(2026, 10, 7, tzinfo=JST)), rows)
        self.assertEqual(len(rows), 13)

    def test_schedule_parse_fails_loudly(self):
        with mock.patch.object(lol, "request_text", return_value="<html>maintenance</html>"):
            with self.assertRaises(FetchError):
                lol.fetch_schedule()

    def test_items(self):
        with mock.patch.object(lol, "request_text", return_value=SCHEDULE_HTML):
            rows = lol.fetch_schedule()
        notes = {(26, 19): {"title": "パッチノート 26.19", "url": "https://example.invalid/26-19"}}
        items = lol.build_items("LOL", rows, notes, {(26, 19), (26, 18)}, self.NOW, self.NOW - timedelta(days=120))
        by_id = {i.id: i for i in items}
        self.assertEqual(by_id["lol-26-19"].status, ST_CONFIRMED)
        self.assertEqual(by_id["lol-26-19"].title, "パッチノート 26.19")
        self.assertEqual(by_id["lol-26-20"].status, ST_SCHEDULED)
        self.assertTrue(all(i.dl == DL_YES and i.kind == KIND_PATCH and i.all_day for i in items))
        self.assertNotIn("lol-26-1", by_id, "期間外の古いパッチは載せない")


NIKKE_DONE = ("親愛なる指揮官様\nアップデートのメンテナンスが終了し、サーバー再開されました。\n"
              "今回のアップデートではゲームクライアントがアップデートされるため、ストアから最新バージョンをダウンロードして、"
              "ゲームをお楽しみください。")
NIKKE_BEFORE = "親愛なる指揮官様\n下記日時にてメンテナンス及びバージョンアップを行います。\n◆実施日時 10月1日(水)11:00～15:00"


def notice(cid, title, posted):
    return {"id": cid, "title": title, "posted": datetime.fromisoformat(posted).replace(tzinfo=JST)}


class NikkeTest(unittest.TestCase):
    NOW = datetime(2026, 9, 29, 12, 0, tzinfo=JST)

    def build(self, notices, texts):
        return nikke.build_items("NIKKE", notices, self.NOW, self.NOW - timedelta(days=120),
                                 get_text=lambda cid: texts.get(cid, ""))

    def test_completed_update_with_client_download(self):
        items = self.build([
            notice("a", "9月17日のアップデートについて（9月17日更新）", "2026-09-14T18:00"),
            notice("b", "9月17日のアップデートによる改善事項", "2026-09-17T17:56"),
            notice("c", "9月17日既知の問題について（9月24日更新）", "2026-09-17T17:56"),
            notice("d", "不正行為の制裁リストに関するお知らせ 2026.9.23", "2026-09-23T23:18"),
        ], {"a": NIKKE_DONE})
        upd = next(i for i in items if i.kind == KIND_PATCH)
        self.assertEqual(upd.start.date().isoformat(), "2026-09-17")
        self.assertEqual((upd.dl, upd.status), (DL_YES, ST_CONFIRMED))
        self.assertEqual(len(upd.sources), 2, "改善事項の告知は同じアップデートの情報源として付く")
        self.assertEqual([i.title for i in items if i.kind == KIND_NEWS], ["9月17日既知の問題について（9月24日更新）"])

    def test_upcoming_update_uses_body_time(self):
        items = self.build([notice("e", "10月1日のアップデートについて", "2026-09-28T18:00")], {"e": NIKKE_BEFORE})
        upd = items[0]
        self.assertEqual(upd.start, datetime(2026, 10, 1, 11, 0, tzinfo=JST))
        self.assertEqual(upd.end, datetime(2026, 10, 1, 15, 0, tzinfo=JST))
        self.assertEqual(upd.status, ST_SCHEDULED)
        self.assertEqual(upd.dl, "likely")


if __name__ == "__main__":
    unittest.main()
