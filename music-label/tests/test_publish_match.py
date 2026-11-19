"""曲の取り込み（publish.py）：ファイルと曲の見分け・名前そろえ・曲のフォルダへの移動・戻す（決定 151〜158・164）"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import time
import types
import unicodedata
import unittest
from pathlib import Path

from _fixture import Project


def quiet(fn, *a, **k):
    """画面への出力を捨てて呼ぶ（出力は戻り値の 2 つ目）"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


class MatchTest(unittest.TestCase):
    """ファイル名から曲を見分ける（match）"""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(ssd=True, debuted=("declan_fay", "hollowplan", "tidewool"))
        p = cls.p
        cls.k_after = p.weekly("hollowplan", p.week(-1), "After Nights")
        cls.k_handed = p.weekly("hollowplan", p.week(-2), "Handed Round")
        cls.k_same_h = p.weekly("hollowplan", p.week(-3), "Same Song")
        cls.k_same_d = p.weekly("declan_fay", p.week(-3), "Same Song")
        cls.k_dup1 = p.weekly("tidewool", p.week(-4), "Low Tide")
        cls.k_dup2 = p.weekly("tidewool", p.week(-2), "Low Tide")
        cls.publish = p.mod("publish")
        cls.songs = [s for s in p.mod("_songs").all_songs() if s.get("title")]

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def m(self, f: Path):
        return self.publish.match(f, self.songs)

    def test_exact_and_suffixes(self):
        for name in ("After Nights.wav", "After Nights (1).mp3", "After Nights v2.wav", "after  nights [suno].m4a", "AFTER NIGHTS_take3.wav"):
            song, why = self.m(Path("/x/Downloads/SUNO楽曲") / name)
            self.assertIsNotNone(song, name)
            self.assertEqual(song["key"], self.k_after, name)

    def test_title_inside_longer_name(self):
        song, _ = self.m(Path("/x/SUNO楽曲/HOLLOWPLAN - Handed Round - final.wav"))
        self.assertEqual(song["key"], self.k_handed)

    def test_fuzzy_typo(self):
        """名前が少し違う：いちばん近い曲がはっきり近いときだけ（決定 152）"""
        song, why = self.m(Path("/x/SUNO楽曲/Handed Roud.wav"))
        self.assertEqual(song["key"], self.k_handed)
        self.assertIn("名前が少し違います", why)

    def test_fuzzy_with_artist_and_date_glued(self):
        song, _ = self.m(Path("/x/SUNO楽曲/AfterNights_HOLLOWPLAN_2026-11-01.wav"))
        self.assertEqual(song["key"], self.k_after)

    def test_unknown_name_is_not_guessed(self):
        song, why = self.m(Path("/x/SUNO楽曲/Completely Different.wav"))
        self.assertIsNone(song)
        self.assertIn("曲名が見つかりません", why)

    def test_song_folder_name_wins(self):
        """曲名のフォルダの中なら、フォルダの名前が曲名（決定 151）"""
        song, _ = self.m(Path("/Volumes/DS72/Postdog Records/02_アーティスト/HOLLOWPLAN/04_楽曲/After Nights/download (3).wav"))
        self.assertEqual(song["key"], self.k_after)

    def test_same_title_two_artists_needs_artist(self):
        song, why = self.m(Path("/x/SUNO楽曲/Same Song.wav"))
        self.assertIsNone(song)
        self.assertIn("どの曲か決められません", why)
        song, _ = self.m(Path("/x/SUNO楽曲/Same Song DECLAN FAY.wav"))
        self.assertEqual(song["key"], self.k_same_d)
        song, _ = self.m(Path("/x/SUNO楽曲/HOLLOWPLAN/Same Song.wav"))
        self.assertEqual(song["key"], self.k_same_h)

    def test_duplicate_title_same_artist_takes_earlier_week(self):
        """同じ組の同じ曲名が 2 つ：まだ取り込んでいない早い週（決定 154）"""
        song, why = self.m(Path("/x/SUNO楽曲/Low Tide.wav"))
        self.assertEqual(song["key"], self.k_dup1)
        self.assertIn("同じ曲名の曲が 2 つ", why)
        self.p.set_state(self.k_dup1, status="imported")
        try:
            song, _ = self.m(Path("/x/SUNO楽曲/Low Tide.wav"))
            self.assertEqual(song["key"], self.k_dup2, "早い週が取り込み済みなら、あとの週")
        finally:
            (self.p.out / "publish" / "state" / f"{self.k_dup1}.json").unlink()


