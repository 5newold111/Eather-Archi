"""所属アーティストを外す・元に戻す（retire.py。決定 166）"""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import date, timedelta

from _fixture import Project


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


class RetireFlowTest(unittest.TestCase):
    def setUp(self):
        self.p = p = Project(ssd=False, debuted=("declan_fay", "hollowplan"))
        self.k_live = p.weekly("hollowplan", p.week(-6), "Out Already", stage="live")
        self.k_ready = p.weekly("hollowplan", p.week(-2), "Almost There", stage="ready")       # 出し切る
        self.k_imp = p.weekly("hollowplan", p.week(-1), "Half Done", stage="mastered")         # 止める
        self.k_new = p.weekly("hollowplan", p.week(0), "Not Started", stage="brief")           # 止める
        self.r = p.mod("retire")
        self.tpl = p.root / "templates" / "artists" / "hollowplan.json"
        self.retired = p.root / "templates" / "archive" / "retired" / "hollowplan.json"

    def tearDown(self):
        self.p.close()

    def test_impact_lists_released_and_doing(self):
        imp = self.r.impact("hollowplan")
        self.assertEqual([x["id"] for x in imp["released"]], [self.k_live])
        self.assertEqual({x["id"] for x in imp["doing"]}, {self.k_ready, self.k_imp, self.k_new})
        self.assertEqual(self.r.impact("nobody"), {"error": "その組が見つかりません"})

    def test_retire_keep_then_tick_then_restore(self):
        p = self.p
        rc, out = quiet(self.r.retire, "hollowplan", [self.k_ready], "試験", False)
        self.assertEqual(rc, 0, out)
        a = json.loads(self.tpl.read_text(encoding="utf-8"))
        self.assertEqual(a["retire"]["status"], "retiring")
        self.assertEqual(a["retire"]["keep"], [self.k_ready])
        self.assertEqual(set(a["retire"]["stopped"]), {self.k_imp, self.k_new})
        self.assertEqual(a["retire"]["restore_until"], (date.today() + timedelta(days=30)).isoformat())
        # 止めた曲：作業ファイルは消さずに「戻したもの」へ
        self.assertFalse((p.out / "briefs" / f"{self.k_new}.json").exists())
        self.assertFalse((p.out / "takes" / self.k_imp).exists())
        undo = p.out / "publish" / "undo"
        self.assertTrue(any(undo.glob(f"{self.k_new}_*_活動終了")))
        self.assertTrue(any(undo.glob(f"{self.k_imp}_*_活動終了")))
        self.assertTrue(any(x.name.startswith("state_") for d in undo.glob(f"{self.k_imp}_*") for x in d.iterdir()))
        # 配信中の曲・出し切る曲はそのまま
        self.assertEqual(p.state(self.k_live)["status"], "live")
        self.assertTrue((p.out / "briefs" / f"{self.k_ready}.json").exists())
        # これからの制作から外れる
        p.reload()
        c = p.mod("_common")
        self.assertNotIn("hollowplan", [x["slug"] for x in c.all_artists()])
        r = p.mod("retire")
        self.assertNotIn("hollowplan", [x["slug"] for x in r.active()])
        self.assertEqual([(x["slug"], x["status"]) for x in r.listing()], [("hollowplan", "retiring")])
        # 2 回は外せない
        rc, _ = quiet(r.retire, "hollowplan", [], "", False)
        self.assertEqual(rc, 1)
        # 出し切る曲がまだ：点検しても終わらない
        quiet(r.tick)
        self.assertTrue(self.tpl.exists())
        # 出し切る曲を登録 → 点検で設定書を保管庫へ
        p.set_state(self.k_ready, status="registered", release_date=(date.today() + timedelta(days=10)).isoformat())
        quiet(r.tick)
        self.assertFalse(self.tpl.exists())
        self.assertTrue(self.retired.exists())
        p.reload()
        c = p.mod("_common")
        self.assertEqual(c.load_artist("hollowplan")["name"], "HOLLOWPLAN", "保管庫からも読める（曲の記録・配信の確認のため）")
        self.assertIn(self.k_ready, [s["key"] for s in p.mod("_songs").weekly_songs()])
        r = p.mod("retire")
        self.assertEqual([(x["slug"], x["status"], x["can_restore"]) for x in r.listing()], [("hollowplan", "retired", True)])
        # 元に戻す
        rc, out = quiet(r.restore, "hollowplan")
        self.assertEqual(rc, 0, out)
        a = json.loads(self.tpl.read_text(encoding="utf-8"))
        self.assertNotIn("retire", a)
        self.assertEqual(len(a["retire_history"]), 1)
        self.assertFalse(self.retired.exists())
        p.reload()
        self.assertIn("hollowplan", [x["slug"] for x in p.mod("_common").all_artists()])
        self.assertTrue(any(undo.glob(f"{self.k_new}_*_活動終了")), "止めた曲は自動では戻さない")

    def test_retire_without_keep_finalizes_at_once(self):
        # 途中の曲は 3 つとも止める（登録の準備ができた曲を止めると落ちる不具合は、下の test_stop_ready_song を参照）
        (self.p.out / "publish" / self.k_ready / "DistroKid下書き.md").unlink()
        import shutil
        shutil.rmtree(self.p.out / "publish" / self.k_ready)
        rc, out = quiet(self.r.retire, "hollowplan", [], "", False)
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.tpl.exists())
        a = json.loads(self.retired.read_text(encoding="utf-8"))
        self.assertEqual(a["retire"]["status"], "retired")

    def test_restore_after_limit_is_refused(self):
        quiet(self.r.retire, "hollowplan", [self.k_ready], "", False)
        a = json.loads(self.tpl.read_text(encoding="utf-8"))
        a["retire"]["restore_until"] = (date.today() - timedelta(days=1)).isoformat()
        self.tpl.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        rc, out = quiet(self.r.restore, "hollowplan")
        self.assertEqual(rc, 2)
        self.assertIn("期間", out)
        self.assertIn("retire", json.loads(self.tpl.read_text(encoding="utf-8")))

    def test_restore_unknown(self):
        rc, _ = quiet(self.r.restore, "declan_fay")
        self.assertEqual(rc, 1)

    def test_stop_debut_song_marks_stopped(self):
        k = self.p.debut("tidewool", "The Low Part", "imported")
        r = self.p.mod("retire")
        quiet(r.stop_song, self.p.song(k))
        self.assertEqual(self.p.state(k)["status"], "stopped")
        self.assertNotIn("tidewool", [s["slug"] for s in self.p.mod("_songs").debut_songs()])
        self.assertTrue((self.p.root / "debut_kit").exists(), "作業セットは消さない")

    def test_stop_ready_song(self):
        """【不具合】登録の準備ができた曲を「止める」と途中で落ちる（retire.py の _stash。publish.py の _stash と同じ原因）。
        out/takes/<キー>・out/masters/<キー>・out/publish/<キー> が同じ名前のフォルダなので、3 つ目を移すときに shutil.Error になる"""
        quiet(self.r.stop_song, self.p.song(self.k_ready))
        self.assertFalse((self.p.out / "publish" / self.k_ready).exists())


if __name__ == "__main__":
    unittest.main()
