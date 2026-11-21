"""Spotify へのピッチ（scripts/pitch.py・決定 171）の確認。本物の out/ には触らない（一時フォルダだけ）"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _claude  # noqa: E402
import _songs  # noqa: E402
import pitch  # noqa: E402

TODAY = date(2026, 11, 20)
ARTIST = {"slug": "hollowplan", "name": "HOLLOWPLAN",
          "sound": {"genres": ["house", "lo-fi house", "deep house"], "dna_tags": ["deep house", "lo-fi", "vocal chops", "warm keys", "hypnotic"]},
          "profile": {"hometown": "イギリス・ロンドン"}, "life": {"act": {"base_now": "ロンドン東部レイトン"}},
          "distribution": {"bio_en": "HOLLOWPLAN writes house music at night."}}
STORY = ("The song follows a night coach heading north at three in the morning. Nothing happens, and that is the point. "
         "It settles into the hum of the road and stays there until the lights of the next services appear.")


def song(i: int, **kw) -> dict:
    s = {"id": f"2026-11-{i:02d}_hollowplan", "key": f"2026-11-{i:02d}_hollowplan", "kind": "weekly", "slug": "hollowplan", "label_slug": None,
         "artist": "HOLLOWPLAN", "title": f"Services at Three {i}", "week": f"2026-11-{i:02d}", "genre": ["Dance", "Electronic"],
         "style": "deep house, lo-fi house, 112 BPM, hypnotic and warm, soft four-on-the-floor kick, 1970s electric piano, round sub bass, vocal chops",
         "bpm": 112, "language": "English", "instrumental": False, "episode": {"story_en": STORY, "theme": "夜行バスの 4 時間半"}, "scene": None}
    s.update(kw)
    return s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"
        self.songs: list[dict] = []
        for p in (mock.patch.object(pitch, "OUT", self.out), mock.patch.object(_songs, "STATE_DIR", self.out / "publish" / "state"),
                  mock.patch.object(_songs, "all_songs", lambda: self.songs),
                  mock.patch.object(_songs, "by_id", lambda sid: next((s for s in self.songs if s["id"] == sid), None)),
                  mock.patch.object(pitch, "load_artist", lambda *a, **k: json.loads(json.dumps(ARTIST)))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def add(self, i: int, status: str = "registered", release: date | None = None, **kw) -> dict:
        s = song(i, **kw)
        self.songs.append(s)
        st = {"status": status, "registered_at": "2026-11-10"}
        if release:
            st["release_date"] = release.isoformat()
        _songs.save_state(s, st)
        return s

    def plan(self, s: dict, posts: list[tuple[str, str]]):
        d = self.out / "social" / s["week"] / s["slug"]
        d.mkdir(parents=True)
        (d / "plan.json").write_text(json.dumps({"artist": s["artist"], "title": s["title"],
                                                 "posts": [{"platform": p, "scheduled_at": f"{day}T22:00:00Z", "status": "pending"} for p, day in posts]}))


class TestStates(Base):
    def test_only_registered_songs_with_a_date(self):
        self.assertIsNone(pitch.info(self.add(1, status="ready", release=TODAY + timedelta(days=20)), TODAY))
        self.assertIsNone(pitch.info(self.add(2, status="live", release=TODAY - timedelta(days=1)), TODAY))
        self.assertIsNone(pitch.info(self.add(3, status="registered"), TODAY))   # 配信日が分からない
        self.assertEqual(pitch.info(self.add(4, release=TODAY + timedelta(days=20)), TODAY)["state"], "todo")

    def test_deadline_is_seven_days_before_release(self):
        i = pitch.info(self.add(1, release=TODAY + timedelta(days=20)), TODAY)
        self.assertEqual(i["deadline"], (TODAY + timedelta(days=13)).isoformat())
        self.assertEqual(i["days_left"], 13)

    def test_soon_late_boundaries(self):
        st = lambda days: pitch.info(self.add(len(self.songs) + 1, release=TODAY + timedelta(days=days)), TODAY)["state"]  # noqa: E731
        self.assertEqual(st(11), "todo")    # 締め切りまで 4 日
        self.assertEqual(st(10), "soon")    # 3 日
        self.assertEqual(st(7), "soon")     # きょうが締め切り
        self.assertEqual(st(6), "late")     # きのうが締め切り

    def test_mark_done_skip_undo(self):
        s = self.add(1, release=TODAY + timedelta(days=20))
        pitch.mark(s, "done")
        self.assertEqual(pitch.info(s, TODAY)["state"], "done")
        self.assertEqual(_songs.state(s)["pitch"]["done_at"], date.today().isoformat())
        pitch.mark(s, "skip")
        self.assertEqual(pitch.info(s, TODAY)["state"], "skip")
        self.assertNotIn("done_at", _songs.state(s)["pitch"])
        pitch.mark(s, "undo")
        self.assertEqual(pitch.info(s, TODAY)["state"], "todo")
        self.assertNotIn("pitch", _songs.state(s))
        self.assertEqual(_songs.state(s)["status"], "registered")   # ほかの記録は変えない

    def test_todos_sorted_and_late_fades_after_a_week(self):
        self.add(1, release=TODAY + timedelta(days=30))
        self.add(2, release=TODAY + timedelta(days=9))     # 急ぎ
        self.add(3, release=TODAY + timedelta(days=3))     # 4 日過ぎた
        self.add(4, release=TODAY - timedelta(days=5))     # 12 日過ぎた → もう知らせない
        done = self.add(5, release=TODAY + timedelta(days=8))
        pitch.mark(done, "done")
        ts = pitch.todos(TODAY)
        self.assertEqual([t["id"][8:10] for t in ts], ["02", "01", "03"])
        self.assertEqual(ts[0]["action"]["href"], "/pitch?id=2026-11-02_hollowplan")
        self.assertEqual(pitch.status(TODAY), {"open": 2, "soon": 1, "late": 1, "done": 1})
        self.assertEqual(len(pitch.alerts(TODAY)), 1)
        self.assertIn("Services at Three 2", pitch.alerts(TODAY)[0])


class TestBuild(Base):
    def test_template_without_claude(self):
        s = self.add(1, release=TODAY + timedelta(days=20))
        self.plan(s, [("youtube", "2026-12-10"), ("instagram", "2026-12-10"), ("tiktok", "2026-12-11")])
        d = pitch.build(s, use_claude=False, quiet=True)
        self.assertLessEqual(len(d["description_en"]), pitch.MAX_DESC)
        self.assertEqual(d["chars"], len(d["description_en"]))
        self.assertFalse(pitch._bad(d["description_en"]))
        self.assertIn("Services at Three 1", d["description_en"])
        self.assertIn("YouTube Shorts, Instagram Reels and TikTok", d["description_en"])
        self.assertIn("2026-12-10", d["description_en"])
        self.assertEqual(d["fields"]["genres"], ["Dance", "Electronic", "house"])
        self.assertIn("Electric Piano", d["fields"]["instruments"])
        self.assertLessEqual(len(d["fields"]["moods"]), 2)
        self.assertLessEqual(len(d["fields"]["styles"]), 3)
        self.assertEqual(d["fields"]["language"], "English")
        self.assertEqual(d["by"], "決まった形（Claude なし）")
        md = (self.out / "publish" / s["key"] / "ピッチ.md").read_text(encoding="utf-8")
        self.assertIn(d["description_en"], md)
        self.assertIn("ジャンル（3 つまで）：Dance、Electronic、house", md)
        self.assertEqual(pitch.load_draft(s)["description_en"], d["description_en"])

    def test_no_plan_no_invented_marketing(self):
        d = pitch.build(self.add(1, release=TODAY + timedelta(days=20)), use_claude=False, quiet=True)
        self.assertNotIn("scheduled", d["description_en"])
        self.assertTrue(any("SNS の投稿の予定がまだ無い" in c for c in d["checks"]))

    def test_long_story_still_fits(self):
        s = self.add(1, release=TODAY + timedelta(days=20), episode={"story_en": "A very long sentence about the road and the night " * 30 + "."},
                     title="A Title That Is Fairly Long For A Song About Roads")
        self.plan(s, [("youtube", "2026-12-10")])
        d = pitch.build(s, use_claude=False, quiet=True)
        self.assertLessEqual(len(d["description_en"]), pitch.MAX_DESC)
        self.assertIn("YouTube Shorts on 2026-12-10", d["description_en"])   # 宣伝の予定は削らない

    def test_instrumental_has_no_vocals(self):
        d = pitch.build(self.add(1, release=TODAY + timedelta(days=20), instrumental=True, language="none"), use_claude=False, quiet=True)
        self.assertNotIn("Vocals", d["fields"]["instruments"])
        self.assertEqual(d["fields"]["instrumental"], "はい（歌なし）")
        self.assertIn("instrumental", d["description_en"])

    def test_fit_cuts_at_sentence(self):
        t = pitch._fit("One sentence here. " * 40)
        self.assertLessEqual(len(t), pitch.MAX_DESC)
        self.assertTrue(t.endswith("."))
        self.assertEqual(pitch._fit("short"), "short")

    def test_banned_words(self):
        for bad in ("An AI track", "made with A.I. tools", "as featured in a magazine", "generated by a model", "a Suno song", "millions of streams"):
            self.assertTrue(pitch._bad(bad), bad)
        for ok in ("A rainy night in Saitama", "The air is still", "Daily routine, again"):
            self.assertFalse(pitch._bad(ok), ok)


def answer(desc: str, **kw) -> dict:
    return {"description_en": desc, "description_ja": "日本語の訳", "moods": ["Chill", "Meditative", "Sad"], "styles": ["Lo-fi", "Hypnotic"],
            "instruments": ["Electric Piano", "Vocals", "Drums"], "cultures": [], "home_en": "London, UK", **kw}


class TestClaude(Base):
    def run_build(self, answers: list, **kw):
        calls = []

        def fake(system, user, schema, effort="high", max_tokens=0):
            calls.append(user)
            a = answers[min(len(calls) - 1, len(answers) - 1)]
            return (a, "OK") if a else (None, "API エラー 500")
        with mock.patch.object(_claude, "available", lambda: True), mock.patch.object(_claude, "call_json", fake):
            d = pitch.build(self.add(1, release=TODAY + timedelta(days=20), **kw), quiet=True)
        return d, calls

    def test_uses_claude_answer(self):
        d, calls = self.run_build([answer("A warm deep house track for the long, quiet stretch of a night drive.")])
        self.assertEqual(d["by"], "Claude")
        self.assertEqual(len(calls), 1)
        self.assertEqual(d["description_ja"], "日本語の訳")
        self.assertEqual(d["fields"]["moods"], ["Chill", "Meditative"])   # 2 つまで
        self.assertEqual(d["fields"]["home"], "London, UK")
        self.assertNotIn("s4a_claimed", calls[0])

    def test_too_long_is_retried_once(self):
        d, calls = self.run_build([answer("x" * 700), answer("A second, shorter try that fits.")])
        self.assertEqual(len(calls), 2)
        self.assertIn("rejected", calls[1])
        self.assertEqual(d["description_en"], "A second, shorter try that fits.")

    def test_still_too_long_is_trimmed(self):
        d, _ = self.run_build([answer("Many words in a row. " * 60)])
        self.assertLessEqual(len(d["description_en"]), pitch.MAX_DESC)
        self.assertEqual(d["by"], "Claude")

    def test_banned_twice_falls_back_to_template(self):
        d, calls = self.run_build([answer("An AI-generated house track.")])
        self.assertEqual(len(calls), 2)
        self.assertEqual(d["by"], "決まった形（Claude なし）")
        self.assertFalse(pitch._bad(d["description_en"]))
        self.assertTrue(any("Claude では作れなかった" in c for c in d["checks"]))

    def test_api_error_falls_back(self):
        d, _ = self.run_build([None])
        self.assertEqual(d["by"], "決まった形（Claude なし）")
        self.assertLessEqual(len(d["description_en"]), pitch.MAX_DESC)

    def test_instrumental_drops_vocals_from_claude(self):
        d, _ = self.run_build([answer("A calm instrumental.")], instrumental=True)
        self.assertEqual(d["fields"]["instruments"], ["Electric Piano", "Drums"])


class TestAutoAndPage(Base):
    def test_auto_builds_only_open_songs_without_draft(self):
        a = self.add(1, release=TODAY + timedelta(days=20))
        late = self.add(2, release=date.today() + timedelta(days=2))
        b = self.add(3, release=date.today() + timedelta(days=30))
        done = self.add(4, release=date.today() + timedelta(days=30))
        pitch.mark(done, "done")
        _songs.save_state(a, {**_songs.state(a), "release_date": (date.today() + timedelta(days=30)).isoformat()})
        with mock.patch.object(_claude, "available", lambda: False):
            self.assertEqual(pitch.auto(quiet=True), 2)
            self.assertEqual(pitch.auto(quiet=True), 0)   # 2 回目は作らない
        self.assertTrue(pitch.draft_path(a).exists() and pitch.draft_path(b).exists())
        self.assertFalse(pitch.draft_path(late).exists() or pitch.draft_path(done).exists())

    def test_pages(self):
        s = self.add(1, release=date.today() + timedelta(days=20))
        idx = pitch.page("TOK")
        self.assertIn("/pitch?id=" + s["id"], idx)
        self.assertIn('const TOKEN="TOK"', idx)
        self.assertIn(pitch.CLAIM_URL, idx)
        before = pitch.page("TOK", s["id"])
        self.assertIn("pitch_build", before)
        self.assertIn("下書きを作る", before)
        pitch.build(s, use_claude=False, quiet=True)
        pg = pitch.page("TOK", s["id"])
        for needle in ("pitch_done", "pitch_skip", "pitch_build", 'id="pdesc"', 'data-copy="pdesc"', pitch.S4A_URL, 'rel="noopener"', "ピッチした"):
            self.assertIn(needle, pg)
        self.assertNotIn("pitch_undo", pg)
        pitch.mark(s, "done")
        after = pitch.page("TOK", s["id"])
        self.assertIn("pitch_undo", after)
        self.assertNotIn("pitch_done", after)
        self.assertIn("見つかりません", pitch.page("TOK", "nope"))

    def test_late_page_has_no_done_button(self):
        s = self.add(1, release=date.today() + timedelta(days=3))
        pg = pitch.page("TOK", s["id"])
        self.assertIn("もうピッチできません", pg)
        self.assertNotIn("pitch_done", pg)
        self.assertIn("pitch_skip", pg)

    def test_title_is_escaped(self):
        s = self.add(1, release=date.today() + timedelta(days=20), title="<script>x</script>")
        pitch.build(s, use_claude=False, quiet=True)
        for pg in (pitch.page("TOK"), pitch.page("TOK", s["id"])):
            self.assertNotIn("<script>x</script>", pg)


if __name__ == "__main__":
    unittest.main()
