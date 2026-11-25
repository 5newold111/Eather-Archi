"""配信中なのに ISRC・UPC・配信の住所が空の曲（SSD の台帳から立て直した曲）を、Spotify から埋める（決定 180）"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import Project  # noqa: E402


class FillLive(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.a = self.p.weekly("hollowplan", self.p.week(-6), "After Nights", stage="registered", days_ago=20)
        self.b = self.p.weekly("tidewool", self.p.week(-5), "Kept Song", stage="registered", days_ago=18)
        self.p.set_state(self.a, status="live", released="2026-10-21", isrc="", upc="", url="")
        self.p.set_state(self.b, status="live", released="2026-10-28", isrc="QZKEEP000001", upc="", url="https://x/keep")
        self.p.reload()
        self.pub = self.p.mod("publish")
        self.calls = []

        def find(token, song):
            self.calls.append(song["title"])
            if song["title"] == "After Nights":
                return {"isrc": "QZNEW0000001", "url": "https://open.spotify.com/track/1", "released": "2026-10-20", "upc": "123456789012"}
            return None
        self.pub.find_on_spotify = find
        self.real_token = self.pub.spotify_token
        self.pub.spotify_token = lambda: "token"
        self.pub._book = lambda: None

    def tearDown(self):
        self.p.close()

    def test_fills_only_empty_fields(self):
        self.pub.cmd_track(types.SimpleNamespace(quiet=True))
        a, b = self.p.state(self.a), self.p.state(self.b)
        self.assertEqual((a["isrc"], a["upc"], a["url"]), ("QZNEW0000001", "123456789012", "https://open.spotify.com/track/1"))
        self.assertEqual(a["released"], "2026-10-21", "書いてある配信日は変えない")
        self.assertEqual(a["status"], "live")
        self.assertEqual(b["isrc"], "QZKEEP000001")
        self.assertEqual(b.get("fill_checked_at"), date.today().isoformat(), "見つからなかった曲は、確かめた日を残す")

    def test_not_found_is_retried_after_a_week(self):
        self.pub.cmd_track(types.SimpleNamespace(quiet=True))
        self.calls.clear()
        self.pub.cmd_track(types.SimpleNamespace(quiet=True))
        self.assertNotIn("Kept Song", self.calls, "1 週間は問い合わせ直さない")

    def test_without_key_does_nothing(self):
        self.pub.spotify_token = lambda: ""
        self.p.mod("store_check").check = lambda song, web_search=True: {"live": False, "spotify": {}, "apple": {}, "deezer": {}, "notes": []}   # 外には出ない
        self.assertEqual(self.pub.cmd_track(types.SimpleNamespace(quiet=True)), 0)
        self.assertEqual(self.calls, [])


    def test_spotify_refused_falls_back_to_stores(self):
        """Spotify が問い合わせを断る（403。アプリの持ち主のプランなど）：鍵の要らない確かめ方（Deezer・Apple Music）で埋める（決定 180）"""
        def refused(token, song):
            raise self.pub.SpotifyRefused(self.pub.spotify_refused_text("Active premium subscription required"))
        self.pub.find_on_spotify = refused
        sc = self.p.mod("store_check")
        seen = []

        def check(song, web_search=True):
            seen.append(web_search)
            return {"live": True, "spotify": {}, "apple": {"url": "https://music.apple.com/x"},
                    "deezer": {"isrc": "QZDZ00000001", "upc": "999", "url": "https://deezer.com/track/1"}, "notes": []}
        sc.check = check
        self.pub.cmd_track(types.SimpleNamespace(quiet=True))
        a = self.p.state(self.a)
        self.assertEqual((a["isrc"], a["upc"], a["url"]), ("QZDZ00000001", "999", "https://music.apple.com/x"))
        self.assertEqual(self.p.state(self.b)["isrc"], "QZKEEP000001", "書いてある値は変えない")
        self.assertTrue(seen and not any(seen), "料金のかかる Web 検索は使わない")
        self.assertTrue(self.pub.spotify_off(), "断られたことを覚える")
        import os
        os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"] = "x", "y"
        try:
            self.assertIsNone(self.real_token(), "覚えている間は、問い合わせない")
        finally:
            os.environ.pop("SPOTIFY_CLIENT_ID"), os.environ.pop("SPOTIFY_CLIENT_SECRET")


if __name__ == "__main__":
    unittest.main()
