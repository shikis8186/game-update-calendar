"""鉄拳8・SF6 の公式サイトの読み取りのテスト。HTML は実際のページの構造を必要な部分だけ抜き出したもの。"""
import json
import unittest
from datetime import datetime, timedelta
from unittest import mock

from collector.common import JST
from collector.model import DL_UNKNOWN, DL_YES, KIND_MAINT, KIND_MAJOR, KIND_PATCH, ST_CONFIRMED, ST_SCHEDULED
from collector.sources import sf6, tekken8

NOW = datetime(2026, 10, 3, 15, 0, tzinfo=JST)
SINCE = NOW - timedelta(days=120)

TK_HISTORY = """<div id="history" class="sectionCol"><ol>
<li>
  <a href="https://www.tekken-official.jp/tekken_news/?p=3639" target="_blank" rel="noopener">
  <div class="txt">
      <p class="ver">V3.02.02</p>
      <p class="date">配信：2026/09/10</p>
      <ul class="pf"><li>PS5®版</li><li>XSX│S版</li><li>Steam版</li></ul>
    </div>
  </a>
</li>
<li class="charaBg bob">
  <a href="https://www.tekken-official.jp/tekken_news/?p=3547" target="_blank" rel="noopener">
  <div class="txt">
      <p class="ver">V3.02.01</p>
      <p class="date">配信：2026/08/20</p>
      <ul class="pf"><li>PS5®版</li><li>XSX│S版</li><li>Steam版</li></ul>
    </div>
  </a>
</li>
<li>
  <a href="https://www.tekken-official.jp/tekken_news/?p=1000" target="_blank" rel="noopener">
  <div class="txt">
      <p class="ver">V2.09.00</p>
      <p class="date">配信：2026/02/10</p>
      <ul class="pf"><li>PS5®版</li><li>XSX│S版</li><li>Steam版</li></ul>
    </div>
  </a>
</li>
</ol></div>"""

TK_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>[TEKKEN 8] Update Data Ver.3.02.02 / アップデートデータ Ver.3.02.02</title>
<link>https://www.tekken-official.jp/tekken_news/?p=3639</link>
<pubDate>Thu, 10 Sep 2026 02:02:57 +0000</pubDate>
<content:encoded><![CDATA[<p>Update data will be distributed.</p><p>以下内容にて『鉄拳8』のアップデートデータを配信いたします。</p>
<h3>■ バージョン</h3><p>Ver.3.02.02</p><h3>■ 適用日時</h3><p>[JST] 9/10(木)11:00頃より順次適用</p>
<h3>■ メンテナンス日時</h3><p>[JST] 9/10(木)10:30~13:00</p>]]></content:encoded></item>
<item><title>[TEKKEN Series] September 2026 － Discover the Latest TEKKEN Merchandise!</title>
<link>https://www.tekken-official.jp/tekken_news/?p=3658</link><pubDate>Fri, 25 Sep 2026 07:00:00 +0000</pubDate></item>
</channel></rss>"""


class TekkenTest(unittest.TestCase):
    def test_history_and_news(self):
        with mock.patch.object(tekken8, "request_text", side_effect=[TK_HISTORY, TK_RSS]):
            history = tekken8.fetch_history()
            news = tekken8.fetch_news()
        self.assertEqual([h["ver"] for h in history], ["3.02.02", "3.02.01", "2.09.00"])
        self.assertEqual(list(news), ["3.02.02"], "グッズの記事などは読まない")
        items = {i.id: i for i in tekken8.build_items("T8", history, news, NOW, SINCE)}
        self.assertEqual(set(items), {"tk8-v3.02.02", "tk8-v3.02.01"}, "期間外の古い版は載せない")
        latest = items["tk8-v3.02.02"]
        self.assertEqual(latest.start, datetime(2026, 9, 10, 11, 0, tzinfo=JST), "ニュースの適用日時を使う")
        self.assertFalse(latest.all_day)
        self.assertEqual((latest.kind, latest.dl, latest.status), (KIND_PATCH, DL_YES, ST_CONFIRMED))
        self.assertIn("メンテナンス日時（公式告知）: 9/10(木)10:30~13:00", latest.basis)
        bob = items["tk8-v3.02.01"]
        self.assertTrue(bob.all_day, "ニュースが残っていない版は配信日だけで載せる")
        self.assertEqual(bob.kind, KIND_MAJOR, "追加キャラクターに対応する版は大型アップデート")


def sf6_page(items, max_page=1):
    data = {"props": {"pageProps": {"current_page": 1, "max_page": max_page, "info_list": items}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script></html>'


def sf6_item(slug, title, posted, body):
    return {"slug": slug, "title": title, "releaseDate": int(datetime.fromisoformat(posted).replace(tzinfo=JST).timestamp()),
            "body": body}


WINDOW = "<p>JST 2026年9月8日(火) 12:00〜16:00</p><p>PDT 2026年9月7日(月) 20:00〜24:00</p><p>UTC 2026年9月8日(火) 03:00〜07:00</p>"


class Sf6Test(unittest.TestCase):
    def test_maintenance_and_update_are_merged(self):
        page = sf6_page([
            sf6_item("update20260908", "アップデートのお知らせ", "2026-09-08T15:45",
                     "<p>下記日時のメンテナンスで、『ストリートファイター6』がアップデートされました。</p>" + WINDOW),
            sf6_item("mainte20260908", "メンテナンスのお知らせ", "2026-09-07T11:05",
                     "<p>以下の日時において、メンテナンス実施を予定しております。</p>" + WINDOW),
        ])
        with mock.patch.object(sf6, "request_text", return_value=page):
            notices = sf6.fetch_notices(SINCE)
        items = sf6.build_items("SF6", notices, NOW, SINCE)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it.start, it.end), (datetime(2026, 9, 8, 12, 0, tzinfo=JST), datetime(2026, 9, 8, 16, 0, tzinfo=JST)),
                         "PDT・UTC ではなく JST の行を使う")
        self.assertEqual((it.kind, it.dl, it.status), (KIND_MAINT, DL_UNKNOWN, ST_CONFIRMED))
        self.assertEqual(len(it.sources), 2)

    def test_upcoming_maintenance_is_scheduled(self):
        page = sf6_page([sf6_item("mainte20261013", "メンテナンスのお知らせ", "2026-10-12T11:00",
                                  "<p>JST 2026年10月13日(火) 12:00〜16:00</p>")])
        now = datetime(2026, 10, 12, 12, 0, tzinfo=JST)
        with mock.patch.object(sf6, "request_text", return_value=page):
            items = sf6.build_items("SF6", sf6.fetch_notices(now - timedelta(days=120)), now, now - timedelta(days=120))
        self.assertEqual(items[0].status, ST_SCHEDULED)


if __name__ == "__main__":
    unittest.main()