class InboxTest(unittest.TestCase):
    """受け口の中の新しいファイル（new_files）"""

    def setUp(self):
        self.p = Project(ssd=True, debuted=("declan_fay", "hollowplan"))
        self.key = self.p.weekly("hollowplan", self.p.week(-1), "After Nights")
        self.publish = self.p.mod("publish")

    def tearDown(self):
        self.p.close()

    def test_inboxes_are_ssd_then_downloads(self):
        boxes = self.publish.inboxes()
        self.assertEqual(boxes[0], self.p.ssd / "楽曲情報")
        self.assertEqual(boxes[1], self.p.home / "Downloads" / "SUNO楽曲")

    def test_new_files_filters(self):
        p = self.p
        ok1 = p.drop("After Nights.wav", "ssd_inbox")
        ok2 = p.drop("Whatever.mp3", "dl_inbox")
        p.drop("Too Fresh.wav", "dl_inbox", age=2)                      # ダウンロードの途中かもしれない
        p.drop(".hidden.wav", "dl_inbox")
        p.drop("._After Nights.wav", "ssd_inbox")                       # Mac の付き添いのファイル
        p.drop("Old.wav", "dl_inbox", sub="取り込み済み")
        p.drop("Stored.wav", "dl_inbox", sub="ローカル保存（一時保存）/HOLLOWPLAN")
        (p.home / "Downloads" / "SUNO楽曲" / "notes.txt").write_text("x")
        got = {f.name for f in self.publish.new_files()}
        self.assertEqual(got, {ok1.name, ok2.name})

    def test_downloads_top_only_matching_recent(self):
        p = self.p
        p.drop("After Nights.wav", "dl_top")
        p.drop("Some Podcast.mp3", "dl_top")
        p.drop("After Nights (2).wav", "dl_top", age=8 * 86400)         # 7 日より前は見ない
        got = {f.name for f in self.publish.new_files()}
        self.assertEqual(got, {"After Nights.wav"})

    def test_unmatched_file_is_retried(self):
        """見分けられなかったファイル（why の記録）は、次の取り込みでまた試す（決定 152）"""
        f = self.p.drop("Mystery.wav", "dl_inbox")
        reg = {self.publish._sig(f): {"song": None, "why": "曲名が見つかりません", "at": "2026-11-01T00:00:00"}}
        self.publish.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        self.publish.REGISTRY.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        self.assertEqual([x.name for x in self.publish.new_files()], ["Mystery.wav"])

    def test_imported_file_is_not_listed_again(self):
        f = self.p.drop("After Nights.wav", "dl_inbox")
        reg = {self.publish._sig(f): {"song": self.key, "at": "2026-11-01T00:00:00"}}
        self.publish.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        self.publish.REGISTRY.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.publish.new_files(), [])


