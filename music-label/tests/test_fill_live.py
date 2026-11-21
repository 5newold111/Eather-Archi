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
        self.assertEqual(self.pub.cmd_track(types.SimpleNamespace(quiet=True)), 0)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
