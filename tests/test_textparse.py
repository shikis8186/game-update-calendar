"""日付の読み取り（textparse）のテスト。実際の告知タイトル・本文の書式を使っている。"""
import unittest
from datetime import datetime

from collector.common import JST
from collector.textparse import find_datetimes, norm

POSTED = datetime(2026, 9, 29, 16, 0, tzinfo=JST)


def first(text, ref=POSTED):
    hits = find_datetimes(norm(text), ref)
    return hits[0] if hits else None


class TextParseTest(unittest.TestCase):
    def test_slash_date_with_jst_range(self):
        h = first("[Completed] 10/01 定期メンテナンスのお知らせ (JST 14:00 - 17:30)")
        self.assertEqual(h.start, datetime(2026, 10, 1, 14, 0, tzinfo=JST))
        self.assertEqual(h.end, datetime(2026, 10, 1, 17, 30, tzinfo=JST))

    def test_slash_date_with_utc_range_is_converted_to_jst(self):
        h = first("[Completed] 8/6 定期メンテナンスのお知らせ (UTC 05:00 - 07:35)", datetime(2026, 8, 4, tzinfo=JST))
        self.assertEqual(h.start, datetime(2026, 8, 6, 14, 0, tzinfo=JST))
        self.assertEqual(h.end, datetime(2026, 8, 6, 16, 35, tzinfo=JST))

    def test_full_width_tilde(self):
        h = first("[Completed] 08/27 定期メンテナンス (JST 14:00 ～ 15:45)", datetime(2026, 8, 25, tzinfo=JST))
        self.assertEqual((h.start.hour, h.end.hour, h.end.minute), (14, 15, 45))

    def test_year_month_day_japanese(self):
        h = first("Year 4追加キャラクター第2弾「アルジュン」が2026年10月13日に参戦！")
        self.assertEqual(h.start.date().isoformat(), "2026-10-13")
        self.assertFalse(h.has_time)
        self.assertFalse(h.until)

    def test_until_is_detected(self):
        h = first("ジュリのaespaコラボコスチュームの販売は2026年7月4日まで")
        self.assertTrue(h.until)

    def test_utc8_is_converted(self):
        text = "親愛なる旅人さんへ\n2026/09/23 06:00 (UTC+8)よりバージョンアップに伴うメンテナンスを実施いたします。"
        h = first(text, datetime(2026, 9, 21, 12, 10, tzinfo=JST))
        self.assertEqual(h.start, datetime(2026, 9, 23, 7, 0, tzinfo=JST))

    def test_jst_label(self):
        h = first("Ver.4.6の事前ダウンロードは2026/09/24 15:00 (JST)に開始いたします。")
        self.assertEqual(h.start, datetime(2026, 9, 24, 15, 0, tzinfo=JST))

    def test_month_day_with_weekday_and_time_range(self):
        h = first("◆実施日時 4月9日(木)11:00～15:00", datetime(2026, 4, 6, tzinfo=JST))
        self.assertEqual(h.start, datetime(2026, 4, 9, 11, 0, tzinfo=JST))
        self.assertEqual(h.end, datetime(2026, 4, 9, 15, 0, tzinfo=JST))

    def test_year_inferred_across_new_year(self):
        h = first("1月7日のアップデートについて", datetime(2026, 12, 28, tzinfo=JST))
        self.assertEqual(h.start.year, 2027)

    def test_version_numbers_are_not_dates(self):
        self.assertIsNone(first("ゲームプレイパッチ7.41eとサマースクラブ"))
        self.assertIsNone(first("パッチノート 26.19"))
        self.assertIsNone(first("Ver.3.2「秘密と、過去と、彼女たちと」アップデート詳細"))

    def test_dotted_period(self):
        hits = find_datetimes(norm("2026.9.3 ~ 2026.9.9 違法プログラム使用に伴う利用制限措置"), POSTED)
        self.assertEqual([h.start.day for h in hits], [3, 9])


if __name__ == "__main__":
    unittest.main()
