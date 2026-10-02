"""Steam のお知らせの分類と、SteamCMD の出力の読み取りのテスト。"""
import unittest
from datetime import datetime, timedelta

from collector.common import JST
from collector.model import KIND_EVENT, KIND_MAINT, KIND_MAJOR, KIND_NEWS, KIND_PATCH, ST_SCHEDULED
from collector.sources import steam_builds, steam_news
from collector.sources.noise import is_noise

NOW = datetime(2026, 10, 2, 20, 0, tzinfo=JST)
SINCE = NOW - timedelta(days=120)


def event(gid, title, etype=28, post="2026-09-29T16:00", start=None, end=None):
    pt = int(datetime.fromisoformat(post).replace(tzinfo=JST).timestamp())
    st = int(datetime.fromisoformat(start).replace(tzinfo=JST).timestamp()) if start else pt
    return {
        "gid": gid, "event_name": title, "event_type": etype,
        "rtime32_start_time": st,
        "rtime32_end_time": int(datetime.fromisoformat(end).replace(tzinfo=JST).timestamp()) if end else None,
        "announcement_body": {"headline": title, "posttime": pt, "body": "本文"},
    }


class ClassifyTest(unittest.TestCase):
    def test_real_titles(self):
        cases = [
            ("[10/01] パッチノート", 28, KIND_PATCH),
            ("[Completed] 10/01 定期メンテナンスのお知らせ (JST 14:00 - 17:30)", 28, KIND_MAINT),
            ("[Updated] Client Update: July 09 (UTC 12:30)", 28, KIND_PATCH),
            ("Black Screen Issue — Update & Request", 28, KIND_NEWS),
            ("シーズン3 第2弾追加キャラクター「ボブ」ゲームプレイトレーラー公開！", 28, KIND_NEWS),
            ("Year 4追加キャラクター「ヤスミン」参戦開始！", 14, KIND_MAJOR),
            ("バトルパスシーズン16開催！", 28, KIND_EVENT),
            ("Everything New in Overwatch Season 4: Heroes of Busan", 28, KIND_NEWS),
            ("7.41f Gameplay Patch", 12, KIND_PATCH),
            ("Anima Strike Meta Event", 35, KIND_EVENT),
            ("Revenants of Azvaldt / アズヴォルト・レヴナント カードパック第9弾発売", 28, KIND_MAJOR),
            ("現在確認されている不具合・問題について（2026年9月10日更新）", 28, KIND_NEWS),
            ("Dev Note #15", 28, KIND_NEWS),
        ]
        for title, etype, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(steam_news.classify(title, etype), expected)

    def test_noise(self):
        self.assertTrue(is_noise("[10/01] 不正プログラム利用者に対する処分のお知らせ"))
        self.assertTrue(is_noise("[Updated] Announcement on Disciplinary Actions"))
        self.assertTrue(is_noise("不正行為の制裁リストに関するお知らせ 2026.9.23"))
        self.assertFalse(is_noise("[10/01] パッチノート"))


class ToItemsTest(unittest.TestCase):
    def test_title_date_becomes_scheduled_item(self):
        items = steam_news.to_items("SF6", 1364780, [
            event("1", "Year 4追加キャラクター第2弾「アルジュン」が2026年10月13日に参戦！", post="2026-09-08T10:53"),
            event("2", "アルジュンが自身の“シネマチックな信念”を携えて『ストリートファイター6』に10月13日に参戦！", post="2026-09-08T08:16"),
        ], NOW, SINCE)
        self.assertEqual(len(items), 1, "同じ参戦日の告知2本は1件にまとまる")
        it = items[0]
        self.assertEqual(it.start.date().isoformat(), "2026-10-13")
        self.assertTrue(it.all_day)
        self.assertEqual(it.status, ST_SCHEDULED)
        self.assertEqual(len(it.sources), 2)

    def test_until_date_is_not_used(self):
        items = steam_news.to_items("SF6", 1364780, [
            event("3", "ジュリのaespaコラボコスチュームの販売は2026年7月4日まで", post="2026-09-24T12:46"),
        ], NOW, SINCE)
        self.assertEqual(items[0].start.date().isoformat(), "2026-09-24")

    def test_update_type_uses_event_start_time(self):
        items = steam_news.to_items("ZZZ", 4162040, [
            event("4", "Ver.3.1「ロング・グッドバイ」アップデート詳細", etype=14,
                  post="2026-07-28T22:26", start="2026-07-29T08:00"),
        ], NOW, SINCE)
        self.assertEqual(items[0].start, datetime(2026, 7, 29, 8, 0, tzinfo=JST))

    def test_noise_is_skipped(self):
        items = steam_news.to_items("SAZP", 3576070, [
            event("5", "[10/01] 不正プログラム利用者に対する処分のお知らせ", post="2026-10-01T14:00"),
        ], NOW, SINCE)
        self.assertEqual(items, [])


STEAMCMD_OUTPUT = '''Redirecting stderr to '/home/runner/Steam/logs/stderr.txt'
Loading Steam API...OK
Connecting anonymously to Steam Public...OK
Waiting for client config...OK
Waiting for user info...OK
AppID : 570, change number : 31234567/0, last change : Thu Oct  2 08:20:01 2026
"570"
{
	"common"
	{
		"name"		"Dota 2"
		"type"		"Game"
		"oslist"		"windows,macos,linux"
	}
	"depots"
	{
		"branches"
		{
			"public"
			{
				"buildid"		"25664722"
				"timeupdated"		"1790896687"
			}
			"beta"
			{
				"buildid"		"25670000"
				"description"		"Beta {test} \\"branch\\""
				"pwdrequired"		"1"
				"timeupdated"		"1790900000"
			}
		}
	}
}
AppID : 1778820, change number : 31230000/0, last change : Wed Sep 10 02:00:02 2026
"1778820"
{
	"common"
	{
		"name"		"TEKKEN 8"
	}
	"depots"
	{
		"branches"
		{
			"public"
			{
				"buildid"		"25221600"
				"timeupdated"		"1789005602"
			}
		}
	}
}
'''


class SteamCmdParseTest(unittest.TestCase):
    def test_extract_public_branch(self):
        blocks = steam_builds.extract_app_blocks(STEAMCMD_OUTPUT, [570, 1778820, 999])
        self.assertEqual(set(blocks), {570, 1778820})
        self.assertEqual(steam_builds._public_branch(blocks[570]), {"buildid": 25664722, "timeupdated": 1790896687})
        self.assertEqual(steam_builds._public_branch(blocks[1778820])["buildid"], 25221600)

    def test_rejects_invalid_values(self):
        self.assertIsNone(steam_builds._public_branch({"depots": {"branches": {"public": {"buildid": "x"}}}}))
        self.assertIsNone(steam_builds._public_branch({"depots": {"branches": {"public": {"buildid": "1", "timeupdated": "100"}}}}))


if __name__ == "__main__":
    unittest.main()
