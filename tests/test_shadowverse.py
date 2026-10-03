"""シャドバ公式サイトのお知らせの読み取りのテスト。本文は実際の告知の書式を必要な部分だけ抜き出したもの。"""
import unittest
from datetime import datetime, timedelta

from collector.common import JST
from collector.model import DL_LIKELY, DL_UNKNOWN, DL_YES, KIND_MAINT, KIND_PATCH, ST_CONFIRMED, ST_SCHEDULED
from collector.sources import shadowverse_wb as s

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=JST)
SINCE = NOW - timedelta(days=120)

TEXTS = {
    "maint": "シーズン更新や各種施策開催などのため、下記の時間帯にメンテナンスを実施いたします。\n"
             "メンテナンス期間2026/9/29 14:00 ~ 17:00\n【9/29 17:00追記】\n"
             "2026/9/29 17:00にメンテナンスを終了いたしました。",
    "update": "不具合修正のため、2026/10/2 11:30頃にアップデートを行いました。\n"
              "アップデート後は通信を行うとタイトル画面に戻り、データのダウンロードが行われます。",
    "version": "不具合の修正のため、Ver.1.9.11を公開いたしました。\n本バージョンへの強制アップデート予定はございません。",
    "park": "不具合修正のため、下記の時間帯にパークメンテナンスとアップデートを実施いたします。\n"
            "パークメンテナンス期間2026/9/18 11:30 ~ 12:00\nアップデート日時・2026/9/18 11:30ごろ\n"
            "※アップデート後は通信を行うとタイトル画面に戻り、データのダウンロードが行われます。",
    "future": "新カードパック追加などのため、下記の時間帯にメンテナンスを実施いたします。\nメンテナンス期間2026/10/28 14:00 ~ 17:00",
    "ipados": "「iPadOS 27」へのアップデートについてのお知らせです。現在、動作確認を行っております。",
}


def notice(id_, title, type_, posted):
    return {"id": id_, "title": title, "type": type_, "posted": datetime.fromisoformat(posted).replace(tzinfo=JST)}


class ShadowverseTest(unittest.TestCase):
    def build(self, notices):
        return {i.id: i for i in s.build_items("SVWB", notices, NOW, SINCE, get_text=lambda id_: TEXTS[id_])}

    def test_maintenance_period_and_completion(self):
        it = self.build([notice("maint", "【9月29日 14:00 ～ 17:00】シーズン更新や各種施策開催などに伴うメンテナンスのお知らせ【9/29 17:00追記】",
                                "メンテナンス", "2026-09-21T19:00")])["svwb-maint"]
        self.assertEqual(it.kind, KIND_MAINT)
        self.assertEqual((it.start, it.end), (datetime(2026, 9, 29, 14, 0, tzinfo=JST), datetime(2026, 9, 29, 17, 0, tzinfo=JST)))
        self.assertEqual(it.status, ST_CONFIRMED)
        self.assertEqual(it.dl, DL_UNKNOWN, "ダウンロードの記載がないメンテナンスは「不明」のまま")

    def test_update_notice_with_download(self):
        it = self.build([notice("update", "アップデートのお知らせ", "お知らせ", "2026-10-02T11:40")])["svwb-update"]
        self.assertEqual((it.kind, it.dl, it.status), (KIND_PATCH, DL_YES, ST_CONFIRMED))
        self.assertEqual(it.start, datetime(2026, 10, 2, 11, 30, tzinfo=JST), "掲載時刻ではなく本文のアップデート時刻")

    def test_version_publish(self):
        it = self.build([notice("version", "Ver.1.9.11の公開について", "バージョンアップ", "2026-10-02T12:00")])["svwb-version"]
        self.assertEqual((it.kind, it.dl, it.status), (KIND_PATCH, DL_YES, ST_CONFIRMED))

    def test_park_maintenance_with_download(self):
        it = self.build([notice("park", "パークメンテナンスとアップデートのお知らせ【9/18 12:00追記】", "メンテナンス", "2026-09-17T15:00")])["svwb-park"]
        self.assertEqual(it.start, datetime(2026, 9, 18, 11, 30, tzinfo=JST))
        self.assertEqual(it.dl, DL_YES)

    def test_future_maintenance_is_scheduled(self):
        it = self.build([notice("future", "【10月28日 14:00 ～ 17:00】新カードパック追加などの更新に伴うメンテナンスのお知らせ",
                                "メンテナンス", "2026-10-21T12:00")])["svwb-future"]
        self.assertEqual(it.status, ST_SCHEDULED)
        self.assertEqual(it.start, datetime(2026, 10, 28, 14, 0, tzinfo=JST))

    def test_device_os_notice_is_not_a_game_update(self):
        self.assertEqual(self.build([notice("ipados", "「iPadOS 27」へのアップデートについて", "お知らせ", "2026-09-18T17:00")]), {})


if __name__ == "__main__":
    unittest.main()
