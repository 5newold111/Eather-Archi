"""
再生数を集める部品（scripts/plays.py・決定 172）の試験

  python3 -m unittest discover -s tests -v      （music-label/ で）

本物の out/ や .env には触らない：記録は一時フォルダ、窓口（Spotify・YouTube）は手元の偽サーバー（19200 番台）。
Spotify for Artists の画面は本物を見られないので、画面の文字の読み取り（parse_s4a_text）を、いくつかの並び方で確かめる。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
sys.path.insert(0, SCRIPTS)
import _ui  # noqa: E402,F401  あとで使う部品も先に読んでおく（ほかの試験が読み込みの道を変えても動くように）
import collect_metrics  # noqa: E402
import plays  # noqa: E402

SONGS = [
    {"id": "PDR-002", "key": "2026-10-11_hollowplan", "slug": "hollowplan", "artist": "HOLLOWPLAN", "title": "Services at Three",
     "release_date": "2026-10-28", "url": "https://open.spotify.com/track/1111111111111111111111", "track_id": "1111111111111111111111", "isrc": "QZAAA2600001"},
    {"id": "2026-11-02_hollowplan", "key": "2026-11-02_hollowplan", "slug": "hollowplan", "artist": "HOLLOWPLAN", "title": "After Nights",
     "release_date": "2026-11-11", "url": "", "track_id": "", "isrc": "QZAAA2600002"},
    {"id": "PDR-005", "key": "2026-10-11_tidewool", "slug": "tidewool", "artist": "TIDEWOOL", "title": "The Low Part",
     "release_date": "2026-10-30", "url": "", "track_id": "3333333333333333333333", "isrc": ""},
]
ARTS = {"hollowplan": {"slug": "hollowplan", "name": "HOLLOWPLAN", "spotify_id": "A" * 22, "youtube": "https://www.youtube.com/channel/UC" + "h" * 22},
        "tidewool": {"slug": "tidewool", "name": "TIDEWOOL", "spotify_id": "", "youtube": ""}}
CALLS: list[str] = []


class Mock(BaseHTTPRequestHandler):
    """Spotify と YouTube の窓口の代わり"""
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        CALLS.append("POST " + self.path)
        if self.path == "/accounts/api/token":
            return self._json(200, {"access_token": "sp-token"})
        if self.path == "/google/token":
            return self._json(200, {"access_token": "yt-token"})
        self._json(404, {})

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        CALLS.append("GET " + u.path + ("?" + u.query if u.query else ""))
        if u.path.startswith("/v1/"):
            if self.headers.get("Authorization") != "Bearer sp-token":
                return self._json(401, {})
            if u.path == "/v1/tracks/1111111111111111111111":
                return self._json(200, {"id": "1" * 22, "popularity": 12})
            if u.path == "/v1/tracks/2222222222222222222222":
                return self._json(200, {"id": "2" * 22, "popularity": 3})
            if u.path == "/v1/tracks/3333333333333333333333":
                return self._json(200, {"id": "3" * 22})                # 人気度を返さない場合
            if u.path == "/v1/search" and q.get("q") == "isrc:QZAAA2600002":
                return self._json(200, {"tracks": {"items": [{"id": "2" * 22}]}})
            if u.path == "/v1/artists/" + "A" * 22:
                return self._json(200, {"followers": {"total": 41}, "popularity": 5})
            return self._json(404, {})
        if u.path.startswith("/yt/"):
            if q.get("key") != "yt-key" and self.headers.get("Authorization") != "Bearer yt-token":
                return self._json(400, {"error": {"errors": [{"reason": "keyInvalid"}]}})
            if u.path == "/yt/search" and q.get("channelId"):
                return self._json(200, {"items": [
                    {"id": {"videoId": "vidA"}, "snippet": {"title": "Services At Three", "channelTitle": "HOLLOWPLAN - Topic"}},
                    {"id": {"videoId": "vidX"}, "snippet": {"title": "Something Else", "channelTitle": "HOLLOWPLAN - Topic"}}]})
            if u.path == "/yt/search" and "After Nights" in q.get("q", ""):
                return self._json(200, {"items": [
                    {"id": {"videoId": "vidZ"}, "snippet": {"title": "After Nights", "channelTitle": "Somebody Else"}},
                    {"id": {"videoId": "vidB"}, "snippet": {"title": "After Nights", "channelTitle": "HOLLOWPLAN - Topic"}}]})
            if u.path == "/yt/search":
                return self._json(200, {"items": []})
            if u.path == "/yt/videos":
                stats = {"vidA": {"viewCount": "250", "likeCount": "7"}, "vidB": {"viewCount": "40"}}
                return self._json(200, {"items": [{"id": v, "statistics": stats[v]} for v in q.get("id", "").split(",") if v in stats]})
        self._json(404, {})


def start_server():
    for port in range(19200, 19300):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Mock)
        except OSError:
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, port
    raise RuntimeError("19200 番台に空きがありません")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = start_server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.inbox = root / "Downloads"
        self.inbox.mkdir()
        base = f"http://127.0.0.1:{self.port}"
        if SCRIPTS not in sys.path:
            sys.path.insert(0, SCRIPTS)
        # 成績の部品は、この試験が持っているもの（記録先を一時フォルダに向けたもの）を必ず使わせる
        ps = [mock.patch.object(plays, "DIR", root / "plays"), mock.patch.object(collect_metrics, "M", root / "metrics"),
              mock.patch.object(plays, "_metrics", lambda: collect_metrics),
              mock.patch.object(plays, "released_songs", lambda: [dict(s) for s in SONGS]),
              mock.patch.object(plays, "artists", lambda slugs: {k: dict(v) for k, v in ARTS.items() if k in slugs}),
              mock.patch.object(plays, "load_dotenv", lambda *a, **k: None),
              mock.patch.object(plays, "_dashboard_busy", lambda: False),
              mock.patch.object(plays, "step", lambda *a: None), mock.patch("builtins.print", lambda *a, **k: None),
              mock.patch.dict(os.environ, {"PLAYS_SPOTIFY_API": base + "/v1", "PLAYS_SPOTIFY_ACCOUNTS": base + "/accounts",
                                           "PLAYS_YOUTUBE_API": base + "/yt", "PLAYS_GOOGLE_TOKEN": base + "/google/token",
                                           "PLAYS_INBOX": str(self.inbox), "SPOTIFY_CLIENT_ID": "id", "SPOTIFY_CLIENT_SECRET": "sec",
                                           "YOUTUBE_API_KEY": "yt-key"})]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"):
            os.environ.pop(k, None)
        self.addCleanup(self.tmp.cleanup)
        self.day = date.today().isoformat()


class ParseNumbers(unittest.TestCase):
    def test_counts(self):
        for tok, want in [("1,234", 1234), ("12", 12), ("0", 0), ("1.2K", 1200), ("3.4m", 3400000), ("12万", 120000), ("1.5万", 15000),
                          ("2億", 200000000), ("1 234", 1234), ("1.234", 1234), ("１，２３４", 1234), ("1,234,567", 1234567), ("15+", 15)]:
            self.assertEqual(plays.parse_count(tok), want, tok)
        for tok in ["—", "-", "", "<1,000", "12%", "3:45", "abc", "+12%", "2026/10/28"]:
            self.assertIsNone(plays.parse_count(tok), tok)

    def test_period(self):
        self.assertEqual(plays.detect_period("Songs\nSince 2015\nTitle"), "all")
        self.assertEqual(plays.detect_period("楽曲\n過去28日間\n曲名"), "28d")
        self.assertEqual(plays.detect_period("Last 7 days"), "7d")
        self.assertEqual(plays.detect_period("リリース日 2026年10月28日\n11月7日"), "unknown")          # 日付を期間と取り違えない
        both = "24 hours\n7 days\n28 days\n12 months\nSince 2015"
        self.assertEqual(plays.detect_period(both), "unknown")                                        # 全部出ているだけでは決めない
        self.assertEqual(plays.detect_period(both, "Since 2015"), "all")                              # 選ばれている印があれば、それ


class ParseS4A(unittest.TestCase):
    TITLES = ["Services at Three", "After Nights", "Night", "Night Drive"]

    def test_tab_table_with_header(self):
        text = ("Music\nSongs\tReleases\n#\tTitle\tStreams\tListeners\tViews\tSaves\tFirst released\n"
                "1\tServices at Three\t1,234\t567\t89\t12\tOct 28, 2026\n2\tAfter Nights\t45\t30\t—\t2\tNov 11, 2026\n")
        got = plays.parse_s4a_text(text, self.TITLES)
        self.assertEqual(got["Services at Three"], {"streams": 1234, "listeners": 567, "views": 89, "saves": 12, "header": True})
        self.assertEqual(got["After Nights"], {"streams": 45, "listeners": 30, "saves": 2, "header": True})   # 「—」の列は飛ばさずに数える
        self.assertNotIn("Night", got)

    def test_one_cell_per_line_japanese(self):
        text = "\n".join(["楽曲", "過去28日間", "#", "タイトル", "ストリーム数", "リスナー数", "保存数", "リリース日",
                          "1", "Services at Three", "HOLLOWPLAN", "E", "1.2万", "8,001", "310", "2026年10月28日",
                          "2", "After Nights", "HOLLOWPLAN", "950", "600", "25", "2026年11月11日", "フッター", "12"])
        got = plays.parse_s4a_text(text, self.TITLES)
        self.assertEqual(got["Services at Three"], {"streams": 12000, "listeners": 8001, "saves": 310, "header": True})
        self.assertEqual(got["After Nights"], {"streams": 950, "listeners": 600, "saves": 25, "header": True})

    def test_no_header_uses_default_order(self):
        text = "Top songs\nServices at Three\n3:12\n2.5K\n1.1K\n40\nAfter Nights\n120\n80\n3\n"
        got = plays.parse_s4a_text(text, self.TITLES)
        self.assertEqual(got["Services at Three"], {"streams": 2500, "listeners": 1100, "saves": 40, "header": False})
        self.assertFalse(got["After Nights"]["header"])

    def test_title_twice_picks_row_with_numbers(self):
        text = "Your top song\nServices at Three\nKeep it up!\nNice\nGreat\nMore words\n#\tTitle\tStreams\tListeners\tSaves\n1\tServices at Three\t77\t50\t4\n"
        self.assertEqual(plays.parse_s4a_text(text, self.TITLES)["Services at Three"]["streams"], 77)

    def test_similar_titles_do_not_mix(self):
        text = "#\tTitle\tStreams\tListeners\tSaves\n1\tNight Drive\t900\t800\t70\n2\tNight\t5\t4\t1\n"
        got = plays.parse_s4a_text(text, self.TITLES)
        self.assertEqual((got["Night Drive"]["streams"], got["Night"]["streams"]), (900, 5))

    def test_next_title_stops_collecting(self):
        text = "Services at Three\n10\nAfter Nights\n20\n30\n"
        got = plays.parse_s4a_text(text, self.TITLES)
        self.assertEqual(got["Services at Three"], {"streams": 10, "header": False})
        self.assertEqual(got["After Nights"]["listeners"], 30)

    def test_not_found_and_empty(self):
        self.assertEqual(plays.parse_s4a_text("Log in to Spotify\nEmail\nPassword", self.TITLES), {})
        self.assertEqual(plays.parse_s4a_text("", self.TITLES), {})

    def test_redact_dump_keeps_only_numbers_and_titles(self):
        text = "someone@example.com\nTaro Yamada\n#\tTitle\tStreams\n1\tServices at Three\t1,234\nSince 2015\nPrivate note 42 items"
        out = plays.redact_dump(text, self.TITLES)
        for bad in ("example.com", "Taro", "Private"):
            self.assertNotIn(bad, out)
        for good in ("Services at Three", "1,234", "Streams", "Since 2015"):
            self.assertIn(good, out)


class Api(Base):
    def test_spotify_popularity_followers_and_isrc_search(self):
        d = plays.load()
        r = plays.collect_spotify(d, plays.released_songs(), plays.artists({"hollowplan", "tidewool"}), self.day)
        self.assertEqual(r["state"], "ok")
        songs = d["days"][self.day]["songs"]
        self.assertEqual(songs["PDR-002"]["spotify_popularity"], 12)
        self.assertEqual(songs["2026-11-02_hollowplan"]["spotify_popularity"], 3)            # ISRC で探して見つけた曲
        self.assertEqual(d["cache"]["track_ids"]["2026-11-02_hollowplan"], "2" * 22)
        self.assertNotIn("spotify_popularity", songs.get("PDR-005", {}))                     # 返ってこない数字は 0 にしない
        self.assertEqual(d["days"][self.day]["artists"]["hollowplan"], {"followers": 41, "popularity": 5, "source": ["api"]})
        self.assertFalse(any("ids=" in c for c in CALLS))                                    # まとめて聞く窓口は使わない

    def test_spotify_without_key(self):
        with mock.patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": ""}):
            r = plays.collect_spotify(plays.load(), SONGS, ARTS, self.day)
        self.assertEqual(r["state"], "need")
        self.assertEqual(CALLS, [])

    def test_youtube_channel_match_fallback_and_cache(self):
        d = plays.load()
        r = plays.collect_youtube(d, plays.released_songs(), ARTS, self.day)
        self.assertEqual((r["state"], r["n"]), ("ok", 2))
        songs = d["days"][self.day]["songs"]
        self.assertEqual(songs["PDR-002"]["youtube_views"], 250)                              # チャンネルの動画から（大文字小文字の違いは同じとみなす）
        self.assertEqual(songs["2026-11-02_hollowplan"]["youtube_views"], 40)                 # 曲名で探し、Topic チャンネルのものだけ採用
        self.assertEqual(d["cache"]["yt_ids"], {"PDR-002": "vidA", "2026-11-02_hollowplan": "vidB"})
        n_search = sum("/yt/search" in c for c in CALLS)
        CALLS.clear()
        plays.collect_youtube(d, plays.released_songs(), ARTS, self.day)                      # 2 回目：探し直さない（回数の上限を守る）
        self.assertEqual(sum("/yt/search" in c for c in CALLS), 0)
        self.assertEqual(sum("/yt/videos" in c for c in CALLS), 1)
        self.assertGreaterEqual(n_search, 2)

    def test_youtube_with_oauth_and_bad_key(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": "", "YOUTUBE_CLIENT_ID": "c", "YOUTUBE_CLIENT_SECRET": "s", "YOUTUBE_REFRESH_TOKEN": "r"}):
            r = plays.collect_youtube(plays.load(), plays.released_songs(), ARTS, self.day)
            self.assertEqual(r["state"], "ok")
            self.assertIn("POST /google/token", CALLS)
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": "wrong"}):
            r = plays.collect_youtube(plays.load(), plays.released_songs(), ARTS, self.day)
            self.assertEqual(r["state"], "error")
            self.assertIn("YOUTUBE_API_KEY", r["msg"])
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}):
            self.assertEqual(plays.collect_youtube(plays.load(), SONGS, ARTS, self.day)["state"], "need")


class S4A(Base):
    PAGE = ("Since 2015\n#\tTitle\tStreams\tListeners\tSaves\tFirst released\n1\tServices at Three\t1,000\t600\t30\tOct 28, 2026\n"
            "2\tAfter Nights\t200\t150\t9\tNov 11, 2026\nlogged in as owner@example.com\n")

    def raw(self, text, selected=""):
        return lambda targets, allow_open: {"state": "ok", "pages": {t["slug"]: {"text": text, "rows": "", "selected": selected} for t in targets}}

    def test_values_period_and_diag(self):
        d = plays.load()
        r = plays.collect_s4a(d, plays.released_songs(), ARTS, self.day, raw=self.raw(self.PAGE))
        self.assertEqual((r["state"], r["n"]), ("ok", 2))
        s = d["days"][self.day]["songs"]["PDR-002"]
        self.assertEqual((s["spotify_streams"], s["spotify_listeners"], s["spotify_saves"], s["spotify_period"]), (1000, 600, 30, "all"))
        diag = (plays.DIR / "diag" / f"{self.day}_hollowplan.txt").read_text(encoding="utf-8")
        self.assertIn("Services at Three", diag)
        self.assertNotIn("example.com", diag)

    def test_only_artists_with_spotify_id_are_opened(self):
        seen = []
        plays.collect_s4a(plays.load(), plays.released_songs(), ARTS, self.day,
                          raw=lambda targets, allow_open: seen.extend(targets) or {"state": "ok", "pages": {}})
        self.assertEqual([t["slug"] for t in seen], ["hollowplan"])
        self.assertEqual(seen[0]["titles"], ["Services at Three", "After Nights"])

    def test_need_login_and_not_found(self):
        r = plays.collect_s4a(plays.load(), plays.released_songs(), ARTS, self.day,
                              raw=lambda t, o: {"state": "need_login", "msg": "ログインしていません"})
        self.assertEqual(r["state"], "need_login")
        r = plays.collect_s4a(plays.load(), plays.released_songs(), ARTS, self.day, raw=self.raw("You do not have access"))
        self.assertEqual(r["state"], "error")
        self.assertIn("曲が見つかりません", r["msg"])

    def test_period_not_cumulative_gives_no_delta(self):
        d = plays.load()
        old = (date.today() - timedelta(days=7)).isoformat()
        plays.put(d, old, "songs", "PDR-002", {"spotify_streams": 500, "spotify_period": "28d"}, "s4a")
        plays.collect_s4a(d, plays.released_songs(), ARTS, self.day, raw=self.raw(self.PAGE.replace("Since 2015", "Last 28 days")))
        self.assertEqual(d["days"][self.day]["songs"]["PDR-002"]["spotify_period"], "28d")
        self.assertIsNone(plays.delta(d, "songs", "PDR-002", "spotify_streams", 7))
        self.assertIsNone(plays.total_plays(d, "PDR-002"))                                    # 期間つきの数字は合計に入れない

    def test_quiet_skips_when_browser_job_running(self):
        with mock.patch.object(plays, "_dashboard_busy", lambda: True):
            r = plays.collect_s4a(plays.load(), plays.released_songs(), ARTS, self.day, quiet=True,
                                  raw=lambda t, o: self.fail("Chrome に触ってはいけない"))
        self.assertEqual(r["state"], "skipped")


class Files(Base):
    DK = ("Reporting Date\tSale Month\tStore\tArtist\tTitle\tISRC\tUPC\tQuantity\tSong/Album\tEarnings (USD)\n"
          "2026-12-01\t2026-10\tSpotify\tHOLLOWPLAN\tServices at Three\tQZAAA2600001\t1\t120\tSong\t0.4\n"
          "2026-12-01\t2026-11\tSpotify\tHOLLOWPLAN\tServices at Three\tQZAAA2600001\t1\t80\tSong\t0.3\n"
          "2026-12-01\t2026-11\tApple Music\tHOLLOWPLAN\tServices at Three\tQZAAA2600001\t1\t25\tSong\t0.2\n"
          "2026-12-01\t2026-11\tSpotify\tSOMEONE\tNot Ours\tXX\t1\t999\tSong\t1\n")

    def test_distrokid_tsv_and_s4a_csv(self):
        (self.inbox / "DistroKid_1234.tsv").write_text(self.DK, encoding="utf-8")
        (self.inbox / "songs.csv").write_text("song,listeners,streams,saves,release_date\nAfter Nights,31,47,2,2026-11-11\n", encoding="utf-8")
        (self.inbox / "家計簿.csv").write_text("日付,金額\n2026-11-01,100\n", encoding="utf-8")
        d = plays.load()
        r = plays.collect_files(d, plays.released_songs(), self.day)
        self.assertEqual(r["state"], "ok")
        songs = d["days"][self.day]["songs"]
        self.assertEqual({k: songs["PDR-002"][k] for k in ("dk_streams", "dk_spotify", "dk_month")},
                         {"dk_streams": 225, "dk_spotify": 200, "dk_month": "2026-11"})
        self.assertEqual((songs["2026-11-02_hollowplan"]["spotify_streams"], songs["2026-11-02_hollowplan"]["spotify_period"]), (47, "file"))
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()), ["家計簿.csv"])            # 関係ないファイルは動かさない
        self.assertEqual(len(list((plays.DIR / "imported").iterdir())), 2)
        daily = collect_metrics.load_daily()
        self.assertEqual(int(daily[("2026-10-11_hollowplan", "2026-11-01", "spotify")]["streams"]), 80)

    def test_distrokid_statement_fills_isrc_and_upc(self):
        """明細に載っている ISRC・UPC で、曲の記録の空いている欄を埋める（Spotify の API を使わないとき。決定 180）"""
        song = plays._songs.by_id("PDR-002")
        st = plays._songs.state(song)
        st.pop("isrc", None)
        st["upc"] = ""
        plays._songs.save_state(song, st)
        (self.inbox / "DistroKid_1234.tsv").write_text(self.DK, encoding="utf-8")
        r = plays.collect_files(plays.load(), plays.released_songs(), self.day)
        st = plays._songs.state(plays._songs.by_id("PDR-002"))
        self.assertEqual((st.get("isrc"), st.get("upc")), ("QZAAA2600001", "1"))
        self.assertIn("ISRC・UPC を 1 曲に記録しました", r["msg"])

    def test_same_file_twice_is_not_counted_twice(self):
        d = plays.load()
        (self.inbox / "DistroKid_a.tsv").write_text(self.DK, encoding="utf-8")
        plays.collect_files(d, plays.released_songs(), self.day)
        (self.inbox / "DistroKid_a (1).tsv").write_text(self.DK, encoding="utf-8")
        r = plays.collect_files(d, plays.released_songs(), self.day)
        self.assertIn("同じ", r["msg"])
        self.assertEqual(d["days"][self.day]["songs"]["PDR-002"]["dk_streams"], 225)
        newer = self.DK + "2027-01-01\t2026-12\tSpotify\tHOLLOWPLAN\tServices at Three\tQZAAA2600001\t1\t10\tSong\t0.1\n"
        (self.inbox / "DistroKid_b.tsv").write_text(newer, encoding="utf-8")                   # 新しい明細は「全部入り」：足さずに置き換える
        plays.collect_files(d, plays.released_songs(), self.day)
        self.assertEqual(d["days"][self.day]["songs"]["PDR-002"]["dk_streams"], 235)
        daily = collect_metrics.load_daily()
        self.assertEqual(int(daily[("2026-10-11_hollowplan", "2026-11-01", "spotify")]["streams"]), 80)

    def test_no_files(self):
        self.assertEqual(plays.collect_files(plays.load(), plays.released_songs(), self.day)["state"], "none")


class Snapshots(Base):
    def fill(self):
        d = plays.load()
        today = date.today()
        for back, sp, yt in ((28, 100, 10), (8, 400, 30), (7, 500, 40), (1, 900, 70), (0, 1000, 80)):
            day = (today - timedelta(days=back)).isoformat()
            plays.put(d, day, "songs", "PDR-002", {"spotify_streams": sp, "spotify_period": "all", "youtube_views": yt}, "s4a")
        plays.put(d, self.day, "artists", "hollowplan", {"followers": 41}, "api")
        plays.save(d)
        return plays.load()

    def test_delta_total_series(self):
        d = self.fill()
        self.assertEqual(plays.delta(d, "songs", "PDR-002", "spotify_streams", 1), 100)
        self.assertEqual(plays.delta(d, "songs", "PDR-002", "spotify_streams", 7), 500)
        self.assertEqual(plays.delta(d, "songs", "PDR-002", "spotify_streams", 28), 900)
        self.assertEqual(plays.delta(d, "songs", "PDR-002", "youtube_views", 7), 40)
        self.assertIsNone(plays.delta(d, "songs", "PDR-005", "spotify_streams", 7))
        self.assertEqual(plays.total_plays(d, "PDR-002"), 1080)
        self.assertEqual(plays.series(d, "PDR-002"), [110, 430, 540, 970, 1080])

    def test_same_day_overwrites_and_keeps_other_fields(self):
        d = plays.load()
        plays.put(d, self.day, "songs", "PDR-002", {"spotify_streams": 5, "youtube_views": 1}, "s4a")
        plays.put(d, self.day, "songs", "PDR-002", {"spotify_streams": 9, "spotify_saves": None}, "api")
        self.assertEqual(d["days"][self.day]["songs"]["PDR-002"], {"spotify_streams": 9, "youtube_views": 1, "source": ["s4a", "api"]})
        self.assertEqual(len(d["days"]), 1)

    def test_status_and_page(self):
        self.fill()
        st = plays.status()
        self.assertEqual((st["songs"], st["with_plays"], st["total_plays"], st["plays_7d"], st["followers"]), (3, 1, 1080, 540, 41))
        self.assertEqual(st["top"][0]["title"], "Services at Three")
        self.assertTrue(st["layers"]["api_spotify"]["ready"])
        self.assertFalse(st["layers"]["s4a"]["ready"])
        html = plays.page("tok")
        for want in ("Services at Three", "1,080", "+540", "<svg", "plays_collect", "browser_login", "HOLLOWPLAN", "TIDEWOOL"):
            self.assertIn(want, html)

    def test_page_without_songs_or_keys(self):
        with mock.patch.object(plays, "released_songs", lambda: []), mock.patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": "", "YOUTUBE_API_KEY": ""}):
            html = plays.page("tok")
            self.assertIn("配信が始まった曲がまだありません", html)
            self.assertIn("youtube_key", html)
            self.assertIn('"key": "spotify"', html.replace("&quot;", '"'))
            res = plays.collect()
            self.assertEqual(res["songs"], 0)


class Collect(Base):
    def test_end_to_end_api_and_files(self):
        (self.inbox / "DistroKid_1.tsv").write_text(Files.DK, encoding="utf-8")
        res = plays.collect(("api", "files"), quiet=True)
        self.assertEqual({k: v["state"] for k, v in res["layers"].items()}, {"api_spotify": "ok", "api_youtube": "ok", "files": "ok"})
        d = plays.load()
        s = d["days"][self.day]["songs"]["PDR-002"]
        self.assertEqual((s["spotify_popularity"], s["youtube_views"], s["dk_streams"]), (12, 250, 225))
        self.assertEqual(sorted(s["source"]), ["api", "files"])
        self.assertEqual(set(d["last"]["layers"]), {"api_spotify", "api_youtube", "files"})
        plays.collect(("api",), quiet=True)                                                   # 同じ日にもう一度：1 日 1 つのまま
        self.assertEqual(len(plays.load()["days"]), 1)
        self.assertIn("files", plays.load()["last"]["layers"])                                # 前回のほかの集め方の結果は残す

    def test_one_layer_failing_does_not_stop_the_others(self):
        with mock.patch.object(plays, "collect_spotify", side_effect=RuntimeError("boom")):
            res = plays.collect(("api",), quiet=True)
        self.assertEqual(res["layers"]["api_spotify"]["state"], "error")
        self.assertEqual(res["layers"]["api_youtube"]["state"], "ok")


class Released(unittest.TestCase):
    def test_released_songs_filter(self):
        today = date.today()
        songs = [{"id": "a", "key": "a", "slug": "x", "artist": "X", "title": "Live", "week": "2026-10-11"},
                 {"id": "b", "key": "b", "slug": "x", "artist": "X", "title": "Registered past", "week": "2026-10-11"},
                 {"id": "c", "key": "c", "slug": "x", "artist": "X", "title": "Registered future", "week": "2026-10-11"},
                 {"id": "d", "key": "d", "slug": "x", "artist": "X", "title": "Not yet", "week": "2026-10-11"},
                 {"id": "e", "key": "e", "slug": "x", "artist": "X", "title": "Test week", "week": "2000-01-03"}]
        states = {"a": {"status": "live", "url": "https://open.spotify.com/intl-ja/track/" + "9" * 22 + "?si=1", "isrc": "qz1"},
                  "b": {"status": "registered", "release_date": (today - timedelta(days=1)).isoformat()},
                  "c": {"status": "registered", "release_date": (today + timedelta(days=5)).isoformat()},
                  "d": {"status": "ready"}, "e": {"status": "live"}}
        with mock.patch.object(plays._songs, "all_songs", lambda: songs), mock.patch.object(plays._songs, "state", lambda s: states[s["id"]]):
            got = plays.released_songs()
        self.assertEqual([s["id"] for s in got], ["a", "b"])
        self.assertEqual((got[0]["track_id"], got[0]["isrc"]), ("9" * 22, "QZ1"))


if __name__ == "__main__":
    unittest.main()
