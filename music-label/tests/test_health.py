"""毎日の自動点検と自動の直し（health.py。決定 173）"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from _fixture import STAGES, Project


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


class Base(unittest.TestCase):
    ssd = False

    def setUp(self):
        self.p = Project(ssd=self.ssd, debuted=("declan_fay", "hollowplan"))
        self.h = self.p.mod("health")
        self.ran: list[list[str]] = []
        self.h._run = lambda cmd, timeout=0: (self.ran.append([Path(cmd[1]).name, *cmd[2:]]), (0, ""))[1]   # 別のプロセスは動かさず、命令だけ記録

    def tearDown(self):
        self.p.close()

    def ids(self, **kw) -> dict[str, dict]:
        found, _ = quiet(self.h.check, **kw)
        return {f["id"]: f for f in found}

    def heal(self, **kw) -> dict:
        r, self.out = quiet(self.h.heal, **kw)
        return r

    def ago(self, **kw) -> str:
        return (datetime.now() - timedelta(**kw)).isoformat(timespec="seconds")


class CleanTest(Base):
    def test_consistent_project_has_no_findings(self):
        p = self.p
        weeks = {"brief": 0, "ready": -1, "imported": -2, "mastered": -3, "registered": -4, "live": -5}   # 登録待ちの曲は、配信日がまだ先の週に
        for st in STAGES:
            p.weekly("hollowplan", p.week(weeks[st]), f"Song {st}", stage=st)
        p.debut("tidewool", "The Low Part", "imported")
        self.assertEqual(self.ids(), {})
        s = self.heal()
        self.assertEqual((s["needs_owner"], s["auto_pending"], s["worst"]), ([], [], "ok"))
        self.assertEqual(s["ok"], s["checks"])
        self.assertEqual(self.ran, [], "直すものが無ければ何も動かさない")
        self.assertIn("食い違いはありません", self.out)

    def test_status_before_first_check(self):
        s = self.h.status()
        self.assertTrue(s["stale"])
        self.assertEqual((s["needs_owner"], s["auto_fixed_today"]), ([], []))
        r = self.p.run("health.py", "report")
        self.assertIn("まだ点検していません", r.stdout)

    def test_cli_check_json(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        (self.p.out / "publish" / k / "DistroKid下書き.md").unlink()
        r = self.p.run("health.py", "check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        found = json.loads(r.stdout)
        self.assertEqual([f["id"] for f in found], [f"draft:{k}"])
        self.assertTrue((self.p.out / "health").exists() is False, "check は何も書かない")


class SongFilesTest(Base):
    """記録とファイルの食い違い"""

    def test_take_missing_without_source_is_owner(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="imported")
        shutil.rmtree(self.p.out / "takes" / k)
        f = self.ids()[f"take:{k}"]
        self.assertEqual((f["sev"], f["fix"]), ("ng", "owner"))
        self.assertEqual(f["action"]["action"], "back")
        self.assertEqual(f["action"]["to"], "suno")

    def test_take_missing_with_source_is_copied_back(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="mastered")
        take = Path(self.p.state(k)["source"])
        src = self.p.wav(self.p.local / "HOLLOWPLAN" / "04_楽曲" / "A" / "A_HOLLOWPLAN_x_Suno元データ.wav", sec=0.3, freq=555)
        self.p.set_state(k, source=str(src))
        take.unlink()
        self.assertEqual(self.ids()[f"take:{k}"]["fix"], "auto")
        s = self.heal()
        self.assertTrue(take.exists())
        self.assertEqual(take.read_bytes(), src.read_bytes())
        self.assertTrue(src.exists(), "元データは動かさない")
        self.assertEqual(s["fixed_now"], [f"HOLLOWPLAN「A」：選んだテイクのファイルがありません"])
        self.assertEqual(self.ids(), {})

    def test_ready_without_master_is_remastered(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        (self.p.out / "masters" / k / "master.wav").unlink()
        self.assertEqual(self.ids()[f"master:{k}"]["heal"], "remaster")
        self.heal()
        self.assertEqual(self.ran, [["publish.py", "back", k, "--to", "master"], ["schedule.py", "run", "intake"]])

    def test_quiet_heal_with_something_to_fix(self):
        """定期実行は --quiet で動く。直す物があるときに表示の関数で落ちていた（実機の 11/22 の失敗。決定 180）"""
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        (self.p.out / "masters" / k / "master.wav").unlink()
        self.heal(quiet=True)
        self.assertIn(["publish.py", "back", k, "--to", "master"], self.ran)

    def test_waiting_cover_without_master_runs_intake(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="mastered")
        (self.p.out / "masters" / k / "master.wav").unlink()
        self.assertEqual(self.ids()[f"master:{k}"]["heal"], "intake")

    def test_ready_without_cover_is_owner(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        (self.p.out / "visuals" / "hollowplan" / "cover" / self.p.week(-1) / "cover_final.jpg").unlink()
        f = self.ids()[f"cover:{k}"]
        self.assertEqual((f["sev"], f["fix"], f["action"]["action"]), ("ng", "owner", "cover"))

    def test_missing_draft_is_rebuilt(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        d = self.p.out / "publish" / k / "DistroKid下書き.md"
        d.unlink()
        self.heal()
        self.assertTrue(d.exists())
        self.assertIn("Song title", d.read_text(encoding="utf-8"))
        self.assertEqual(self.ids(), {})

    def test_missing_public_set_runs_archive_for_that_act_only(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        shutil.rmtree(self.p.local / "HOLLOWPLAN" / "04_楽曲" / "A")
        self.assertIn(f"set:{k}", self.ids())
        self.heal()
        self.assertEqual(self.ran, [["archive_sync.py", "--only", "hollowplan"]])

    def test_stale_staging_is_restaged(self):
        """DistroKid に渡す写しが、いまの音源と違う（音量を整え直したあとなど）"""
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        bf = self.p.mod("browser_fill")
        song = self.p.song(k)
        (audio, cover, _), _ = quiet(bf.upload_files, song)
        self.assertEqual(self.ids(), {})
        master = self.p.wav(self.p.out / "masters" / k / "master.wav", sec=1.0, freq=999)     # 音源が新しくなった
        self.assertEqual(self.ids()[f"stage:{k}"]["heal"], "restage")
        self.heal()
        self.assertEqual(audio.read_bytes(), master.read_bytes())
        self.assertNotIn(f"stage:{k}", self.ids())

    def test_song_without_title(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "", stage="brief", title_candidates=[])
        self.p.set_state(k, status="imported", imported_at=self.ago(minutes=1))
        self.assertIn(f"title:{k}", self.ids())


class StuckTest(Base):
    """同じ段階で止まっている"""

    def test_imported_long_ago_runs_intake_once(self):
        p = self.p
        k1 = p.weekly("hollowplan", p.week(-1), "A", stage="imported")
        k2 = p.weekly("hollowplan", p.week(-2), "B", stage="imported")
        p.set_state(k1, imported_at=self.ago(hours=2))
        p.set_state(k2, imported_at=self.ago(hours=3))
        self.assertEqual({f["heal"] for f in self.ids().values()}, {"intake"})
        self.heal()
        self.assertEqual(self.ran, [["schedule.py", "run", "intake"]], "何曲あっても取り込みの仕事は 1 回")

    def test_fresh_import_is_left_alone(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="imported")
        self.p.set_state(k, imported_at=self.ago(minutes=5))
        self.assertEqual(self.ids(), {})

    def test_ready_waits_for_listening_then_registration(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(1), "A", stage="ready", days_ago=5)
        try:
            listen = p.mod("listen")
        except ImportError:
            self.skipTest("聴いて確かめる部品（listen.py）が無い版")
        f = self.ids()[f"listen:{k}"]
        self.assertEqual((f["fix"], f["action"]["url"]), ("owner", f"/listen?id={k}"))
        listen.approve(p.song(k))
        f = self.ids()[f"register:{k}"]
        self.assertEqual(f["action"]["action"], "fill_distrokid")

    def test_waiting_for_suno(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-3), "A", stage="brief")
        b = p.out / "briefs" / f"{k}.json"
        t = time.time() - 10 * 86400
        os.utime(b, (t, t))
        f = self.ids()[f"suno:{k}"]
        self.assertEqual(f["action"]["action"], "fill_suno")
        d = json.loads(b.read_text(encoding="utf-8"))
        d["release_postponed"] = {"why": "Suno の枠"}
        b.write_text(json.dumps(d), encoding="utf-8")
        os.utime(b, (t, t))
        self.assertNotIn(f"suno:{k}", self.ids(), "Suno の枠に合わせて後ろへずらした曲は、待っていて正しい")

    def test_registered_without_date_and_not_live(self):
        p = self.p
        k1 = p.weekly("hollowplan", p.week(-5), "A", stage="registered")
        k2 = p.weekly("hollowplan", p.week(-6), "B", stage="registered")
        p.set_state(k1, release_date=None)
        p.set_state(k2, release_date=(date.today() - timedelta(days=5)).isoformat())
        got = self.ids()
        self.assertEqual(got[f"reldate:{k1}"]["fix"], "owner")
        self.assertEqual(got[f"notlive:{k2}"]["heal"], "release")
        s = self.heal()
        self.assertEqual(self.ran, [["schedule.py", "run", "release"]])
        owner = {f["id"]: f for f in s["needs_owner"]}
        self.assertIn(f"notlive:{k2}", owner, "試しても直らなければ、あなたの番に回す")
        self.assertIn("自動では直りませんでした", owner[f"notlive:{k2}"]["why"])
        self.ran.clear()
        self.heal()
        self.assertEqual(self.ran, [], "同じ直しは 1 日 1 回だけ")


class WholeTest(Base):
    ssd = True

    def test_duplicate_titles(self):
        p = self.p
        k1 = p.weekly("hollowplan", p.week(-2), "Low Tide")
        p.weekly("hollowplan", p.week(-1), "low  tide")
        p.weekly("declan_fay", p.week(-1), "Low Tide")          # 別の組の同じ曲名はよい
        got = self.ids()
        self.assertEqual(list(got), [f"dup:hollowplan:{k1}"])
        self.assertEqual(got[f"dup:hollowplan:{k1}"]["fix"], "owner")

    def test_inbox_known_and_unknown(self):
        p = self.p
        p.weekly("hollowplan", p.week(-1), "After Nights")
        p.drop("After Nights.wav", "ssd_inbox", age=3600)
        p.drop("Mystery Tune.wav", "dl_inbox", age=3600)
        got = self.ids()
        self.assertEqual(got["inbox:known"]["heal"], "intake")
        f = got["inbox:Mystery Tune.wav"]
        self.assertEqual(f["fix"], "owner")
        self.assertIn("publish.py take", f["why"])

    def test_inbox_fresh_file_waits_for_normal_intake(self):
        self.p.weekly("hollowplan", self.p.week(-1), "After Nights")
        self.p.drop("After Nights.wav", "ssd_inbox", age=120)
        self.assertEqual(self.ids(), {})

    def test_orphan_state_is_only_a_note(self):
        self.p.set_state("2026-01-05_hollowplan", status="ready")
        f = self.ids()["orphan:2026-01-05_hollowplan"]
        self.assertEqual((f["sev"], f["fix"]), ("info", "none"))

    def log(self, job: str, codes: list[int]) -> None:
        s = self.p.mod("schedule")
        lines = [f"\n===== {datetime.now():%Y-%m-%d %H:%M} {job}：試験 ====="]
        for i, c in enumerate(codes):
            lines += [f"▷ 手順 {i}：{' '.join(s.JOBS[job][1][i])}", f"→ 終了コード {c}"]
        f = self.p.out / "logs" / f"{job}.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_failed_jobs(self):
        self.log("intake", [1, 0])
        self.log("brief", [0, 1])
        self.log("post", [1])
        self.log("release", [0])
        got = self.ids()
        self.assertEqual((got["job:intake"]["fix"], got["job:intake"]["args"]), ("auto", {"job": "intake", "steps": [0]}))
        self.assertEqual(got["job:brief"]["fix"], "none", "週 1 の仕事は、毎日 12:05 のやり直しに任せる（決定 113）")
        self.assertEqual(got["job:post"]["fix"], "owner", "SNS の投稿は、自動ではやり直さない")
        self.assertNotIn("job:release", got)
        calls = []
        self.h.FIXERS["rerun"] = lambda job, steps, **_: (calls.append((job, steps)), (True, "やり直しました"))[1]
        self.heal()
        self.assertEqual(calls, [("intake", [0])])

    def test_half_entered_keys(self):
        os.environ["NOTION_TOKEN"] = "ntn_x"
        try:
            f = self.ids()["keys:notion"]
            self.assertEqual((f["fix"], f["action"]), ("owner", {"action": "terminal", "key": "notion", "label": "Notion の鍵を入れる（ターミナルが開きます）"}))
        finally:
            os.environ.pop("NOTION_TOKEN", None)

    def test_quota_note(self):
        q = self.p.mod("suno_quota")
        q.status = lambda today=None: {"on": True, "left": 0, "waiting": 3, "reset": "2026-12-04"}
        self.assertEqual(self.ids()["quota"]["fix"], "none")


class HealRecordTest(Base):
    def test_log_last_and_status(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "A", stage="ready")
        k2 = p.weekly("hollowplan", p.week(-2), "B", stage="ready")
        (p.out / "publish" / k / "DistroKid下書き.md").unlink()
        (p.out / "visuals" / "hollowplan" / "cover" / p.week(-2) / "cover_final.jpg").unlink()
        s = self.heal()
        self.assertEqual(len(s["fixed_now"]), 1)
        self.assertEqual([f["id"] for f in s["needs_owner"]], [f"cover:{k2}"])
        self.assertEqual(s["worst"], "ng")
        for f in s["needs_owner"] + s["auto_pending"]:
            self.assertNotIn("heal", f, "画面に出す側には、中の直し方の名前を出さない")
        log = [json.loads(x) for x in self.h.LOG.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(e["id"], e["ok"]) for e in log], [(f"draft:{k}", True)])
        st = self.h.status()
        self.assertFalse(st["stale"])
        self.assertEqual(len(st["auto_fixed_today"]), 1)
        self.assertEqual(st["needs_owner"][0]["action"]["action"], "cover")
        r = self.p.run("health.py", "report")
        self.assertIn("あなたの番", r.stdout)
        self.assertIn("ジャケットを作る", r.stdout)

    def test_dry_run_changes_nothing(self):
        k = self.p.weekly("hollowplan", self.p.week(-1), "A", stage="ready")
        d = self.p.out / "publish" / k / "DistroKid下書き.md"
        d.unlink()
        self.heal(dry=True)
        self.assertFalse(d.exists())
        self.assertFalse(self.h.LAST.exists())
        self.assertFalse(self.h.STATE.exists())

    def test_failed_fix_does_not_stop_others(self):
        p = self.p
        k = p.weekly("hollowplan", p.week(-1), "A", stage="ready")
        k2 = p.weekly("hollowplan", p.week(-2), "B", stage="ready")
        (p.out / "publish" / k / "DistroKid下書き.md").unlink()
        (p.out / "publish" / k2 / "DistroKid下書き.md").unlink()
        real, n = self.h.FIXERS["draft"], []

        def flaky(id, **kw):  # noqa: A002
            n.append(id)
            if len(n) == 1:
                raise RuntimeError("boom")
            return real(id, **kw)
        self.h.FIXERS["draft"] = flaky
        s = self.heal()
        self.assertEqual(len(n), 2)
        self.assertEqual(len(s["fixed_now"]), 1)
        self.assertEqual(len(s["needs_owner"]), 1)

    def test_heal_only_runs_safe_commands(self):
        """自動の直しが動かすのは、取り込み・配信の確認・保管・戻す（音量）だけ。Suno・DistroKid・ブラウザには触らない"""
        p = self.p
        k1 = p.weekly("hollowplan", p.week(-1), "A", stage="ready")
        k2 = p.weekly("hollowplan", p.week(-2), "B", stage="ready")
        k3 = p.weekly("hollowplan", p.week(-3), "C", stage="imported")
        k4 = p.weekly("hollowplan", p.week(-6), "D", stage="registered")
        (p.out / "masters" / k1 / "master.wav").unlink()
        shutil.rmtree(p.local / "HOLLOWPLAN" / "04_楽曲" / "B")
        p.set_state(k3, imported_at=self.ago(hours=5))
        p.set_state(k4, release_date=(date.today() - timedelta(days=9)).isoformat())
        before = sorted(str(x) for x in (p.out / "takes").rglob("*"))
        self.heal()
        allowed = {("schedule.py", "run", "intake"), ("schedule.py", "run", "release"), ("archive_sync.py", "--only"), ("publish.py", "back")}
        for cmd in self.ran:
            self.assertTrue(tuple(cmd[:3]) in allowed or tuple(cmd[:2]) in allowed, cmd)
            self.assertNotIn("browser_fill.py", cmd)
        self.assertEqual(len(self.ran), 4)
        self.assertEqual(sorted(str(x) for x in (p.out / "takes").rglob("*")), before, "テイクは消さない")
        src = (p.scripts / "health.py").read_text(encoding="utf-8")
        fix_code = src[src.index("# 自動の直し"):src.index("def _log(")]
        for word in ("fill_suno", "fill_distrokid", "unlink(", "rmtree(", "browser_fill.run", "open_chrome"):
            self.assertNotIn(word, fix_code, f"自動の直しの中に {word} があってはいけない")


if __name__ == "__main__":
    unittest.main()
