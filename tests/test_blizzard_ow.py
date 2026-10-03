"""オーバーウォッチ公式サイト（パッチノート・ニュース）の読み取りのテスト。
HTML は実際のページの構造を、必要な部分だけ抜き出したもの。"""
import unittest
from datetime import datetime

from collector.common import JST
from collector.model import KIND_MAJOR, KIND_PATCH, ST_ANNOUNCED, ST_SCHEDULED
from collector.sources import blizzard_ow as b

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=JST)

PATCH_PAGE = """<div class="PatchNotes-list"><div class="PatchNotes-body">
<div class="PatchNotes-patch PatchNotes-live"><div class="anchor" id="patch-2026-09-22"></div><div class="PatchNotes-labels"><div class="PatchNotes-date">2026年9月22日</div></div><h3 class="PatchNotes-patchTitle">[オーバーウォッチ] 2026年9月23日配信パッチ内容のおしらせ</h3><div class="PatchNotes-section PatchNotes-section-generic_update"><h4 class="PatchNotes-sectionTitle">不具合の修正のおしらせ</h4><div class="PatchNotes-sectionDescription"><p>こちらは不具合の修正を目的としたホットフィックスのアップデートです。</p></div></div></div>
<div class="PatchNotes-patch PatchNotes-live"><div class="anchor" id="patch-2026-08-11"></div><div class="PatchNotes-labels"><div class="PatchNotes-date">2026年8月11日</div></div><h3 class="PatchNotes-patchTitle">[オーバーウォッチ ] 2026年8月12日配信パッチ内容のおしらせ</h3><div class="PatchNotes-section"><h4 class="PatchNotes-sectionTitle">帝国の覇者 - シーズン4「釜山の英雄たち」</h4></div></div>
<div class="PatchNotes-patch PatchNotes-live"><div class="anchor" id="patch-2026-08-04"></div><div class="PatchNotes-labels"><div class="PatchNotes-date">2026年8月4日</div></div><h3 class="PatchNotes-patchTitle">コンソール向け非許諾周辺機器に関するお知らせ</h3></div>
</div></div><div class="PatchNotesTop"></div>"""

NEWS_PAGE = """<blz-news><a slot="gallery-items" href="/news/24294376/blizzcon" target="_blank"><blz-card><blz-content-block><h3 slot="heading">BlizzCon - 初公開の新情報と今後のプログラムをおさらい！</h3></blz-content-block><blz-timestamp slot="footer" timestamp="2026-09-12T18:10:00.000Z" lang="ja-JP"></blz-timestamp></blz-card></a>
<a slot="gallery-items" href="/news/24295381/4" target="_blank"><blz-card><h3 slot="heading">帝国の覇者 - シーズン4「釜山の英雄たち」が開幕！</h3><blz-timestamp slot="footer" timestamp="2026-08-10T17:05:00.000Z" lang="ja-JP"></blz-timestamp></blz-card></a></blz-news>"""

ARTICLE = """<div class="article-content"><p>クーポンの配信と引き換えは、9月13日のオープニングセレモニー終了後から10月6日までです</p>
<p>今週末は、見逃せないイベントが目白押しです！10月7日開幕のシーズン5と来年の「オーバーウォッチ：スポットライト 2027」にもどうぞご期待ください！</p></div>
<div class="article-sidebar">最近の記事 シーズン9は1月1日に開幕</div>"""


class PatchNotesTest(unittest.TestCase):
    def test_parse(self):
        items = b.parse_patch_page(PATCH_PAGE, "https://example.invalid/09/", NOW)
        self.assertEqual([i.id for i in items], ["ow-patch-2026-09-22", "ow-patch-2026-08-11"], "パッチ以外のお知らせは除く")
        hotfix, season = items
        self.assertEqual(hotfix.start, datetime(2026, 9, 23, tzinfo=JST), "掲載日ではなくタイトルの配信日を使う")
        self.assertTrue(hotfix.all_day)
        self.assertEqual(hotfix.kind, KIND_PATCH)
        self.assertEqual(hotfix.status, ST_ANNOUNCED)
        self.assertEqual(season.kind, KIND_MAJOR, "新シーズンを含むパッチは大型アップデート")
        self.assertTrue(hotfix.sources[0]["url"].endswith("#patch-2026-09-22"))


class SeasonTest(unittest.TestCase):
    def test_season_opening_from_article(self):
        pages = {b.NEWS_URL: NEWS_PAGE, "https://overwatch.blizzard.com/ja-jp/news/24294376/blizzcon": ARTICLE}
        items = b.fetch_season_items(NOW, get=lambda url: pages.get(url, '<div class="article-content"></div><div class="article-sidebar">'))
        self.assertEqual(len(items), 1, "サイドバーの文や「〇日まで」は読まない")
        it = items[0]
        self.assertEqual((it.id, it.title), ("ow-season5", "シーズン5開幕"))
        self.assertEqual(it.start, datetime(2026, 10, 7, tzinfo=JST))
        self.assertEqual(it.status, ST_SCHEDULED)
        self.assertIn("10月7日開幕のシーズン5", it.basis[0])

    def test_patterns(self):
        posted = datetime(2026, 11, 20, tzinfo=JST)
        cases = {
            "シーズン6は12月8日に開幕します。": (6, 12, 8),
            "シーズン6「新章」が12月8日(火)より開始！": (6, 12, 8),
            "12月8日より開幕するシーズン6をお楽しみに": (6, 12, 8),
        }
        for text, (n, m, d) in cases.items():
            with self.subTest(text=text):
                got = b.find_season_openings(text, posted)
                self.assertEqual([(x[0], x[1].month, x[1].day) for x in got], [(n, m, d)])
        self.assertEqual(b.find_season_openings("シーズン5は10月6日まで、シーズン6で正式デビュー", posted), [])

    def test_empty_list_is_error(self):
        from collector.common import FetchError
        with self.assertRaises(FetchError):
            b.fetch_season_items(NOW, get=lambda url: "<html>メンテナンス中</html>")


if __name__ == "__main__":
    unittest.main()
