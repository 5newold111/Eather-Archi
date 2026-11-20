"""決定 178 の試験：仕上げの流れを 1 本に（毎時の制作は SNS の準備だけ。仕上げは取り込みが行う）"""
import inspect
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import Project  # noqa: E402


class HourlyJobTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project(ssd=True)
        cls.week = cls.p.week(-1)
        cls.key = cls.p.weekly("hollowplan", cls.week, "Night Shift", stage="ready")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def test_1_finish_stage_does_not_finish_songs(self):
        """毎時の「まとめ」は、仕上げ・ジャケット・保管の部品を呼ばない"""
        rw = self.p.mod("run_week")
        calls = [ln for ln in inspect.getsource(rw.stage_finish).splitlines() if "run([" in ln]
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(any("distrokid_sheet.py" in c for c in calls) and any("post_social.py" in c for c in calls))
        for gone in ("master_track.py", "generate_visuals.py", "finalize_cover.py", "archive_sync.py", "supabase_sync.py"):
            self.assertFalse(any(gone in c for c in calls), gone)

    def test_2_finish_stage_runs(self):
        r = self.p.run("run_week.py", "finish", "--week", self.week)
        self.assertEqual(r.returncode, 0, r.stdout[-600:] + r.stderr[-600:])
        self.assertNotIn("音量を整えています", r.stdout)
        self.assertNotIn("外付け SSD に保管", r.stdout)
        self.assertTrue((self.p.out / "distrokid" / self.week / "hollowplan.metadata.json").exists())

    def test_3_meta_not_rewritten_when_unchanged(self):
        """登録データは、変わったときだけ書く（毎時書くと、SNS の縦動画が毎時作り直される）"""
        mp = self.p.out / "distrokid" / self.week / "hollowplan.metadata.json"
        self.p.run("distrokid_sheet.py", "--week", self.week)
        before = mp.stat().st_mtime_ns
        time.sleep(0.05)
        r = self.p.run("distrokid_sheet.py", "--week", self.week)
        self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])
        self.assertEqual(mp.stat().st_mtime_ns, before)
        m = json.loads(mp.read_text(encoding="utf-8"))
        self.assertEqual(m["title"], "Night Shift")


class AdoptTest(unittest.TestCase):
    """out/takes/<曲>/ に手で置いて選んだテイクは、取り込みの流れが引き継ぐ"""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(ssd=True)
        cls.key = cls.p.weekly("hollowplan", cls.p.week(-1), "Hand Picked", stage="brief")
        cls.tdir = cls.p.out / "takes" / cls.key

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def test_adopt(self):
        publish = self.p.mod("publish")
        song = self.p.song(self.key)
        self.assertFalse(publish.adopt_selected(song))                        # テイクが無い
        self.p.wav(self.tdir / "take_01.wav", sec=0.5)
        (self.tdir / "selection.json").write_text(json.dumps({"decision": {"selected": None}}), encoding="utf-8")
        self.assertFalse(publish.adopt_selected(song))                        # まだ選んでいない
        (self.tdir / "selection.json").write_text(json.dumps({"decision": {"selected": "gone.wav"}}), encoding="utf-8")
        self.assertFalse(publish.adopt_selected(song))                        # 選んだファイルが無い
        (self.tdir / "selection.json").write_text(json.dumps({"decision": {"selected": "take_01.wav", "at": "2026-11-18T09:00:00"}}), encoding="utf-8")
        self.assertTrue(publish.adopt_selected(song))
        st = self.p.state(self.key)
        self.assertEqual((st["status"], st["take"], st["imported_at"], st["adopted"]), ("imported", "take_01.wav", "2026-11-18T09:00:00", True))
        self.assertFalse(publish.adopt_selected(song))                        # 2 回目は何もしない（もう流れに乗っている）

    def test_scan_calls_adopt(self):
        publish = self.p.mod("publish")
        self.assertIn("adopt_selected(song)", inspect.getsource(publish.cmd_scan))


if __name__ == "__main__":
    unittest.main()