class TakeInTest(unittest.TestCase):
    """取り込み（take_in）：名前をそろえ、曲のフォルダへ移す"""

    def setUp(self):
        self.p = Project(ssd=True, debuted=("declan_fay", "hollowplan"))
        self.key = self.p.weekly("hollowplan", self.p.week(-1), "After Nights")
        self.publish = self.p.mod("publish")
        self.song = self.p.song(self.key)

    def tearDown(self):
        self.p.close()

    def folder(self) -> Path:
        return self.p.ssd / "02_アーティスト" / "HOLLOWPLAN" / "04_楽曲" / "After Nights"

    def test_take_in_renames_moves_and_records(self):
        f = self.p.drop("After Nights (1).wav", "ssd_inbox")
        self.p.drop("._After Nights (1).wav", "ssd_inbox")
        _, out = quiet(self.publish.take_in, self.song, f)
        day = self.publish.date.today().isoformat()
        stem = f"After Nights_HOLLOWPLAN_{day}"
        self.assertFalse(f.exists(), "受け口には残さない")
        moved = self.folder() / f"{stem}_Suno元データ.wav"
        self.assertTrue(moved.exists(), out)
        self.assertTrue((self.folder() / f"._{stem}_Suno元データ.wav").exists(), "付き添いのファイルも一緒に（決定 153・158）")
        self.assertFalse((self.p.ssd / "02_アーティスト" / "HOLLOWPLAN" / "楽曲情報").exists(), "組ごとの「楽曲情報」は作らない（決定 158）")
        st = self.p.state(self.key)
        self.assertEqual(st["status"], "imported")
        self.assertEqual(st["take"], f"{stem}.wav")
        self.assertTrue((self.p.out / "takes" / self.key / f"{stem}.wav").exists())
        sel = json.loads((self.p.out / "takes" / self.key / "selection.json").read_text(encoding="utf-8"))
        self.assertEqual(sel["decision"]["selected"], f"{stem}.wav")
        self.assertEqual(self.publish.new_files(), [], "曲のフォルダへ移したものを取り込み直さない（決定 151）")

    def test_without_ssd_goes_to_local_song_folder(self):
        self.p.close()
        self.p = Project(ssd=False, debuted=("hollowplan",))
        key = self.p.weekly("hollowplan", self.p.week(-1), "After Nights")
        publish = self.p.mod("publish")
        f = self.p.drop("After Nights.wav", "dl_inbox")
        quiet(publish.take_in, self.p.song(key), f)
        d = self.p.local / "HOLLOWPLAN" / "04_楽曲" / "After Nights"
        self.assertEqual(len([x for x in d.iterdir() if x.name.endswith("_Suno元データ.wav")]), 1)

    def test_suffix_not_doubled_nfd(self):
        """Mac が濁点を分けた形（NFD）で持つ名前でも、「_Suno元データ」を 2 回付けない（決定 164）"""
        nfd = unicodedata.normalize("NFD", "After Nights_HOLLOWPLAN_2026-11-01_Suno元データ.wav")
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        f = self.p.drop(nfd, "ssd_inbox")
        dst, _ = quiet(self.publish.to_song_folder, self.song, f)
        name = unicodedata.normalize("NFC", dst.name)
        self.assertEqual(name.count("_Suno元データ"), 1, name)
        self.assertEqual(dst.parent, self.folder())

    def test_same_content_already_there_is_only_cleaned(self):
        self.folder().mkdir(parents=True)
        have = self.p.wav(self.folder() / "After Nights_HOLLOWPLAN_2026-11-01_Suno元データ.wav", sec=0.3, freq=330.0)
        f = self.p.drop("After Nights_HOLLOWPLAN_2026-11-01.wav", "ssd_inbox")
        dst, _ = quiet(self.publish.to_song_folder, self.song, f)
        self.assertEqual(dst, have)
        self.assertFalse(f.exists())
        self.assertEqual(len(list(self.folder().glob("*.wav"))), 1)

    def test_different_content_same_name_gets_number(self):
        self.folder().mkdir(parents=True)
        self.p.wav(self.folder() / "After Nights_HOLLOWPLAN_2026-11-01_Suno元データ.wav", sec=0.3, freq=999.0)
        f = self.p.drop("After Nights_HOLLOWPLAN_2026-11-01.wav", "ssd_inbox")
        dst, _ = quiet(self.publish.to_song_folder, self.song, f)
        self.assertTrue(dst.name.endswith("_Suno元データ_2.wav"), dst.name)

    def test_already_in_song_folder_is_not_moved(self):
        self.folder().mkdir(parents=True)
        f = self.p.wav(self.folder() / "x.wav", sec=0.2)
        dst, _ = quiet(self.publish.to_song_folder, self.song, f)
        self.assertEqual(dst, f)


