"""工程表（_timeline.py・_status.py・_songs.py）：段階ごとの工程の状態・デビュー完了の決まり・外す手続き中の組（決定 54・120・146・166）"""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta

from _fixture import STAGES, Project


def states(row: dict) -> dict[str, str]:
    return {p["name"]: p["state"] for p in row["phases"]}


class PhasesTest(unittest.TestCase):
    """毎週の曲：段階ごとに、どの工程が「いまの番」か"""

    @classmethod
    def setUpClass(cls):
        cls.p = p = Project(ssd=False, debuted=("declan_fay", "hollowplan"))
        cls.keys = {st: p.weekly("hollowplan", p.week(-i - 1), f"Song {st}", stage=st) for i, st in enumerate(STAGES)}
        cls.k_nobrief = p.weekly("hollowplan", p.week(-8), "No Style Yet", suno_style_prompt="")
        cls.k_late = p.weekly("hollowplan", p.week(-9), "Late Ready", stage="ready", days_ago=5)
        cls.k_irr = p.irregular("hollowplan", p.week(-10), "Side Street", stage="imported")
        tl = p.mod("_timeline").timeline()
        cls.tl = tl
        cls.rows = {r["id"]: r for k in ("debut", "regular", "irregular") for r in tl[k]["rows"] if not r.get("planned")}

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def cur(self, key: str):
        return next((x for x in self.rows[key]["phases"] if x["state"] in ("ng", "wait", "run")), None)

    def test_every_song_has_a_row_with_six_phases(self):
        for st, k in self.keys.items():
            self.assertIn(k, self.rows, st)
            self.assertEqual([x["name"] for x in self.rows[k]["phases"]],
                             ["ブリーフ", "Suno で作曲", "仕上げ（音量・ジャケット）", "DistroKid に登録", "配信待ち", "配信"])

    def test_current_phase_per_stage(self):
        want = {"brief": "Suno で作曲", "imported": "仕上げ（音量・ジャケット）", "mastered": "仕上げ（音量・ジャケット）",
                "ready": "DistroKid に登録", "registered": "配信待ち"}
        for st, name in want.items():
            c = self.cur(self.keys[st])
            self.assertIsNotNone(c, st)
            self.assertEqual(c["name"], name, st)

    def test_owner_turns(self):
        self.assertEqual(self.cur(self.keys["brief"])["who"], "あなた")
        self.assertEqual(self.cur(self.keys["ready"])["who"], "あなた")
        self.assertEqual(self.cur(self.keys["registered"])["state"], "run")

    def test_live_is_all_done_and_stays_listed(self):
        """配信が終わった曲も表から消さない（決定 54）"""
        s = states(self.rows[self.keys["live"]])
        self.assertEqual(set(s.values()), {"done"})
        self.assertIsNone(self.cur(self.keys["live"]))
        self.assertIsNone(self.rows[self.keys["live"]]["blocker"])

    def test_brief_without_style_waits_on_brief(self):
        self.assertEqual(self.cur(self.k_nobrief)["name"], "ブリーフ")

    def test_ready_for_days_becomes_ng(self):
        c = self.cur(self.k_late)
        self.assertEqual((c["name"], c["state"]), ("DistroKid に登録", "ng"))

    def test_irregular_song_is_in_irregular_table_only(self):
        ids = lambda k: {r["id"] for r in self.tl[k]["rows"]}  # noqa: E731
        self.assertIn(self.k_irr, ids("irregular"))
        self.assertNotIn(self.k_irr, ids("regular"))

    def test_phase_dates_are_ordered(self):
        for k, r in self.rows.items():
            for x in r["phases"]:
                self.assertLessEqual(x["start"], x["end"], (k, x["name"]))


