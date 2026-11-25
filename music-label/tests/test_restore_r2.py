"""R2 から戻す（restore_r2.py）の試験：一覧の続き読み・戻す場所・同じものは飛ばす・新しい手元は残す（決定 174）"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import restore_r2  # noqa: E402
from _mock_s3 import MockS3, pin  # noqa: E402

PORT = 19120
OLD = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)


class RestoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s3 = MockS3(PORT).start()

    @classmethod
    def tearDownClass(cls):
        cls.s3.stop()

    def setUp(self):
        pin(restore_r2)
        self.tmp = Path(tempfile.mkdtemp(prefix="restore-test-"))
        self.root = self.tmp / "music-label"
        self.root.mkdir()
        self.ssd = None
        out = self.root / "out"
        self.patches = [
            mock.patch.multiple(restore_r2, ROOT=self.root, OUT=out, MANIFEST=out / "backup" / "r2_manifest.json", RESTORE=out / "restore",
                                ssd_root=lambda: self.ssd, step=lambda *_: None),
            mock.patch.dict(os.environ, {**self.s3.env(), "R2_LIST_MAX": "2"}),   # 2 件ずつ → 続き読みを必ず通る
        ]
        for p in self.patches:
            p.start()
        self.s3.objects.clear()
        for key, body in {
            "label/out/briefs/2026-11-02_hollowplan.json": b'{"a":1}',
            "label/out/briefs/2026-11-02_hollowplan.lyrics_final.txt": "歌詞".encode(),
            "label/out/masters/2026-11-02_hollowplan/master.wav": b"RIFF" + b"\0" * 5000,
            "label/templates/artists/hollowplan.json": b'{"slug":"hollowplan"}',
            "label/ssd/01_管理表/楽曲管理.xlsx": b"xlsx-bytes",
            "label/vault/env.enc": b"secret-blob",
            "label/../evil.txt": b"x",
            "other/outside.txt": b"x",
        }.items():
            self.s3.put(key, body, OLD)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_paginates(self):
        objs = restore_r2.list_objects("acc", "label-backup", "AKTEST", "SKTEST")
        self.assertEqual(len(objs), 7)                                  # label/ の下だけ（other/ は入らない）
        self.assertGreaterEqual(sum(1 for m, p in self.s3.requests if "list-type" in p), 4)
        o = next(x for x in objs if x["key"].endswith("楽曲管理.xlsx"))
        self.assertEqual(o["size"], 10)
        self.assertAlmostEqual(o["modified"], OLD.timestamp(), delta=1)

    def test_dry_run_writes_nothing(self):
        c = restore_r2.restore(dry=True)
        self.assertEqual(c["new"], 5)
        self.assertEqual(c["skip"], 2)                                  # 金庫と、おかしな場所
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])

    def test_restore_then_same(self):
        c = restore_r2.restore()
        self.assertEqual((c["new"], c["update"]), (5, 0))
        f = self.root / "out/briefs/2026-11-02_hollowplan.json"
        self.assertEqual(f.read_bytes(), b'{"a":1}')
        self.assertEqual((self.root / "out/restore/ssd/01_管理表/楽曲管理.xlsx").read_bytes(), b"xlsx-bytes")   # SSD が無い → いったんここへ
        self.assertFalse((self.root / "vault").exists())
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertAlmostEqual(f.stat().st_mtime, OLD.timestamp(), delta=1)
        man = restore_r2.read_json(restore_r2.MANIFEST)
        st = f.stat()
        self.assertEqual(man["out/briefs/2026-11-02_hollowplan.json"], [st.st_size, int(st.st_mtime)])   # backup_r2.py と同じ印
        self.assertIn("ssd/01_管理表/楽曲管理.xlsx", man)
        self.assertEqual(list(self.root.rglob("*.part")), [])
        c = restore_r2.restore()
        self.assertEqual((c["new"], c["update"], c["same"]), (0, 0, 5))

    def test_ssd_connected(self):
        self.ssd = self.tmp / "SSD" / "Postdog Records"
        self.ssd.mkdir(parents=True)
        restore_r2.restore(only="ssd")
        self.assertEqual((self.ssd / "01_管理表/楽曲管理.xlsx").read_bytes(), b"xlsx-bytes")
        self.assertFalse((self.root / "out/briefs").exists())           # --only の外は戻さない

    def test_local_newer_is_kept_unless_forced(self):
        f = self.root / "templates/artists/hollowplan.json"
        f.parent.mkdir(parents=True)
        f.write_bytes(b'{"slug":"hollowplan","edited":true}')           # いま書いた ＝ R2（2 日前）より新しい
        c = restore_r2.restore(only="templates")
        self.assertEqual((c["local_newer"], c["update"]), (1, 0))
        self.assertIn(b"edited", f.read_bytes())
        c = restore_r2.restore(only="templates", force=True)
        self.assertEqual(c["update"], 1)
        self.assertEqual(f.read_bytes(), b'{"slug":"hollowplan"}')
        kept = list((self.root / "out/restore/replaced").rglob("hollowplan.json"))
        self.assertEqual(len(kept), 1)
        self.assertIn(b"edited", kept[0].read_bytes())                  # 前のファイルはよけてある

    def test_older_local_is_updated(self):
        f = self.root / "out/briefs/2026-11-02_hollowplan.json"
        f.parent.mkdir(parents=True)
        f.write_bytes(b'{"a":0}')
        old = time.time() - 10 * 86400
        os.utime(f, (old, old))
        c = restore_r2.restore(only="out/briefs")
        self.assertEqual(c["update"], 1)
        self.assertEqual(f.read_bytes(), b'{"a":1}')

    def test_bad_key_and_missing_creds(self):
        with mock.patch.dict(os.environ, {"R2_SECRET_ACCESS_KEY": "wrong"}):
            with self.assertRaises(restore_r2.R2Error) as e:
                restore_r2.restore(dry=True)
            self.assertIn("403", str(e.exception))
        with mock.patch.dict(os.environ, {"R2_ACCESS_KEY_ID": ""}):
            with self.assertRaises(restore_r2.R2Error):
                restore_r2.restore(dry=True)

    def test_dest_of(self):
        d = restore_r2.dest_of
        self.assertEqual(d("out/briefs/a.json", None), self.root / "out/briefs/a.json")
        self.assertEqual(d("ssd/02_アーティスト/X/a.wav", Path("/Volumes/DS72/Postdog Records")), Path("/Volumes/DS72/Postdog Records/02_アーティスト/X/a.wav"))
        for bad in ("../x", "a/../../x", "/etc/passwd", "vault/env.enc", ".env", "ssd", ""):
            self.assertIsNone(d(bad, None), bad)

    def test_put_get_roundtrip(self):
        restore_r2.put_object("acc", "label-backup", "label/日本語 の名前/a b.txt", b"hello", "text/plain", "AKTEST", "SKTEST")
        self.assertEqual(restore_r2.get_object("acc", "label-backup", "label/日本語 の名前/a b.txt", "AKTEST", "SKTEST"), b"hello")


if __name__ == "__main__":
    unittest.main()