class ScanPickTest(unittest.TestCase):
    """取り込み（scan）の選び方：同じ曲に 2 本なら新しい方・手で選んだテイクは差し替えない（決定 154・155）"""

    def setUp(self):
        self.p = Project(ssd=True, debuted=("declan_fay", "hollowplan"))
        self.key = self.p.weekly("hollowplan", self.p.week(-1), "After Nights")
        self.publish = self.p.mod("publish")
        import sys
        stub = types.ModuleType("take_check")   # 作曲の質の確認（音の解析）は、ここでは通ったことにする
        stub.gate = lambda song: "ok"
        sys.modules["take_check"] = stub
        self.taken: list[tuple[str, str]] = []
        self.publish.take_in = lambda song, f: self.taken.append((song["key"], f.name))
        self.publish.advance = lambda song: ""
        self.publish.store_set = lambda song: None
        self.publish._book = lambda: None
        self.args = argparse.Namespace(quiet=True, no_open=True)

    def tearDown(self):
        self.p.close()

    def test_newest_of_two_wins(self):
        self.p.drop("After Nights.wav", "dl_inbox", age=600)
        self.p.drop("After Nights (1).wav", "dl_inbox", age=60)
        quiet(self.publish.cmd_scan, self.args)
        self.assertEqual(self.taken, [(self.key, "After Nights (1).wav")])

    def test_manual_take_is_protected(self):
        self.p.set_state(self.key, status="imported", manual_take=True)
        self.p.drop("After Nights (3).wav", "dl_inbox")
        _, out = quiet(self.publish.cmd_scan, self.args)
        self.assertEqual(self.taken, [])
        self.assertIn("差し替えません", out)

    def test_unmatched_is_recorded_with_reason(self):
        f = self.p.drop("Mystery.wav", "dl_inbox")
        _, out = quiet(self.publish.cmd_scan, self.args)
        reg = json.loads(self.publish.REGISTRY.read_text(encoding="utf-8"))
        self.assertIsNone(reg[self.publish._sig(f)]["song"])
        self.assertIn("why", reg[self.publish._sig(f)])
        self.assertIn("？ Mystery.wav", out)

    def test_take_command_marks_manual(self):
        f = self.p.drop("zzz.wav", "dl_top")
        self.publish.take_in = lambda song, ff: (self.taken.append((song["key"], ff.name)),
                                                 self.p.set_state(song["key"], status="imported"))[0]
        rc, out = quiet(self.publish.cmd_take, argparse.Namespace(file=str(f), song=self.key))
        self.assertEqual(self.taken, [(self.key, "zzz.wav")])
        self.assertTrue(self.p.state(self.key).get("manual_take"), out)

    def test_take_command_uses_real_file_for_appledouble(self):
        f = self.p.drop("After Nights.wav", "dl_top")
        self.publish.take_in = lambda song, ff: (self.taken.append((song["key"], ff.name)),
                                                 self.p.set_state(song["key"], status="imported"))[0]
        quiet(self.publish.cmd_take, argparse.Namespace(file=str(f.with_name("._" + f.name)), song=""))
        self.assertEqual(self.taken, [(self.key, "After Nights.wav")])

    def test_take_command_missing_file(self):
        rc, out = quiet(self.publish.cmd_take, argparse.Namespace(file=str(self.p.home / "nope.wav"), song=""))
        self.assertEqual(rc, 2)
        self.assertIn("ファイルが見つかりません", out)


