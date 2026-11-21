"""スマホへの知らせと見回り（alert.py）の試験：ntfy・healthchecks.io の偽サーバーで確かめる（決定 174）"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alert  # noqa: E402
from _mock_s3 import pin  # noqa: E402

NTFY_PORT, HC_PORT = 19130, 19131
GOT = {"ntfy": [], "hc": []}


class Ntfy(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.server.kind == "ntfy":
            d = json.loads(body.decode("utf-8"))
            code = 403 if d.get("topic") == "forbidden" else 200
            if code == 200:
                GOT["ntfy"].append({**d, "_auth": self.headers.get("Authorization", ""), "_path": self.path})
        else:
            code = 200 if self.path.startswith("/ping-uuid") else 404
            GOT["hc"].append({"path": self.path, "body": body.decode("utf-8")})
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


def serve(port, kind):
    s = ThreadingHTTPServer(("127.0.0.1", port), Ntfy)
    s.kind = kind
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


class AlertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = [serve(NTFY_PORT, "ntfy"), serve(HC_PORT, "hc")]

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers:
            s.shutdown()
            s.server_close()

    def setUp(self):
        pin(alert, alert._common)
        self.tmp = Path(tempfile.mkdtemp(prefix="alert-test-"))
        d = self.tmp / "alert"
        self.mac = []
        self.found = []
        self.patches = [
            mock.patch.multiple(alert, DIR=d, SENT=d / "sent.json", STATE=d / "state.json", HEART=d / "heartbeat.json",
                                load_dotenv=lambda **_: None),
            mock.patch.object(alert._common, "notify", lambda t, m: self.mac.append((t, m))),
            mock.patch.dict(os.environ, {"NTFY_URL": f"http://127.0.0.1:{NTFY_PORT}", "NTFY_TOPIC": "postdog-test", "NTFY_TOKEN": "",
                                         "HEALTHCHECK_URL": f"http://127.0.0.1:{HC_PORT}/ping-uuid"}),
        ]
        for p in self.patches:
            p.start()
        GOT["ntfy"].clear()
        GOT["hc"].clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_push_json_utf8(self):
        ok, _ = alert.push("自動の仕事が失敗しました", "毎週の制作（auto）", "error", url="http://localhost:8765/")
        self.assertTrue(ok)
        d = GOT["ntfy"][0]
        self.assertEqual((d["topic"], d["title"], d["priority"], d["click"]), ("postdog-test", "自動の仕事が失敗しました", 5, "http://localhost:8765/"))
        self.assertEqual(d["_path"], "/")
        with mock.patch.dict(os.environ, {"NTFY_TOKEN": "tk_abc"}):
            alert.push("t", "m")
        self.assertEqual(GOT["ntfy"][1]["_auth"], "Bearer tk_abc")

    def test_push_unset_and_refused(self):
        with mock.patch.dict(os.environ, {"NTFY_TOPIC": ""}):
            self.assertEqual(alert.push("t", "m"), (False, "未設定"))
            self.assertFalse(alert.notify("t", "m"))
            self.assertEqual(len(self.mac), 1)                       # Mac の通知はこれまでどおり出す
        with mock.patch.dict(os.environ, {"NTFY_TOPIC": "forbidden"}):
            ok, msg = alert.push("t", "m")
            self.assertFalse(ok)
            self.assertIn("403", msg)
        with mock.patch.dict(os.environ, {"NTFY_URL": "http://127.0.0.1:19199"}):   # だれもいない
            self.assertFalse(alert.push("t", "m")[0])

    def test_notify_dedupes(self):
        self.assertTrue(alert.notify("同じ知らせ", "中身"))
        self.assertFalse(alert.notify("同じ知らせ", "中身"))
        self.assertTrue(alert.notify("同じ知らせ", "中身", force=True))
        self.assertTrue(alert.notify("同じ知らせ", "別の中身"))
        self.assertEqual(len(GOT["ntfy"]), 3)
        sent = json.loads(alert.SENT.read_text())                    # 6 時間たてば、また送る
        alert.SENT.write_text(json.dumps({k: (datetime.now() - timedelta(hours=7)).isoformat() for k in sent}))
        self.assertTrue(alert.notify("同じ知らせ", "中身"))

    def test_heartbeat(self):
        self.assertTrue(alert.heartbeat(True))
        self.assertTrue(alert.heartbeat(False, "最後のエラーの行"))
        self.assertEqual([h["path"] for h in GOT["hc"]], ["/ping-uuid", "/ping-uuid/fail"])
        self.assertEqual(GOT["hc"][1]["body"], "最後のエラーの行")
        hb = json.loads(alert.HEART.read_text())
        self.assertEqual((hb["ok"], hb["sent"]), (False, True))
        with mock.patch.dict(os.environ, {"HEALTHCHECK_URL": ""}):
            self.assertFalse(alert.heartbeat(True))                  # 未設定でも、合図の記録は残す
        self.assertTrue(json.loads(alert.HEART.read_text())["ok"])

    def _watch(self):
        with mock.patch.object(alert, "problems", lambda st=None: list(self.found)):
            return alert.watch()

    def test_watch_alerts_once_and_recovery(self):
        r = self._watch()
        self.assertEqual((r["new"], r["pushed"], r["heartbeat"]), (0, 0, True))
        self.assertEqual(GOT["hc"][-1]["path"], "/ping-uuid")
        self.found = [{"key": "job:auto", "level": "error", "title": "自動の仕事が失敗しました：毎週の制作", "detail": "auto", "tail": "Traceback…"},
                      {"key": "vault_kit", "level": "info", "push": False, "title": "復旧キットをまだ保存していません", "detail": "…"}]
        r = self._watch()
        self.assertEqual((r["new"], r["pushed"]), (2, 1))            # スマホに送るのは 1 件（push: False は画面だけ）
        self.assertEqual(GOT["hc"][-1]["path"], "/ping-uuid/fail")
        self.assertIn("Traceback", GOT["hc"][-1]["body"])
        n = len(GOT["ntfy"])
        r = self._watch()
        self.assertEqual((r["new"], len(GOT["ntfy"])), (0, n))       # 同じ問題は 2 度知らせない
        st = alert.status()
        self.assertEqual({p["key"] for p in st["problems"]}, {"job:auto", "vault_kit"})
        self.assertFalse(st["stale"])
        self.found = [self.found[1]]
        r = self._watch()
        self.assertEqual(r["cleared"], 1)
        self.assertEqual(GOT["ntfy"][-1]["title"], "直りました")
        self.assertEqual(GOT["hc"][-1]["path"], "/ping-uuid")

    def test_job_failed_then_watch_no_duplicate(self):
        alert.job_failed("backup", "外付け SSD への保管と R2 への予備保管", 1)
        self.assertEqual(len(GOT["ntfy"]), 1)
        alert.job_failed("backup", "外付け SSD への保管と R2 への予備保管", 1)
        self.found = [{"key": "job:backup", "level": "error", "title": "x", "detail": "y"}]
        self._watch()
        self.assertEqual(len(GOT["ntfy"]), 1)

    def test_job_problems_reads_schedule_log(self):
        import schedule
        logs = self.tmp / "logs"
        logs.mkdir()
        (logs / "backup.log").write_text("\n===== 2026-11-19 03:30 backup：保管 =====\n▷ 手順 0：archive_sync.py\n→ 終了コード 0\n"
                                         "▷ 手順 1：backup_r2.py\nTraceback\n→ 終了コード 1\n", encoding="utf-8")
        (logs / "intake.log").write_text("\n===== 2026-11-19 03:30 intake：取り込み =====\n▷ 手順 0：publish.py scan --quiet\n→ 終了コード 0\n", encoding="utf-8")
        with mock.patch.object(schedule, "OUT", self.tmp), mock.patch.object(alert, "OUT", self.tmp):
            ps = alert._job_problems()
        self.assertEqual([p["key"] for p in ps], ["job:backup"])
        self.assertIn("backup_r2.py", ps[0]["detail"])
        self.assertIn("終了コード 1", ps[0]["tail"])

    def test_ssd_r2_disk_rules(self):
        import _archive
        st = {}
        with mock.patch.object(_archive, "_root", lambda: (None, "無い")):
            self.assertIsNone(alert._ssd_problem(st))                # つながっていないのに気づいた直後は知らせない
            st["ssd_missing_since"] = (datetime.now() - timedelta(hours=25)).isoformat()
            self.assertEqual(alert._ssd_problem(st)["key"], "ssd")
        with mock.patch.object(_archive, "_root", lambda: (self.tmp, "ok")):
            self.assertIsNone(alert._ssd_problem(st))
            self.assertNotIn("ssd_missing_since", st)
        with mock.patch.object(alert, "OUT", self.tmp), mock.patch.dict(os.environ, {"R2_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "b", "R2_SECRET_ACCESS_KEY": "c"}):
            self.assertIn("まだ 1 回も", alert._r2_problem()["title"])
            man = self.tmp / "backup" / "r2_manifest.json"
            man.parent.mkdir()
            man.write_text("{}")
            self.assertIsNone(alert._r2_problem())
            old = datetime.now().timestamp() - 3 * 86400
            os.utime(man, (old, old))
            self.assertIn("3 日", alert._r2_problem()["title"])
        with mock.patch.dict(os.environ, {"R2_ACCOUNT_ID": ""}):
            self.assertIsNone(alert._r2_problem())
        usage = lambda gb: mock.patch.object(alert.shutil, "disk_usage", lambda p: mock.Mock(free=gb * 1024 ** 3))  # noqa: E731
        with usage(50):
            self.assertIsNone(alert._disk_problem())
        with usage(3):
            self.assertEqual(alert._disk_problem()["level"], "warn")
        with usage(0.5):
            self.assertEqual(alert._disk_problem()["level"], "error")

    def test_status_stale_and_check(self):
        self.assertTrue(alert.status()["stale"])
        alert.heartbeat(True)
        self.assertFalse(alert.status()["stale"])
        alert.HEART.write_text(json.dumps({"at": (datetime.now() - timedelta(hours=2)).isoformat(), "ok": True, "sent": True}))
        self.assertTrue(alert.status()["stale"])
        self.assertEqual(alert.check("ntfy")[0], "ok")
        self.assertEqual(alert.check("healthcheck")[0], "ng")        # http:// の Ping URL は受け付けない
        with mock.patch.dict(os.environ, {"NTFY_TOPIC": "", "HEALTHCHECK_URL": ""}):
            self.assertEqual((alert.check("ntfy")[0], alert.check("healthcheck")[0]), ("none", "none"))
        self.assertIn("pmset", alert.keep_awake_hint())


if __name__ == "__main__":
    unittest.main()
