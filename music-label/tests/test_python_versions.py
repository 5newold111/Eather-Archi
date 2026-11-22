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


if __name__ == "__main__":
    unittest.main()