class GoBackTest(unittest.TestCase):
    """間違えて進めた工程を戻す（go_back。決定 38・124・155）"""

    def setUp(self):
        self.p = Project(ssd=False, debuted=("declan_fay", "hollowplan"))
        self.publish = self.p.mod("publish")
        self.publish._book = lambda: None

    def tearDown(self):
        self.p.close()

    def test_options_by_stage(self):
        p = self.p
        k_ready = p.weekly("hollowplan", p.week(-1), "A", "ready")
        k_reg = p.weekly("hollowplan", p.week(-2), "B", "registered")
        k_live = p.weekly("hollowplan", p.week(-3), "C", "live")
        k_imp = p.weekly("hollowplan", p.week(-4), "D", "imported")
        k_new = p.weekly("hollowplan", p.week(-5), "E", "brief")
        bo = self.publish.back_options
        self.assertEqual(bo(p.song(k_ready)), ["cover", "suno", "master"])
        self.assertEqual(bo(p.song(k_reg)), ["ready", "suno"])
        self.assertEqual(bo(p.song(k_live)), ["registered", "ready", "suno"])
        self.assertEqual(bo(p.song(k_imp)), ["suno", "master"])
        self.assertEqual(bo(p.song(k_new)), [])
        with self.assertRaises(ValueError):
            self.publish.go_back(p.song(k_new))
        with self.assertRaises(ValueError):
            self.publish.go_back(p.song(k_imp), "ready")

    def test_debut_has_no_cover_step(self):
        k = self.p.debut("tidewool", "The Low Part", "ready")
        self.assertNotIn("cover", self.publish.back_options(self.p.song(k)))

    def _back_to_suno(self, stage: str):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "After Nights", stage)
        self.publish.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        self.publish.REGISTRY.write_text(json.dumps({"/x/a.wav|1|2": {"song": k, "at": "t"}}), encoding="utf-8")
        quiet(self.publish.go_back, p.song(k), "suno")
        st = p.state(k)
        self.assertEqual(st["status"], "")
        for gone in ("take", "source", "imported_at", "ready_at"):
            self.assertNotIn(gone, st)
        self.assertFalse((p.out / "takes" / k).exists())
        self.assertFalse((p.out / "masters" / k).exists())
        self.assertFalse((p.out / "publish" / k).exists())
        undo = list((p.out / "publish" / "undo").glob(f"{k}_*"))
        self.assertEqual(len(undo), 1, "消さずに「戻したもの」へ")
        self.assertTrue(any(undo[0].rglob("selection.json")), "選んだテイクの記録も残っている")
        reg = json.loads(self.publish.REGISTRY.read_text(encoding="utf-8"))
        self.assertIn("undone", reg["/x/a.wav|1|2"], "自動でまた取り込まない印")
        self.assertEqual(st["history"][-1]["to"], "suno")

    def test_back_to_suno_from_imported(self):
        self._back_to_suno("imported")

    def test_back_to_suno_from_ready(self):
        """【不具合】登録の準備ができた曲を Suno で作曲に戻すと、途中で落ちる（publish.py の _stash）。
        out/takes/<キー>・out/masters/<キー>・out/publish/<キー> は同じ名前のフォルダなので、3 つ目を「戻したもの」へ移すときに
        shutil.Error（Destination path … already exists）になる。テイクと音源は移ったあとで、進み具合は「登録の準備ができた」のまま残る"""
        self._back_to_suno("ready")

    def test_back_to_master_keeps_take(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "After Nights", "ready")
        quiet(self.publish.go_back, p.song(k), "master")
        self.assertEqual(p.state(k)["status"], "imported")
        self.assertTrue((p.out / "takes" / k / "selection.json").exists())
        sel = json.loads((p.out / "takes" / k / "selection.json").read_text(encoding="utf-8"))
        self.assertNotIn("master", sel)
        self.assertFalse((p.out / "masters" / k).exists())

    def test_back_from_registered_clears_release(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "After Nights", "registered")
        quiet(self.publish.go_back, p.song(k))
        st = p.state(k)
        self.assertEqual(st["status"], "ready")
        self.assertNotIn("release_date", st)
        self.assertNotIn("registered_at", st)

    def test_back_to_cover_waits_for_owner_pick(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "After Nights", "ready")
        try:
            quiet(self.publish.go_back, p.song(k), "cover")
        except ImportError as e:   # ジャケットのシーンの部品が要る部品を持たない環境
            self.skipTest(str(e))
        self.assertEqual(p.state(k)["status"], "waiting_cover")
        cd = p.out / "visuals" / "hollowplan" / "cover" / p.week(-1)
        self.assertFalse((cd / "cover_final.jpg").exists())
        self.assertTrue((cd / "owner_pick.json").exists(), "作り直した候補は自動で選ばない（決定 123）")

    def test_back_to_draft_only_for_irregular(self):
        """曲の案に戻す（決定 155）：案から作った曲だけ。取り込み済みなら先に取り込みも取り消す"""
        p = self.p
        k = p.irregular("hollowplan", p.week(-1), "Side Street", "imported")
        self.assertIn("draft", self.publish.back_options(p.song(k)))
        msg, _ = quiet(self.publish.go_back, p.song(k), "draft")
        self.assertFalse((p.out / "briefs" / f"{k}.json").exists(), "作曲用のブリーフは「戻したもの」へ")
        self.assertTrue(list((p.out / "publish" / "undo").glob(f"{k}_*")))
        drafts = [json.loads(x.read_text(encoding="utf-8")) for x in (p.out / "irregular" / "drafts").glob("*.json")]
        self.assertEqual(drafts[0]["irregular"]["status"], "proposed")
        self.assertNotIn("song_id", drafts[0]["irregular"])

    def test_no_draft_after_registered(self):
        p = self.p
        k = p.irregular("hollowplan", p.week(-1), "Side Street", "registered")
        self.assertNotIn("draft", self.publish.back_options(p.song(k)))


if __name__ == "__main__":
    unittest.main()
