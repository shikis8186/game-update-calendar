"""HoYoverse（原神・スターレイル）の告知の読み取りと予定作成のテスト。
告知本文は、実際のお知らせの書式を必要な部分だけ抜き出したもの。"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from collector.common import JST
from collector.model import DL_YES, KIND_MAJOR, KIND_PRE, ST_CONFIRMED, ST_SCHEDULED
from collector.sources import hoyoverse
from collector.textparse import norm

GI_TEXT = norm("""親愛なる旅人さんへ
2026/09/23 06:00 (UTC+8)よりバージョンアップに伴うメンテナンスを実施いたします。メンテナンス終了後、最新のVer.7.1「冥府へのレクイエム」に更新されます。
ただいまVer.7.1の事前ダウンロードが可能です。
〓バージョンアップ・メンテナンス情報〓
2026/09/23 06:00 (UTC+8)からおよそ5時間
アップデートメンテナンスに伴う補償：2026/09/23 06:00 (UTC+8)までに冒険ランク5に到達した旅人さん。
PC版：10GB
モバイル端末：4GB
さらなる更新内容は2026/09/23 07:00 (UTC+8)に公開されるリリースお知らせにてご確認ください。""")

HSR_TEXT = norm("""親愛なる開拓者様へ
Ver.4.6の事前ダウンロードは2026/09/24 15:00 (JST)に開始いたします。
また、列車運営チームは2026/09/28 07:00 (JST)にバージョンアップに伴うメンテナンスを実施いたします。
▌メンテナンス時間
2026/09/28 07:00 (JST) からおよそ5時間の予定。
補償対象：2026/09/28 07:00 (JST)までに開拓レベル4に達した開拓者様
● PC端末の事前ダウンロードリソースパックのサイズは約3.58GBです。""")


def post(pid, subject, posted):
    return {"id": pid, "subject": subject, "posted": posted}


class PreNoticeTest(unittest.TestCase):
    def test_genshin(self):
        pn = hoyoverse.parse_pre_notice(post("1", "Ver.7.1バージョンアップのお知らせ",
                                             datetime(2026, 9, 21, 12, 10, tzinfo=JST)), GI_TEXT)
        self.assertEqual(pn.maint_start, datetime(2026, 9, 23, 7, 0, tzinfo=JST))  # UTC+8 06:00 = 日本時間 07:00
        self.assertEqual(pn.duration_h, 5)
        self.assertTrue(pn.pre_now)
        self.assertIsNone(pn.pre_start)
        self.assertEqual(pn.pc_size_gb, "10")

    def test_starrail(self):
        pn = hoyoverse.parse_pre_notice(post("2", "Ver.4.6アップデートメンテナンスのお知らせ",
                                             datetime(2026, 9, 24, 15, 0, tzinfo=JST)), HSR_TEXT)
        self.assertEqual(pn.pre_start, datetime(2026, 9, 24, 15, 0, tzinfo=JST))
        self.assertEqual(pn.maint_start, datetime(2026, 9, 28, 7, 0, tzinfo=JST))
        self.assertEqual(pn.pc_size_gb, "3.58")


class BuildItemsTest(unittest.TestCase):
    NOW = datetime(2026, 9, 26, 12, 0, tzinfo=JST)

    def versions(self):
        notices = [
            post("2", "Ver.4.6アップデートメンテナンスのお知らせ", datetime(2026, 9, 24, 15, 0, tzinfo=JST)),
            post("3", "Ver.4.6イベント跳躍・その1", datetime(2026, 9, 27, 15, 0, tzinfo=JST)),
        ]
        return hoyoverse.collect_versions(notices, fetch_text=lambda pid: HSR_TEXT)

    def test_scheduled_update_and_observed_predownload(self):
        branch = {
            "main": {"tag": "4.5.0", "diff_tags": ["4.4.0"], "branch": "main", "package_id": "a", "password": "b"},
            "pre_download": {"tag": "4.6.0", "branch": "predownload", "package_id": "c", "password": "d"},
        }
        sizes = {"game": 3_500_000_000, "ja-jp": 150_000_000}
        state: dict = {}
        with mock.patch.object(hoyoverse, "patch_sizes", return_value=sizes):
            items = hoyoverse.build_items("HSR", {"launcher_id": "x", "hoyolab_gid": 6}, branch, self.versions(),
                                          state, self.NOW, self.NOW - timedelta(days=120))
        major = next(i for i in items if i.kind == KIND_MAJOR)
        pre = next(i for i in items if i.kind == KIND_PRE)
        self.assertEqual(major.start, datetime(2026, 9, 28, 7, 0, tzinfo=JST))
        self.assertEqual(major.end, datetime(2026, 9, 28, 12, 0, tzinfo=JST))
        self.assertEqual(major.status, ST_SCHEDULED)
        self.assertEqual(major.dl, DL_YES)
        self.assertEqual(pre.start, datetime(2026, 9, 24, 15, 0, tzinfo=JST))
        self.assertEqual(pre.end, major.start)
        self.assertEqual(pre.status, ST_CONFIRMED, "ランチャーで事前DLが見えている")
        self.assertIn("3.4GB", pre.size)
        self.assertEqual(state["history"][-1]["tag"], "4.5.0")

    def test_confirmed_after_release(self):
        branch = {"main": {"tag": "4.6.0", "diff_tags": ["4.5.0"], "branch": "main", "package_id": "a", "password": "b"},
                  "pre_download": None}
        now = datetime(2026, 9, 29, 12, 0, tzinfo=JST)
        with mock.patch.object(hoyoverse, "patch_sizes", return_value={"game": 3_500_000_000}):
            items = hoyoverse.build_items("HSR", {"launcher_id": "x", "hoyolab_gid": 6}, branch, self.versions(),
                                          {}, now, now - timedelta(days=120))
        major = next(i for i in items if i.kind == KIND_MAJOR)
        self.assertEqual(major.status, ST_CONFIRMED)
        self.assertTrue(major.size.startswith("約3.3GB"))

    def test_supplement_only_skips_past_versions(self):
        now = datetime(2026, 10, 10, 12, 0, tzinfo=JST)
        items = hoyoverse.build_items("ZZZ", {"launcher_id": "x", "hoyolab_gid": 8, "supplement_only": True},
                                      None, self.versions(), {}, now, now - timedelta(days=120))
        self.assertEqual([i for i in items if i.kind == KIND_MAJOR], [])


if __name__ == "__main__":
    unittest.main()
