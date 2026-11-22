"""新しい Python（3.15）で見つけた落とし穴を、二度と起こさないための試験（決定 180）"""
from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


class HelpStrings(unittest.TestCase):
    def test_percent_in_help_is_doubled(self):
        """3.14 から、引数の説明文の中の % 1 文字はエラーになる（% は %% と書く）。money.py の「事業の割合 %」で落ちていた"""
        bad = []
        for f in sorted(SCRIPTS.glob("*.py")):
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                for m in re.finditer(r'help=(?:f?)"([^"]*)"', line):
                    if re.search(r"%(?!\()", m.group(1).replace("%%", "")):
                        bad.append(f"{f.name}:{i}")
        self.assertEqual(bad, [])


class Whisper(unittest.TestCase):
    def test_transcribes_without_onnxruntime(self):
        """onnxruntime（3.15 版がまだ無い）が無いときは、声の区間を見つける機能を使わずに聞き取る"""
        import reference_db as R
        seen = {}

        class Model:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, path, **kw):
                seen.update(kw)
                seg = types.SimpleNamespace(text=" hello", start=0.0, end=1.0, avg_logprob=-0.2, no_speech_prob=0.1, compression_ratio=1.2)
                return [seg], types.SimpleNamespace(language="en", language_probability=0.9)
        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = Model
        keep = {k: sys.modules.get(k) for k in ("faster_whisper", "onnxruntime")}
        sys.modules["faster_whisper"] = fake
        sys.modules["onnxruntime"] = None          # import すると ImportError になる
        old = R._WM
        R._WM = None
        try:
            R.transcribe(Path("x.wav"))
        finally:
            R._WM = old
            for k, v in keep.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertIs(seen.get("vad_filter"), False)
        self.assertIn("no_speech_threshold", seen)


class Requirements(unittest.TestCase):
    def test_check_reads_markers(self):
        """部品の一覧の「; python_version …」の書き方を、点検が名前だけ読めること"""
        txt = (SCRIPTS.parent / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(txt, r'(?m)^faster-whisper; python_version < "3\.15"')
        import recover
        names = []
        for line in txt.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r"[A-Za-z0-9_.\-]+", line)
            if m:
                names.append(m.group(0))
        self.assertIn("faster-whisper", names)
        self.assertEqual(recover.IMPORT_NAME["faster-whisper"], "faster_whisper")


class LaunchdPython(unittest.TestCase):
    def test_plist_with_other_python_is_found(self):
        """定期実行が、部品の入っていない Python で登録されていたら見つける（決定 180）"""
        import plistlib
        import tempfile
        import recover
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            venv = d / "music-label" / ".venv" / "bin" / "python3"
            venv.parent.mkdir(parents=True)
            venv.write_text("")
            agents = d / "LaunchAgents"
            agents.mkdir()
            (agents / "jp.x.a.plist").write_bytes(plistlib.dumps({"ProgramArguments": [str(venv), "s.py"]}))
            self.assertEqual(recover.plist_python_mismatch(agents, "jp.x", venv), "")
            (agents / "jp.x.b.plist").write_bytes(plistlib.dumps({"ProgramArguments": ["/opt/homebrew/bin/python3", "s.py"]}))
            self.assertEqual(recover.plist_python_mismatch(agents, "jp.x", venv), "/opt/homebrew/bin/python3")
            self.assertEqual(recover.plist_python_mismatch(agents, "jp.x", d / "none" / "python3"), "", "置き場が無ければ見ない")


class SweepFixes(unittest.TestCase):
    """全部を試しに流して見つけた所（決定 180）"""

    def test_send_diag_help_does_not_send(self):
        import subprocess
        r = subprocess.run([sys.executable, str(SCRIPTS / "send_diag.py"), "--help"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("使い方", r.stdout)
        self.assertNotIn("送りました", r.stdout + r.stderr)


    def test_supabase_rows_without_take_no(self):
        """SSD から戻した曲の記録（テイクの番号 take_no が無い）でも、データベースへの写しを組み立てられる"""
        import json
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _fixture import Project
        p = Project()
        try:
            k = p.weekly("hollowplan", p.week(-1), "A", stage="ready")
            sel = p.out / "takes" / k / "selection.json"
            d = json.loads(sel.read_text(encoding="utf-8"))
            for t in d["takes"]:
                t.pop("take_no", None)
                t.pop("measure", None)
            sel.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            p.reload()
            ss = p.mod("supabase_sync")
            rows, _ = ss.week_rows(p.week(-1), False)
            gens = [r for t, r, _ in rows if t == "generations"]
            self.assertTrue(gens)
            self.assertTrue(all(isinstance(g["take_no"], int) for g in gens))
        finally:
            p.close()


    def _project(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _fixture import Project
        return Project()

    def test_live_song_is_not_sent_to_distrokid_again(self):
        """配信中・登録済みの曲に「DistroKid に入力する」を出さず、入力の本体も断る（二重の登録を防ぐ）"""
        p = self._project()
        try:
            k = p.weekly("hollowplan", p.week(-6), "After Nights", stage="registered", days_ago=20)
            p.set_state(k, status="live", released="2026-10-21")
            p.reload()
            songs = p.mod("_songs")
            song = next(x for x in songs.all_songs() if x["key"] == k)
            html = p.mod("song_check").song_page(song["id"], "tok") or ""
            self.assertNotIn("fill_distrokid", html)
            self.assertIn("配信中", html)
            bf = p.mod("browser_fill")
            self.assertEqual(bf.run_distrokid(None, song["id"]), 2, "ブラウザを開く前に断る")
            web = p.mod("webapp")
            tl = web.status_json() if hasattr(web, "status_json") else None
            if tl:
                for key in ("debut", "regular", "irregular"):
                    for r in tl["timeline"][key]["rows"]:
                        if r.get("id") == song["id"]:
                            self.assertFalse([a for a in r.get("redo") or [] if a.get("action") == "fill_distrokid"])
        finally:
            p.close()

    def test_chorus_start_without_measure(self):
        """SSD から戻した曲（テイクの測った値が無い）でも、SNS の投稿案が落ちない"""
        import json
        p = self._project()
        try:
            k = p.weekly("tidewool", p.week(-1), "Half", stage="ready")
            sel = p.out / "takes" / k / "selection.json"
            d = json.loads(sel.read_text(encoding="utf-8"))
            for t in d["takes"]:
                t.pop("measure", None)
            sel.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            p.reload()
            week, slug = k.split("_", 1)
            self.assertEqual(p.mod("post_social").chorus_start(week, slug), 45.0)
        finally:
            p.close()

    def test_debut_song_has_no_retake_form(self):
        """デビュー曲（ブリーフが無い）では、Suno の作り直しを受け付けない"""
        p = self._project()
        try:
            k = p.debut("declan_fay", "Side B", stage="brief")
            p.reload()
            song = next(x for x in p.mod("_songs").all_songs() if x["key"] == k)
            web = p.mod("webapp")
            self.assertIsNone(web.command_for("suno_retake", {"id": song["id"], "why": "サビが弱い"}))
            self.assertEqual(p.mod("take_check").fix_brief(song, ["サビが弱い"], ["x"], "test"), (False, ""))
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
