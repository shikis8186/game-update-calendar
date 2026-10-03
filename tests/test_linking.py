"""告知と Steam のビルド更新の突き合わせ（ダウンロード有無の確定）のテスト。"""
import unittest
from datetime import datetime

from collector.common import JST
from collector.linking import build_items, cluster, link
from collector.model import (DL_LIKELY, DL_NO, DL_UNKNOWN, DL_YES, KIND_MAINT, KIND_MAJOR, KIND_NEWS,
                             KIND_PATCH, ST_ANNOUNCED, ST_CONFIRMED, Item)


def dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=JST)


def item(id_, kind, start, end=None, dl=None, title=None, etype=None):
    return Item(id=id_, game="G", kind=kind, title=title or id_, start=dt(start),
                end=dt(end) if end else None, dl=dl or (DL_LIKELY if kind in (KIND_MAJOR, KIND_PATCH) else DL_UNKNOWN),
                status=ST_ANNOUNCED, basis=["元の告知"], posted=dt(start), steam_event_type=etype)


def builds(*times):
    return build_items("G", 1, [{"buildid": 100 + n, "timeupdated": int(dt(t).timestamp())} for n, t in enumerate(times)])


class LinkTest(unittest.TestCase):
    def test_patch_notes_and_maintenance_merge_into_build(self):
        notes = item("notes", KIND_PATCH, "2026-10-01T14:00", title="[10/01] パッチノート")
        maint = item("maint", KIND_MAINT, "2026-10-01T14:00", "2026-10-01T17:30")
        leftover = link([notes, maint], builds("2026-10-01T14:03"), dt("2026-10-01T14:03"), dt("2026-10-02T20:00"))
        self.assertEqual(leftover, [])
        self.assertEqual((notes.dl, notes.status), (DL_YES, ST_CONFIRMED))
        self.assertTrue(maint.merged)
        self.assertEqual((notes.start, notes.end), (dt("2026-10-01T14:00"), dt("2026-10-01T17:30")))

    def test_build_without_announcement_is_kept(self):
        leftover = link([], builds("2026-09-10T11:00"), dt("2026-09-10T11:00"), dt("2026-10-02T20:00"))
        self.assertEqual(len(leftover), 1)
        self.assertEqual(leftover[0].title, "クライアント更新")

    def test_no_build_in_covered_period_means_no_download(self):
        news = item("outlands", KIND_MAJOR, "2026-09-23T12:00", title="新たな挑戦者がアウトランズに参戦！")
        link([news], builds("2026-08-03T12:00"), dt("2026-08-03T12:00"), dt("2026-10-02T20:00"))
        self.assertEqual(news.dl, DL_NO)
        self.assertEqual(news.kind, KIND_NEWS)

    def test_before_coverage_stays_unconfirmed(self):
        patch = item("old", KIND_PATCH, "2026-07-01T10:00")
        link([patch], builds("2026-09-10T11:00"), dt("2026-09-10T11:00"), dt("2026-10-02T20:00"))
        self.assertEqual(patch.dl, DL_LIKELY)

    def test_recent_items_are_not_judged_yet(self):
        patch = item("recent", KIND_PATCH, "2026-10-02T10:00")
        link([patch], builds("2026-09-10T11:00"), dt("2026-09-10T11:00"), dt("2026-10-02T20:00"))
        self.assertEqual(patch.dl, DL_LIKELY, "24時間たつまでは「なし」と決めない")

    def test_failed_check_limits_coverage(self):
        patch = item("p", KIND_PATCH, "2026-09-25T10:00")
        link([patch], builds("2026-09-10T11:00"), dt("2026-09-10T11:00"), dt("2026-09-20T00:00"))
        self.assertEqual(patch.dl, DL_LIKELY, "最後に確認できた時刻より後は判断しない")

    def test_all_day_scheduled_item_matches_build_on_same_day(self):
        sched = item("char", KIND_MAJOR, "2026-08-03T00:00")
        sched.all_day = True
        link([sched], builds("2026-08-03T12:00"), dt("2026-08-03T12:00"), dt("2026-10-02T20:00"))
        self.assertEqual(sched.dl, DL_YES)
        self.assertEqual(sched.start, dt("2026-08-03T12:00"))

    def test_all_day_item_matches_build_on_previous_night(self):
        season = item("season5", KIND_MAJOR, "2026-10-07T00:00", title="シーズン5開幕")
        season.all_day = True
        link([season], builds("2026-10-06T22:30"), dt("2026-10-02T08:00"), dt("2026-10-08T20:00"))
        self.assertEqual(season.dl, DL_YES, "開幕前夜のビルド更新も同じ更新とみなす")

    def test_all_day_item_without_build_is_judged_after_margin(self):
        hotfix = item("hotfix", KIND_PATCH, "2026-10-09T00:00", title="[オーバーウォッチ] 2026年10月9日配信パッチ")
        hotfix.all_day = True
        link([hotfix], builds("2026-10-02T08:00"), dt("2026-10-02T08:00"), dt("2026-10-10T06:00"))
        self.assertEqual(hotfix.dl, DL_LIKELY, "翌日の正午までは判断しない")
        link([hotfix], builds("2026-10-02T08:00"), dt("2026-10-02T08:00"), dt("2026-10-10T12:30"))
        self.assertEqual(hotfix.dl, DL_NO, "サーバー側だけの修正（ビルド更新なし）はダウンロードなし")

    def test_completed_maintenance_without_build_has_no_download(self):
        sf6 = item("sf6", KIND_MAINT, "2026-09-08T12:00", "2026-09-08T16:00")
        sf6.status = ST_CONFIRMED  # 公式告知で実施済みを確認
        link([sf6], builds("2026-08-03T12:00"), dt("2026-08-03T12:00"), dt("2026-10-03T15:00"))
        self.assertEqual(sf6.dl, DL_NO, "実施済みでも、Steam の本体更新がなければダウンロードなし")
        self.assertEqual(sf6.kind, KIND_MAINT)

    def test_in_game_download_games_are_not_judged_no(self):
        maint = item("svwb", KIND_MAINT, "2026-09-29T14:00", "2026-09-29T17:00")
        link([maint], builds("2026-08-01T12:00"), dt("2026-08-01T12:00"), dt("2026-10-02T20:00"), judge_no_download=False)
        self.assertEqual(maint.dl, DL_UNKNOWN, "ゲーム内でデータをダウンロードするゲームは、ビルド更新がなくても「なし」にしない")

    def test_japanese_official_title_is_preferred(self):
        steam = item("steam", KIND_MAJOR, "2026-08-12T02:55", title="Overwatch Season 4 Now Live!", etype=14)
        notes = item("notes", KIND_MAJOR, "2026-08-12T00:00", title="[オーバーウォッチ] 2026年8月12日配信パッチ内容のおしらせ")
        notes.all_day = True
        cluster([steam, notes])
        self.assertTrue(steam.merged)
        self.assertFalse(notes.merged)


