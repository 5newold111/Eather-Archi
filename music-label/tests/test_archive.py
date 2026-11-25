"""保管の置き場所（_archive.py）：曲のフォルダは 04_楽曲/<曲名>/・組ごとの「楽曲情報」は作らない（決定 151・158）"""
from __future__ import annotations

import unicodedata
import unittest

from _fixture import Project


class SongFolderTest(unittest.TestCase):
    def setUp(self):
        self.p = Project(ssd=True)
        self.a = self.p.mod("_archive")

    def tearDown(self):
        self.p.close()

    def test_song_folder_on_ssd(self):
        d = self.a.song_folder("HOLLOWPLAN", "After Nights")
        self.assertEqual(d, self.p.ssd / "02_アーティスト" / "HOLLOWPLAN" / "04_楽曲" / "After Nights")
        self.assertTrue(d.is_dir())
        self.assertFalse((self.p.ssd / "02_アーティスト" / "HOLLOWPLAN" / "楽曲情報").exists())

    def test_song_folders_lists_only_existing_ssd_first(self):
        self.assertEqual(self.a.song_folders("HOLLOWPLAN", "After Nights"), [], "作らない")
        loc = self.a.local_folder("HOLLOWPLAN", "music", "After Nights")
        self.assertEqual(self.a.song_folders("HOLLOWPLAN", "After Nights"), [loc])
        ssd = self.a.song_folder("HOLLOWPLAN", "After Nights")
        self.assertEqual(self.a.song_folders("HOLLOWPLAN", "After Nights"), [ssd, loc])

    def test_same_name_reuses_existing_folder(self):
        """全角・大文字小文字・前後の空白が違うだけの同じ名前のフォルダは、作り直さず使う"""
        made = self.p.ssd / "02_アーティスト" / "hollowplan " / "04_楽曲"
        made.mkdir(parents=True)
        d = self.a.song_folder("HOLLOWPLAN", "After Nights")
        self.assertEqual(d.parent, made)
        self.assertEqual(len(list((self.p.ssd / "02_アーティスト").iterdir())), 1)

    def test_root_creates_fixed_tops_and_inbox(self):
        base, _ = self.a.root()
        self.assertEqual(base, self.p.ssd)
        self.assertTrue((base / "楽曲情報").is_dir(), "いちばん上の受け口（Suno のダウンロード先）は残す")
        self.assertTrue((base / "09_移行前").is_dir())

    def test_numbered_and_save(self):
        folder = self.a.local_folder("HOLLOWPLAN", "cover")
        a = self.p.jpg(self.p.tmp / "a.jpg", color=(1, 2, 3))
        b = self.p.jpg(self.p.tmp / "b.jpg", color=(9, 9, 9))
        n1 = self.a.numbered(folder, "HOLLOWPLAN_After Nights", a)
        self.assertEqual(n1, "HOLLOWPLAN_After Nights_1.jpg")
        self.assertEqual(self.a.save(a, folder, n1), "保管")
        self.assertEqual(self.a.numbered(folder, "HOLLOWPLAN_After Nights", a), n1, "同じ中身なら同じ番号")
        self.assertEqual(self.a.numbered(folder, "HOLLOWPLAN_After Nights", b), "HOLLOWPLAN_After Nights_2.jpg")
        self.assertIn("_old", self.a.save(b, folder, n1))
        self.assertTrue((folder / "HOLLOWPLAN_After Nights_1_old.jpg").exists(), "前の版は消さない")


class NoSsdTest(unittest.TestCase):
    def setUp(self):
        self.p = Project(ssd=False)
        self.a = self.p.mod("_archive")

    def tearDown(self):
        self.p.close()

    def test_falls_back_to_local(self):
        base, why = self.a.root()
        self.assertIsNone(base)
        self.assertIn("SSD", why)
        d = self.a.song_folder("HOLLOWPLAN", "After Nights")
        self.assertEqual(d, self.p.local / "HOLLOWPLAN" / "04_楽曲" / "After Nights")


class MergeInboxTest(unittest.TestCase):
    """前の作り（<組>/楽曲情報/<曲名>/・その中の 配信用/）を 04_楽曲/<曲名>/ にまとめる（決定 158）"""

    def setUp(self):
        self.p = Project(ssd=True)
        self.a = self.p.mod("_archive")
        self.top = self.p.ssd / "02_アーティスト"

    def tearDown(self):
        self.p.close()

    def test_merge_moves_renames_and_removes_empty_inbox(self):
        old = self.top / "HOLLOWPLAN" / "楽曲情報" / "After Nights"
        take = self.p.wav(old / "After Nights_HOLLOWPLAN_2026-11-01.wav", sec=0.2)
        (old / "._After Nights_HOLLOWPLAN_2026-11-01.wav").write_bytes(b"x")
        master = self.p.wav(old / "配信用" / "After Nights_HOLLOWPLAN_2026-11-01.wav", sec=0.2, freq=880)
        (old.parent / ".DS_Store").write_bytes(b"x")
        keep = self.p.wav(self.top / "HOLLOWPLAN" / "04_楽曲" / "After Nights" / "歌詞.txt", sec=0.1)   # 04_楽曲 側に前からあるもの
        self.a._merge_artist_inboxes(self.top)
        new = self.top / "HOLLOWPLAN" / "04_楽曲" / "After Nights"
        names = {unicodedata.normalize("NFC", f.name) for f in new.iterdir()}
        self.assertIn("After Nights_HOLLOWPLAN_2026-11-01_Suno元データ.wav", names, "ダウンロードした曲は「_Suno元データ」の名前に")
        self.assertIn("._After Nights_HOLLOWPLAN_2026-11-01_Suno元データ.wav", names)
        self.assertIn("After Nights_HOLLOWPLAN_2026-11-01.wav", names, "配信用の音源は元の名前のまま")
        self.assertIn("歌詞.txt", names)
        self.assertFalse((self.top / "HOLLOWPLAN" / "楽曲情報").exists(), "空になった「楽曲情報」は消す")
        self.assertTrue(keep.exists())

    def test_merge_keeps_existing_file_on_conflict(self):
        old = self.top / "HOLLOWPLAN" / "楽曲情報" / "After Nights"
        self.p.wav(old / "配信用" / "master.wav", sec=0.2, freq=111)
        have = self.p.wav(self.top / "HOLLOWPLAN" / "04_楽曲" / "After Nights" / "master.wav", sec=0.2, freq=999)
        size = have.read_bytes()
        self.a._merge_artist_inboxes(self.top)
        self.assertEqual(have.read_bytes(), size, "同じ名前なら 04_楽曲 側を残す")

    def test_merge_is_idempotent_and_ignores_top_inbox(self):
        base, _ = self.a.root()
        self.p.wav(base / "楽曲情報" / "New Download.wav", sec=0.1)
        self.a._merge_artist_inboxes(self.top)
        self.a._merge_artist_inboxes(self.top)
        self.assertTrue((base / "楽曲情報" / "New Download.wav").exists(), "いちばん上の受け口の中身には触らない")


if __name__ == "__main__":
    unittest.main()
