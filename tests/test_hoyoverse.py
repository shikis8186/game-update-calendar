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


ZZZ_TEXT = norm("""親愛なるプロキシ様へ
Ver.3.2の事前ダウンロードが開始されました。
【事前ダウンロード期間】2026/09/07 13:00 (JST) ~ 2026/09/09 06:50 (JST)
【事前ダウンロード詳細】● PC端末
事前ダウンロードリソースのサイズはおよそ 6 GBです。
● モバイル端末(Android、iOS)
事前ダウンロードリソースのサイズはおよそ 5 GBです。
なお、近日中にバージョンアップに伴うメンテナンスが行われる予定です。
【バージョンアップ日時】2026/09/09 07:00 (JST)より開始、所要時間は約5時間と予想されます。
メンテナンスに伴う補償の範囲:2026/09/09 07:00 (JST) までにインターノットレベルが4以上に達したプロキシ様。""")

GI_LUNA_TEXT = norm("""2026/07/01 06:00 (UTC+8)よりバージョンアップに伴うメンテナンスを実施いたします。
ただいま「Luna VIII」の事前ダウンロードが可能です。
PC版:7GB""")

HSR_COLLAB_TEXT = norm("""親愛なる開拓者の皆様へ
列車運営チームは2026/07/21 16:00(JST)に、コラボイベント「幻造:聖杯戦争」に伴うアップデートを行いました。2026/07/23 15:00 (JST)までにアップデートデータをダウンロードしなかった場合、強制終了が発生します。
ゲームを再起動すると、PC版では約【540】MB、Android版では約【300】MBのデータが更新されます。""")

ZZZ_BIRTHDAY_TEXT = norm("""• 2026/07/04 01:00:00(JST)以降、誕生日メールにて配布される誕生日プレゼントが更新されます。""")


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


class NewFormatsTest(unittest.TestCase):
    """2026-10-03 の調査で見つかった取りこぼしの再発防止。"""

    def test_zzz_predownload_period(self):
        pn = hoyoverse.parse_pre_notice(post("z", "Ver.3.2「秘密と、過去と、彼女たちと」事前ダウンロード開始＆アップデートのお知らせ",
                                             datetime(2026, 9, 7, 13, 5, tzinfo=JST)), ZZZ_TEXT)
        self.assertEqual(pn.pre_start, datetime(2026, 9, 7, 13, 0, tzinfo=JST))
        self.assertEqual(pn.pre_end, datetime(2026, 9, 9, 6, 50, tzinfo=JST))
        self.assertEqual(pn.maint_start, datetime(2026, 9, 9, 7, 0, tzinfo=JST))
        self.assertEqual(pn.duration_h, 5)
        self.assertEqual(pn.pc_size_gb, "6")

    def test_zzz_notice_title_is_recognized(self):
        notices = [post("z", "Ver.3.2「秘密と、過去と、彼女たちと」事前ダウンロード開始＆アップデートのお知らせ",
                        datetime(2026, 9, 7, 13, 5, tzinfo=JST))]
        versions = hoyoverse.collect_versions(notices, fetch_text=lambda pid: ZZZ_TEXT)
        self.assertEqual(versions["3.2"].name, "秘密と、過去と、彼女たちと")
        self.assertIsNotNone(versions["3.2"].pre)

    def test_named_version_luna(self):
        self.assertEqual(hoyoverse.version_of("「Luna Ⅷ」バージョンアップのお知らせ"), ("Luna VIII", "Luna VIII"))
        notices = [
            post("a", "「Luna Ⅷ」バージョンアップのお知らせ", datetime(2026, 6, 29, 12, 10, tzinfo=JST)),
            post("b", "「『空月の歌・喜曲』帰夏！映影？千霊祭！」「Luna Ⅷ」正式リリース", datetime(2026, 7, 1, 8, 0, tzinfo=JST)),
        ]
        versions = hoyoverse.collect_versions(notices, fetch_text=lambda pid: GI_LUNA_TEXT)
        info = versions["Luna VIII"]
        self.assertEqual(info.name, "『空月の歌・喜曲』帰夏!映影?千霊祭!", "バージョン名ではなく副題を使う")
        now = datetime(2026, 7, 5, tzinfo=JST)
        items = hoyoverse.build_items("GI", {"launcher_id": "x"}, None, versions, {}, now, now - timedelta(days=120))
        major = next(i for i in items if i.kind == KIND_MAJOR)
        self.assertEqual(major.start, datetime(2026, 7, 1, 7, 0, tzinfo=JST))
        self.assertEqual(major.status, ST_CONFIRMED, "正式リリースの告知で配信済みを確認")
        self.assertTrue(any(i.kind == KIND_PRE for i in items))

    def test_release_hint_gives_early_date(self):
        info_posts = [post("h", "【原神】Ver.7.2「新章」イベントまとめ", datetime(2026, 10, 24, 22, 20, tzinfo=JST))]
        versions: dict = {}
        now = datetime(2026, 10, 25, tzinfo=JST)
        hoyoverse.collect_release_hints(versions, info_posts, now,
                                        fetch_text=lambda pid: "【原神】Ver.7.2「新章」は2026年11月4日にリリースされます")
        items = hoyoverse.build_items("GI", {"launcher_id": "x"}, None, versions, {}, now, now - timedelta(days=120))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].start, datetime(2026, 11, 4, tzinfo=JST))
        self.assertTrue(items[0].all_day)
        self.assertEqual(items[0].status, ST_SCHEDULED)

    def test_extra_update_with_download(self):
        notices = [
            post("c", "Fate[UBW]コラボアップデートのお知らせ", datetime(2026, 7, 23, 13, 0, tzinfo=JST)),
            post("d", "「誕生日プレゼント」アップデート詳細", datetime(2026, 7, 2, 13, 30, tzinfo=JST)),
            post("e", "Ver.4.6ショップ更新", datetime(2026, 9, 20, 21, 15, tzinfo=JST)),
        ]
        texts = {"c": HSR_COLLAB_TEXT, "d": ZZZ_BIRTHDAY_TEXT, "e": "ショップの商品が更新されます"}
        now = datetime(2026, 10, 3, tzinfo=JST)
        items = hoyoverse.build_extra_updates("HSR", {"launcher_id": "x"}, notices, now, now - timedelta(days=120),
                                              fetch_text=lambda pid: texts[pid])
        self.assertEqual([i.title for i in items], ["Fate[UBW]コラボアップデートのお知らせ"], "ダウンロードの記載がない告知は載せない")
        it = items[0]
        self.assertEqual(it.start, datetime(2026, 7, 21, 16, 0, tzinfo=JST))
        self.assertEqual(it.size, "約540MB（公式告知・PC）")
        self.assertEqual((it.dl, it.status), (DL_YES, ST_CONFIRMED))


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