class ClusterTest(unittest.TestCase):
    def test_same_day_announcements_merge(self):
        notes = item("notes", KIND_PATCH, "2026-08-06T16:43", title="[08/06] パッチノート")
        maint = item("maint", KIND_MAINT, "2026-08-06T14:00", "2026-08-06T16:35")
        cluster([notes, maint])
        self.assertTrue(maint.merged)
        self.assertFalse(notes.merged)
        self.assertEqual(notes.start, dt("2026-08-06T14:00"))

    def test_far_maintenance_window_is_not_adopted(self):
        upd = item("upd", KIND_PATCH, "2026-07-09T21:31", title="Client Update: July 09 (UTC 12:30)")
        maint = item("m", KIND_MAINT, "2026-07-09T17:30", "2026-07-09T17:40")
        cluster([upd, maint])
        self.assertEqual(upd.start, dt("2026-07-09T21:31"))

    def test_next_day_maintenance_is_separate(self):
        a = item("a", KIND_PATCH, "2026-07-09T21:31")
        b = item("b", KIND_MAINT, "2026-07-10T13:20", "2026-07-10T13:33")
        cluster([a, b])
        self.assertFalse(a.merged or b.merged)

    def test_patch_titled_announcement_is_primary(self):
        ti = item("ti", KIND_PATCH, "2026-07-31T08:57", title="The International：予想、ファンタジー", etype=13)
        patch = item("p", KIND_PATCH, "2026-07-31T08:58", title="ゲームプレイパッチ7.41eとサマースクラブ", etype=13)
        cluster([ti, patch])
        self.assertTrue(ti.merged)
        self.assertFalse(patch.merged)


if __name__ == "__main__":
    unittest.main()
