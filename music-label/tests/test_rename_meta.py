"""決定 177 の試験：曲名と配信の記録の置き場所を 1 つに・曲名を変える・公開セットは変わったときだけ・Supabase への自動の写しをやめた"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import Project  # noqa: E402


def _json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


class OneSourceTest(unittest.TestCase):
    """曲名＝ブリーフ、配信の記録＝曲の記録。登録データ（metadata.json）は写し"""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(ssd=True)
        cls.week = cls.p.week(-3)
        cls.key = cls.p.weekly("hollowplan", cls.week, "After Nights", stage="ready")
        cls.meta = cls.p.out / "distrokid" / cls.week / "hollowplan.metadata.json"

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def save(self, **kw) -> None:
        """本物と同じ道（_songs.save_state）で曲の記録を書く"""
        songs = self.p.mod("_songs")
        songs.save_state(self.p.song(self.key), {**self.p.state(self.key), **kw})

    def test_1_title_comes_from_brief_not_meta(self):
        """登録データに古い曲名が残っていても、ブリーフの曲名が使われる"""
        self.meta.parent.mkdir(parents=True, exist_ok=True)
        (self.p.out / "publish").mkdir(parents=True, exist_ok=True)
        (self.p.out / "publish" / "meta_migrated.json").write_text("{}", encoding="utf-8")   # 引っ越しは済んだあと、とする
        self.meta.write_text(json.dumps({"artist_slug": "hollowplan", "week_start": self.week, "title": "Old Stale Name",
                                         "release_at_utc": f"{self.week}T21:00:00Z", "status": "planned"}), encoding="utf-8")
        self.p.reload()
        self.assertEqual(self.p.song(self.key)["title"], "After Nights")

    def test_2_state_is_mirrored_to_meta(self):
        """登録・配信の記録を曲の記録に書くと、登録データ（写し）にも入る"""
        songs = self.p.mod("_songs")
        self.save(status="registered", registered_at="2026-11-10T10:00:00", release_date="2026-12-01")
        m = _json(self.meta)
        self.assertEqual((m["title"], m["status"], m["release_at_utc"][:10]), ("After Nights", "uploaded", "2026-12-01"))
        self.save(status="live", released="2026-12-02", isrc="QZTEST2600009", upc="000000000009", url="https://open.spotify.com/track/x")
        m = _json(self.meta)
        self.assertEqual((m["status"], m["isrc"], m["upc"], m["release_at_utc"][:10]), ("live", "QZTEST2600009", "000000000009", "2026-12-02"))
        self.assertFalse(songs.sync_meta(self.p.song(self.key)))   # 同じなら書き直さない
        self.save(status="ready")                 # 登録を取り消した
        self.assertEqual(_json(self.meta)["status"], "planned")

    def test_3_readers_see_the_single_source(self):
        """成績集めは、写しを通して曲の記録の ISRC を読む"""
        self.save(status="live", released="2026-12-02", isrc="QZTEST2600009")
        cm = self.p.mod("collect_metrics")
        rel = [r for r in cm.releases() if r["release_id"] == self.key]
        self.assertEqual(len(rel), 1)
        self.assertEqual((rel[0]["title"], rel[0]["isrc"], rel[0]["release_date"]), ("After Nights", "QZTEST2600009", "2026-12-02"))
        self.save(status="ready")


class MigrateTest(unittest.TestCase):
    """前は登録データの曲名が優先されていた → 1 回だけ、ブリーフと曲の記録へ引っ越す（見えている曲名は変わらない）"""

    def test_migrate_once(self):
        p = Project(ssd=False)
        try:
            week = p.week(-2)
            key = p.weekly("tidewool", week, "Brief Name", stage="imported")
            mp = p.out / "distrokid" / week / "tidewool.metadata.json"
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(json.dumps({"artist_slug": "tidewool", "week_start": week, "title": "Chosen Name", "isrc": "QZTEST2600001",
                                      "release_at_utc": f"{week}T21:00:00Z", "status": "planned"}), encoding="utf-8")
            mark = p.out / "publish" / "meta_migrated.json"
            mark.unlink(missing_ok=True)
            p.reload()
            self.assertEqual(p.song(key)["title"], "Chosen Name")                       # 前に見えていた曲名のまま
            self.assertEqual(_json(p.out / "briefs" / f"{key}.json")["title"], "Chosen Name")   # 正本（ブリーフ）に入った
            self.assertEqual(p.state(key)["isrc"], "QZTEST2600001")                      # ISRC は曲の記録へ
            self.assertTrue(mark.exists())
            b = _json(p.out / "briefs" / f"{key}.json")
            b["title"] = "Renamed Later"
            (p.out / "briefs" / f"{key}.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
            p.reload()
            self.assertEqual(p.song(key)["title"], "Renamed Later")                      # 2 回目からは登録データに戻されない
        finally:
            p.close()


class RenameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project(ssd=True)
        cls.week = cls.p.week(-2)
        cls.key = cls.p.weekly("hollowplan", cls.week, "After Nights", stage="ready")
        cls.other = cls.p.weekly("hollowplan", cls.p.week(-1), "Second Song", stage="brief")
        cls.reg = cls.p.weekly("tidewool", cls.week, "Out Already", stage="registered")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def test_1_refuses(self):
        publish = self.p.mod("publish")
        for key, title, word in ((self.key, "", "空"), (self.key, "After Nights", "同じ"), (self.key, "second  song", "もう"),
                                 (self.reg, "New Name", "登録済み"), (self.key, "x" * 121, "120")):
            with self.assertRaises(ValueError) as cm:
                publish.rename_song(self.p.song(key), title)
            self.assertIn(word, str(cm.exception))
        self.assertEqual(self.p.song(self.key)["title"], "After Nights")

    def test_2_rename_ready_song(self):
        publish, songs, archive = self.p.mod("publish"), self.p.mod("_songs"), self.p.mod("_archive")
        song = self.p.song(self.key)
        old_dir, _, _ = publish.publish_folder(song)
        self.assertTrue(old_dir.is_dir())
        calls = []
        publish.ensure_cover_text = lambda s, c: (calls.append(s["title"]) or True, "")   # 画像の部品が無い環境でも試せるように
        msg = publish.rename_song(song, "Morning After")
        self.assertIn("Morning After", msg)
        now = self.p.song(self.key)
        self.assertEqual(now["title"], "Morning After")
        self.assertIn("After Nights", now["alts"])                           # 前の名前は別案に残る（前の名前のファイルも取り込める）
        b = _json(self.p.out / "briefs" / f"{self.key}.json")
        self.assertEqual((b["title"], b["title_candidates"][0]), ("Morning After", "Morning After"))
        self.assertEqual(calls, ["Morning After"])                           # ジャケットの文字を新しい曲名で入れ直す
        new_dir, audio, _ = publish.publish_folder(now)
        self.assertEqual(new_dir.name, "Morning After")
        self.assertFalse(old_dir.exists())                                   # フォルダは名前が変わった（残骸なし）
        self.assertTrue((new_dir / audio).exists())
        self.assertTrue(audio.startswith("Morning After_"))
        self.assertFalse(any(f.name.startswith("After Nights") for f in new_dir.rglob("*")))
        self.assertIn("Morning After", (self.p.out / "publish" / self.key / "DistroKid下書き.md").read_text(encoding="utf-8"))
        self.assertEqual(self.p.state(self.key)["status"], "ready")          # 段階はそのまま

    def test_3_old_name_file_still_matches(self):
        publish = self.p.mod("publish")
        songs = [self.p.song(self.key), self.p.song(self.other)]
        f = self.p.drop("After Nights.mp3", "dl_inbox")
        got, _why = publish.match(f, songs)
        self.assertIsNotNone(got)
        self.assertEqual(got["key"], self.key)
        f.unlink()

    def test_4_command(self):
        r = self.p.run("publish.py", "rename", self.other, "--title=Third Light")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("【結果】", r.stdout)
        self.p.reload()
        self.assertEqual(self.p.song(self.other)["title"], "Third Light")
        r = self.p.run("publish.py", "rename", self.reg, "--title=Nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("曲名は変えていません", r.stdout)

    def test_5_dashboard_button(self):
        webapp = self.p.mod("webapp")
        cmd, label = webapp.command_for("rename", {"id": self.other, "title": "  Fourth   Light "})
        self.assertEqual(cmd[-3:], ["rename", self.other, "--title=Fourth Light"])
        self.assertIsNone(webapp.command_for("rename", {"id": self.other, "title": ""}))
        self.assertIsNone(webapp.command_for("rename", {"id": "nope", "title": "x"}))
        self.assertIn('data-act="rename"', webapp.PAGE)


class DebutRenameTest(unittest.TestCase):
    """デビュー曲は、作業セット（kit.json）の曲名が正本"""

    def test_debut(self):
        p = Project(ssd=True, debuted=())
        try:
            key = p.debut("tidewool", "The Low Part", stage="imported")
            publish = p.mod("publish")
            publish.ensure_cover_text = lambda s, c: (True, "")
            publish.rename_song(p.song(key), "The High Part")
            now = p.song(key)
            self.assertEqual((now["title"], now["kind"]), ("The High Part", "debut"))
            self.assertIn("The Low Part", now["alts"])
            kit = _json(Path(now["kit"]) / "kit.json")
            self.assertEqual(next(x for x in kit["songs"] if x["slug"] == "tidewool")["title"], "The High Part")
        finally:
            p.close()


class SsdLaterTest(unittest.TestCase):
    """SSD が無い間に変えた曲名は、つないだときに SSD のフォルダにも反映する"""

    def test_pending(self):
        p = Project(ssd=False)
        try:
            key = p.weekly("hollowplan", p.week(-2), "Night Bus", stage="ready")
            publish = p.mod("publish")
            publish.ensure_cover_text = lambda s, c: (True, "")
            msg = publish.rename_song(p.song(key), "Day Bus")
            self.assertIn("次につないだとき", msg)
            todo = _json(p.out / "publish" / "renames.json")["pending"]
            self.assertEqual((todo[0]["old_name"], todo[0]["new_name"]), ("Night Bus", "Day Bus"))
            self.assertEqual(publish.apply_pending_renames(), 0)             # まだ SSD が無い → 残す
            self.assertEqual(len(_json(p.out / "publish" / "renames.json")["pending"]), 1)
        finally:
            p.close()


class StoreSetTest(unittest.TestCase):
    """公開セットは、中身が変わったときだけ作り直す（前は 10 分ごとの取り込みのたびに作り直していた）"""

    def test_only_when_changed(self):
        p = Project(ssd=True)
        try:
            key = p.weekly("hollowplan", p.week(-2), "Slow Tide", stage="ready")
            publish = p.mod("publish")
            runs = []
            real = publish.subprocess.run
            publish.subprocess.run = lambda cmd, *a, **k: (runs.append(cmd), real(cmd, *a, **k))[1]
            try:
                self.assertTrue(publish.store_set(p.song(key)))               # 1 回目は作る
                self.assertFalse(publish.store_set(p.song(key)))              # 変わっていなければ何もしない
                self.assertFalse(publish.store_set(p.song(key)))
                self.assertEqual(len(runs), 1)
                m = p.out / "masters" / key / "master.wav"
                p.wav(m, sec=1.2, freq=500)                                   # 音源が変わった（仕上げ直し）
                self.assertTrue(publish.store_set(p.song(key)))
                self.assertTrue(publish.store_set(p.song(key), force=True))   # 強制
                self.assertEqual(len(runs), 3)
            finally:
                publish.subprocess.run = real
        finally:
            p.close()


class NoSupabaseTest(unittest.TestCase):
    def test_not_called_automatically(self):
        """毎時の制作とデビューの流れは、Supabase への写しを動かさない（手で動かす部品は残す）"""
        scripts = Path(__file__).resolve().parent.parent / "scripts"
        for name in ("run_week.py", "debut.py"):
            code = "\n".join(ln for ln in (scripts / name).read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#"))
            self.assertNotIn("supabase_sync.py", code, name)
        self.assertTrue((scripts / "supabase_sync.py").exists())


if __name__ == "__main__":
    unittest.main()