class DebutDoneTest(unittest.TestCase):
    """デビューが終わったか（決定 120・146）：配信中／登録済みで配信日を過ぎた／Spotify のページがある。アーティストのページの記録は条件にしない"""

    def setUp(self):
        self.p = Project(ssd=False, debuted=())

    def tearDown(self):
        self.p.close()

    def done(self, slug: str) -> bool:
        self.p.reload()
        return self.p.mod("_timeline").debut_done(slug)

    def test_not_done_until_live_or_released(self):
        k = self.p.debut("tidewool", "The Low Part", "ready")
        self.assertFalse(self.done("tidewool"))
        self.p.set_state(k, status="registered", release_date=(date.today() + timedelta(days=5)).isoformat())
        self.assertFalse(self.done("tidewool"), "登録済みでも配信日の前はまだ")
        self.p.set_state(k, release_date=date.today().isoformat())
        self.assertTrue(self.done("tidewool"), "登録済みで配信日を過ぎた")

    def test_live_is_done_without_artist_pages(self):
        self.p.debut("tidewool", "The Low Part", "live")
        self.assertEqual(self.p.artist("tidewool").get("distribution", {}).get("artist_pages"), None)
        self.assertTrue(self.done("tidewool"), "アーティストのページの記録は条件にしない（決定 146）")

    def test_spotify_id_alone_is_done(self):
        self.p.debut("tidewool", "The Low Part", "brief")
        self.assertFalse(self.done("tidewool"))
        self.p.edit_artist("tidewool", lambda a: a.setdefault("distribution", {}).update(spotify_uri="spotify:artist:3333333333333333333333"))
        self.assertTrue(self.done("tidewool"))

    def test_spotify_id_from_release_record(self):
        self.p.debut("tidewool", "The Low Part", "brief")
        f = self.p.out / "knowledge" / "artist_links.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"tidewool": {"spotify": {"id": "3333333333333333333333"}}}), encoding="utf-8")
        self.assertTrue(self.done("tidewool"))

    def test_tabs(self):
        """デビューが終わっていない組はデビューの表に、終わった組は出さない。これから作る週（予定）はデビューを終えた組だけ"""
        self.p.debut("tidewool", "The Low Part", "imported")
        self.p.debut("declan_fay", "Side B", "live", days_ago=20)
        self.p.reload()
        tl = self.p.mod("_timeline").timeline()
        self.assertEqual([r["slug"] for r in tl["debut"]["rows"]], ["tidewool"])
        planned = {r["slug"] for r in tl["regular"]["rows"] if r.get("planned")}
        self.assertNotIn("tidewool", planned)
        t = self.p.mod("_timeline")
        self.assertTrue(t.in_regular("declan_fay"))
        self.assertTrue(t.in_regular("hollowplan"), "デビュー曲の作業セットが無い組は、最初から定期配信")
        self.assertFalse(t.in_regular("tidewool"))

    def test_weekly_production_waits_for_debut_registration(self):
        self.p.debut("tidewool", "The Low Part", "ready")
        c = self.p.mod("_common")
        ok, why = c.production_status(c.load_artist("tidewool"), c.monday_of(date.today()))
        self.assertFalse(ok)
        self.assertIn("デビュー曲の登録待ち", why)


class RetiringTest(unittest.TestCase):
    """外す手続き中の組（設定書に retire の印。決定 166）：これからの制作から外すが、出し切る曲は追いかける"""

    def setUp(self):
        self.p = p = Project(ssd=False, debuted=("declan_fay", "hollowplan"))
        self.k = p.weekly("hollowplan", p.week(-1), "Last One", stage="ready")
        p.edit_artist("hollowplan", lambda a: a.update(retire={"status": "retiring", "keep": [self.k], "at": date.today().isoformat()}))
        p.reload()

    def tearDown(self):
        self.p.close()

    def test_excluded_from_production_lists(self):
        c = self.p.mod("_common")
        self.assertNotIn("hollowplan", [a["slug"] for a in c.all_artists()])
        self.assertIn("hollowplan", [a["slug"] for a in c.all_artists(include_retiring=True)])
        ok, why = c.production_status(c.load_artist("hollowplan"), c.monday_of(date.today()))
        self.assertFalse(ok)
        self.assertIn("外す手続き中", why)

    def test_kept_song_is_still_tracked_but_no_planned_weeks(self):
        tl = self.p.mod("_timeline").timeline()
        rows = [r for r in tl["regular"]["rows"] if r["slug"] == "hollowplan"]
        self.assertEqual([r["id"] for r in rows if not r.get("planned")], [self.k])
        self.assertEqual([r for r in rows if r.get("planned")], [])


class SongsTest(unittest.TestCase):
    def setUp(self):
        self.p = Project(ssd=False, debuted=())

    def tearDown(self):
        self.p.close()

    def test_stopped_debut_song_is_skipped(self):
        k = self.p.debut("tidewool", "The Low Part", "imported")
        self.p.debut("declan_fay", "Side B", "brief")
        s = self.p.mod("_songs")
        self.assertEqual(len(s.debut_songs()), 2)
        self.p.set_state(k, status="stopped")
        self.assertEqual([x["slug"] for x in s.debut_songs()], ["declan_fay"])

    def test_test_weeks_and_side_files_are_not_songs(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "Real")
        p.weekly("hollowplan", "2000-01-03", "Trial")                       # 試験用の週
        (p.out / "briefs" / f"{k}.prompt.json").write_text("{}", encoding="utf-8")
        (p.out / "briefs" / "notes.json").write_text("{}", encoding="utf-8")
        self.assertEqual([x["key"] for x in p.mod("_songs").weekly_songs()], [k])

    def test_freeze_reads_once(self):
        p = self.p
        p.weekly("hollowplan", p.week(-1), "A")
        s = p.mod("_songs")
        s.freeze()
        try:
            p.weekly("hollowplan", p.week(-2), "B")
            self.assertEqual(len(s.all_songs()), 1, "表示 1 回の間は読み直さない")
        finally:
            s.thaw()
        self.assertEqual(len(s.all_songs()), 2)

    def test_names(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "A/B: Mix")
        s = p.mod("_songs")
        song = s.by_id(k)
        self.assertEqual(s.folder_name(song), "A／B： Mix", "Finder で使えない字は置き換える")
        self.assertEqual(s.cover_stem(song), "HOLLOWPLAN_A／B： Mix")
        self.assertEqual(s.norm("  Ｈello—World’s  "), "hello worlds")


if __name__ == "__main__":
    unittest.main()
