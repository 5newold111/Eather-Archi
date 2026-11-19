"""全体の煙の試験（決定 173）：どの部品も読み込める・ダッシュボードが組み立つ・押しても動かないボタンが無い・定期実行の登録に抜けが無い"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _fixture import REPO, STAGES, Project, script_names

# 入っていなくてもよい部品（無い環境では、その部品を使う部品の読み込みを飛ばす）
OPTIONAL = {"openpyxl", "anthropic", "playwright", "faster_whisper", "librosa", "soundfile", "numpy", "scipy", "PIL", "fontTools", "torch",
            "transformers", "basic_pitch", "demucs", "pretty_midi", "mido", "matchering", "certifi", "cryptography"}
# command_for を通らない操作（別の入口で受ける）。増やすときは、受ける入口を書くこと
OTHER_ENDPOINTS: dict[str, str] = {}
# "action" という言葉を、ダッシュボードのボタンではない意味で使っている部品（ブラウザの中の案内のボタン：continue・skip・stop など）
NOT_DASHBOARD = {"browser_fill.py"}


def node_check(js: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    try:
        r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True, timeout=60)
        return r.returncode == 0, r.stderr[-600:]
    finally:
        Path(f.name).unlink(missing_ok=True)


def scripts_of(html: str) -> list[str]:
    return [m for m in re.findall(r"<script>(.*?)</script>", html, re.S) if m.strip()]


class CompileTest(unittest.TestCase):
    def test_every_script_compiles(self):
        bad = []
        for p in sorted((REPO / "scripts").glob("*.py")):
            try:
                compile(p.read_text(encoding="utf-8"), str(p), "exec")
            except SyntaxError as e:
                bad.append(f"{p.name}:{e.lineno} {e.msg}")
        self.assertEqual(bad, [])

    def test_no_secret_looking_strings_in_scripts(self):
        """鍵らしい文字列をプログラムに書いていない（鍵は .env にだけ）"""
        rx = re.compile(r"(sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|ntn_[A-Za-z0-9]{30,}|secret_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})")
        hits = [f"{p.name}" for p in sorted((REPO / "scripts").glob("*.py")) if rx.search(p.read_text(encoding="utf-8"))]
        self.assertEqual(hits, [])


class ImportTest(unittest.TestCase):
    """どの部品も、読み込むだけでは何も起こさない（ファイルを作らない・止まらない・何も表示しない）"""

    def test_import_all_without_side_effects(self):
        p = Project(ssd=True)
        try:
            listing = lambda: sorted(str(x.relative_to(p.tmp)) for x in p.tmp.rglob("*")  # noqa: E731
                                     if "scripts" not in x.parts and "templates" not in x.parts)
            before, bad, skipped = listing(), [], []
            for name in sorted(script_names()):
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        p.mod(name)
                except ImportError as e:
                    if (e.name or "").split(".")[0] in OPTIONAL:
                        skipped.append(f"{name}（{e.name} が無い）")
                    else:
                        bad.append(f"{name}: {type(e).__name__}: {e}")
                except BaseException as e:  # noqa: BLE001  SystemExit も「読み込むだけで止まる」ので不具合
                    bad.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
                else:
                    if buf.getvalue().strip():
                        bad.append(f"{name}: 読み込むだけで表示が出る：{buf.getvalue().strip()[:80]}")
            self.assertEqual(bad, [])
            self.assertEqual([x for x in listing() if x not in before], [], "読み込むだけでファイルを作らない")
        finally:
            p.close()


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = p = Project(ssd=True, debuted=("declan_fay", "hollowplan"))
        cls.keys = [p.weekly("hollowplan", p.week(-i - 1), f"Song {st}", stage=st, days_ago=i) for i, st in enumerate(STAGES)]
        cls.kd = p.debut("tidewool", "The Low Part", "imported")
        cls.webapp = p.mod("webapp")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def test_status_json_builds_and_serializes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            d = self.webapp.status_json()
        s = json.dumps(d, ensure_ascii=False, default=str)
        self.assertGreater(len(s), 1000)
        for k in ("generated", "artists", "timeline", "songs", "system", "jobs"):
            self.assertIn(k, d)
        self.assertEqual({x["id"] for x in d["songs"]}, set(self.keys) | {"PDR-001"})
        rows = [r for r in d["timeline"]["regular"]["rows"] if not r.get("planned")]
        self.assertEqual(len(rows), len(STAGES))
        for r in rows:   # どのマスにも、止まっている原因・やること・ボタンの説明が付く
            for ph in r["phases"]:
                self.assertIn("guide", ph, (r["id"], ph["name"]))
                self.assertNotIn("error", ph["guide"], (r["id"], ph["name"], ph["guide"].get("error")))
        self.assertEqual([r["slug"] for r in d["timeline"]["debut"]["rows"]], ["tidewool"])

    def test_page_scripts_are_valid_javascript(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        pages = {"webapp.PAGE": self.webapp.PAGE}
        for mod, attr in (("song_check", "PAGE"),):
            pages[f"{mod}.{attr}"] = getattr(self.p.mod(mod), attr)
        try:
            ui = self.p.mod("_ui")
            pages["_ui.page"] = ui.page("試験", "<p>x</p>", "tok", auto=True)
        except ImportError:
            pass
        for name, html in pages.items():
            js = scripts_of(html)
            self.assertTrue(js, name)
            for i, code in enumerate(js):
                ok, err = node_check(code)
                self.assertTrue(ok, f"{name} の {i + 1} つ目の <script>：{err}")

    def _names(self, body: str) -> set[str]:
        out = set(re.findall(r'action == "([a-z_0-9]+)"', body))
        for m in re.findall(r"action in \(([^)]*)\)", body):
            out |= set(re.findall(r'"([a-z_0-9]+)"', m))
        return out

    def handled(self) -> set[str]:
        """受ける側にある操作の名前。「money_ で始まるものは money.py に任せる」のような任せ方は、任せた先の部品の中も見る"""
        src = (self.p.scripts / "webapp.py").read_text(encoding="utf-8")
        i = src.index("def command_for")
        body = src[i:src.index("\ndef ", i + 10)]
        out = self._names(body)
        for prefix in re.findall(r'action\.startswith\("([a-z_0-9]+)"\)', body):
            delegate = self.p.scripts / f"{prefix.rstrip('_')}.py"
            if delegate.exists():
                out |= {n for n in self._names(delegate.read_text(encoding="utf-8")) if n.startswith(prefix)}
        return out

    def used(self) -> dict[str, set[str]]:
        """ボタンが送る操作の名前 → 出てくる場所"""
        out: dict[str, set[str]] = {}
        for p in sorted(self.p.scripts.glob("*.py")):
            if p.name in NOT_DASHBOARD:
                continue
            src = p.read_text(encoding="utf-8")
            names = set(re.findall(r'data-act=\\?"([a-z_0-9]+)\\?"', src))                       # HTML に書いたボタン
            names |= set(re.findall(r'["\']action["\']:\s*["\']([a-z_0-9]+)["\']', src))        # 案内・別ページのボタン（{"action": "…"}）
            names |= set(re.findall(r'\bact\("([a-z_0-9]+)"', src))                              # 画面のプログラムから直接
            for rhs in re.findall(r'dataset\.act=(?!=)([^;]+);', src):                               # あとから付け替えるボタン
                names |= set(re.findall(r'"([a-z_0-9]+)"', rhs))
            for n in names:
                out.setdefault(n, set()).add(p.name)
        return out

    def test_no_dead_buttons(self):
        """画面のどのボタン（data-act・案内の action）も、受ける側（webapp.command_for）にある"""
        handled, used = self.handled(), self.used()
        self.assertGreater(len(handled), 40)
        self.assertGreater(len(used), 30)
        dead = {n: sorted(w) for n, w in used.items() if n not in handled and n not in OTHER_ENDPOINTS}
        self.assertEqual(dead, {}, "押しても「その操作はできません」になるボタン")

    def test_parameterless_actions_point_to_existing_scripts(self):
        missing = []
        for a in sorted(self.handled()):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    found = self.webapp.command_for(a, {})
            except Exception as e:  # noqa: BLE001
                missing.append(f"{a}: {type(e).__name__}: {str(e)[:80]}")
                continue
            if not found:
                continue   # 曲や組の指定が要る操作
            cmd, label = found
            self.assertTrue(label, a)
            for part in cmd[1:3]:
                if str(part).endswith(".py") and not Path(part).exists() and not (self.p.root / str(part)).exists():
                    missing.append(f"{a}: {part} がありません")
        self.assertEqual(missing, [])

    def test_guide_actions_for_every_phase(self):
        """マスの案内のボタンが送る操作も、受ける側にある"""
        with contextlib.redirect_stdout(io.StringIO()):
            d = self.webapp.status_json()
        handled, bad = self.handled(), []
        for key in ("debut", "regular", "irregular"):
            for r in d["timeline"][key]["rows"]:
                for ph in r["phases"]:
                    for a in (ph.get("guide") or {}).get("actions", []) + r.get("back", []) + r.get("redo", []):
                        if a.get("action") and a["action"] not in handled:
                            bad.append((r.get("id"), ph["name"], a["action"]))
        self.assertEqual(bad, [])


class ScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project(ssd=True)
        cls.s = cls.p.mod("schedule")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def test_every_job_script_exists(self):
        missing = [f"{job}: {cmd[0]}" for job, (_desc, cmds) in self.s.JOBS.items() for cmd in cmds if not (self.p.scripts / cmd[0]).exists()]
        missing += [f"{name}: {cmd[0]}" for name, (_d, cmd) in self.s.SERVICES.items() if not (self.p.scripts / cmd[0]).exists()]
        self.assertEqual(missing, [])

    def test_every_job_has_launchd_and_cron_entry(self):
        keys = {"StartInterval", "StartCalendarInterval", "WatchPaths", "StartOnMount"}
        for job in self.s.JOBS:
            cal = self.s.calendar(job)
            self.assertTrue(keys & set(cal), f"{job}: launchd の起動条件がありません（{cal}）")
            self.assertIn(job, self.s.CRON, f"{job}: cron の時刻がありません")
            self.assertRegex(self.s.CRON[job], r"^\S+ \S+ \S+ \S+ \S+$", job)
        self.assertEqual(sorted(set(self.s.CRON) - set(self.s.JOBS)), [], "無くなった仕事の時刻が残っていない")

    def test_plists_are_well_formed(self):
        import plistlib
        for job in [*self.s.JOBS, *self.s.SERVICES]:
            d = self.s.plist_for(job)
            self.assertTrue(d["Label"].endswith("." + job))
            self.assertTrue(plistlib.dumps(d))
            self.assertTrue(all(isinstance(x, str) for x in d["ProgramArguments"]), job)

    def test_job_descriptions_are_unique(self):
        descs = [d for d, _ in self.s.JOBS.values()]
        self.assertEqual(len(descs), len(set(descs)))

    def test_job_arguments_are_accepted(self):
        """定期実行が渡す引数を、その部品が受け取れる（--help が通る・知らない引数で落ちない書き方か）"""
        bad = []
        for job, (_d, cmds) in self.s.JOBS.items():
            for cmd in cmds:
                src = (self.p.scripts / cmd[0]).read_text(encoding="utf-8")
                for a in cmd[1:]:
                    if a.startswith("--") and a not in src:
                        bad.append(f"{job}: {cmd[0]} は {a} を知りません")
                    elif not a.startswith("-") and re.fullmatch(r"[a-z][a-z-]+", a) and f'"{a}"' not in src and f"'{a}'" not in src:
                        bad.append(f"{job}: {cmd[0]} に {a} という命令が見当たりません")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
