"""鍵の金庫（vault.py）の試験：暗号・預ける・戻す・鍵の保存（決定 174）。本物の .env・out/・キーチェーンには触らない"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault  # noqa: E402
from _mock_s3 import MockS3, pin  # noqa: E402

PORT = 19110
SECRET = "sk-test-VERY-SECRET-0123456789"
_real_which = shutil.which


def _which(name, *a, **k):   # Mac で試験しても、本物のキーチェーン・クリップボードに触らない
    return None if name in ("security", "pbcopy") else _real_which(name, *a, **k)


class VaultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s3 = MockS3(PORT).start()

    @classmethod
    def tearDownClass(cls):
        cls.s3.stop()

    def setUp(self):
        pin(vault, vault.restore_r2)
        self.tmp = Path(tempfile.mkdtemp(prefix="vault-test-"))
        d = self.tmp / "out" / "vault"
        self.patches = [
            mock.patch.multiple(vault, ENV=self.tmp / ".env", DIR=d, STATE=d / "state.json", BLOB=d / "env.enc",
                                CHECKS=d / "checks.json", PASS_FILE=d / "passphrase"),
            mock.patch.object(vault.shutil, "which", side_effect=_which),
            mock.patch.dict(os.environ, {**self.s3.env(), "VAULT_ALLOW_FILE": "1"}),
        ]
        for p in self.patches:
            p.start()
        os.environ.pop("VAULT_PASSPHRASE", None)
        self.s3.objects.clear()
        (self.tmp / ".env").write_text(f"OPENAI_API_KEY={SECRET}\nR2_BUCKET=label-backup\n", encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- 暗号
    def test_roundtrip_and_no_plaintext(self):
        p = vault.new_passphrase()
        self.assertRegex(p, r"^([A-Z2-9]{5}-){5}[A-Z2-9]{5}$")
        blob = vault.encrypt(b"KEY=" + SECRET.encode(), p)
        self.assertNotIn(SECRET.encode(), blob)
        self.assertEqual(json.loads(blob)["format"], "postdog-vault")
        self.assertEqual(vault.decrypt(blob, p), b"KEY=" + SECRET.encode())

    def test_wrong_passphrase_and_tamper(self):
        p = vault.new_passphrase()
        blob = vault.encrypt(b"KEY=1", p)
        with self.assertRaises(vault.VaultError):
            vault.decrypt(blob, vault.new_passphrase())
        head = json.loads(blob)
        import base64
        ct = bytearray(base64.b64decode(head["ct"]))
        ct[-1] ^= 1
        bad = dict(head, ct=base64.b64encode(bytes(ct)).decode())
        with self.assertRaises(vault.VaultError):
            vault.decrypt(json.dumps(bad).encode(), p)
        with self.assertRaises(vault.VaultError):   # 見出しの書き換えも気づく
            vault.decrypt(json.dumps(dict(head, created="2000-01-01T00:00:00")).encode(), p)
        with self.assertRaises(vault.VaultError):
            vault.decrypt(b"not a vault", p)

    def test_pbkdf2_fallback(self):
        with mock.patch.object(vault, "_kdf_name", return_value="pbkdf2-sha256"), mock.patch.object(vault, "PBKDF2_ITER", 1000):
            blob = vault.encrypt(b"A=1", "pass")
            self.assertEqual(json.loads(blob)["kdf"], "pbkdf2-sha256")
            self.assertEqual(vault.decrypt(blob, "pass"), b"A=1")

    # --- 預ける・戻す
    def test_backup_restore_via_r2(self):
        self.assertFalse(vault.backup(quiet=True)["ok"])          # 合言葉がまだ無い
        p = vault.new_passphrase()
        self.assertIn("passphrase", vault.store_passphrase(p))
        self.assertEqual(oct(vault.PASS_FILE.stat().st_mode & 0o777), "0o600")
        r = vault.backup(quiet=True)
        self.assertTrue(r["ok"] and r["uploaded"], r)
        self.assertIn(vault.R2_KEY, self.s3.objects)
        self.assertNotIn(SECRET.encode(), self.s3.objects[vault.R2_KEY]["body"])
        self.assertNotIn(SECRET, vault.STATE.read_text())
        self.assertTrue(vault.backup(if_changed=True, quiet=True)["skipped"])
        n = len(self.s3.requests)
        vault.backup(if_changed=True, quiet=True)
        self.assertEqual(len(self.s3.requests), n)                # 変わっていなければ送らない
        st = vault.status()
        self.assertTrue(st["ready"])
        self.assertFalse(st["env_changed_since"])
        # .env が変わる → 預け直しが要る
        vault._write_env("NOTION_TOKEN", "ntn_abc")
        self.assertTrue(vault.status()["env_changed_since"])
        self.assertFalse(vault.backup(if_changed=True, quiet=True).get("skipped"))
        # 新しい Mac：.env が無い状態から戻す
        want = vault.ENV.read_bytes()
        vault.ENV.unlink()
        r = vault.restore(p)
        self.assertEqual(vault.ENV.read_bytes(), want)
        self.assertEqual(r["keys"], 3)
        self.assertEqual(oct(vault.ENV.stat().st_mode & 0o777), "0o600")
        # 前の .env があれば、よけてから戻す
        vault.ENV.write_text("OLD=1\n")
        r = vault.restore(p)
        self.assertTrue(r["kept"].exists() and r["kept"].read_text() == "OLD=1\n")
        self.assertTrue(vault.restore(p)["same"])
        with self.assertRaises(vault.VaultError):
            vault.restore("WRONG-WRONG")

    def test_backup_without_r2_keeps_local_copy(self):
        vault.store_passphrase("LOCAL-ONLY")
        with mock.patch.dict(os.environ, {"R2_ACCOUNT_ID": "", "R2_ACCESS_KEY_ID": "", "R2_SECRET_ACCESS_KEY": ""}):
            r = vault.backup(quiet=True)
        self.assertTrue(r["ok"])
        self.assertFalse(r["uploaded"])
        self.assertEqual(vault.restore("LOCAL-ONLY", source=str(vault.BLOB))["same"], True)
        self.assertFalse(vault.status()["ready"])                 # R2 に無いので「預けてある」にはしない

    def test_backup_reports_r2_failure(self):
        vault.store_passphrase("X-PASS")
        with mock.patch.dict(os.environ, {"R2_SECRET_ACCESS_KEY": "wrong"}):
            r = vault.backup(quiet=True)
        self.assertFalse(r["ok"])
        self.assertIn("R2 に預けられませんでした", r["message"])

    # --- ダッシュボードの鍵の保存
    def test_save_key(self):
        self.assertFalse(vault.save_key("PATH", "x", backup_after=False)["ok"])                 # 知らない名前
        self.assertFalse(vault.save_key("NTFY_TOPIC", "a\nB=1", backup_after=False)["ok"])      # 改行で別の鍵を足せない
        self.assertFalse(vault.save_key("NTFY_TOPIC", "  ", backup_after=False)["ok"])
        with mock.patch.dict(os.environ, {}):
            r = vault.save_key("NTFY_TOPIC", ' "postdog-topic-xyz" ', backup_after=False)
            self.assertTrue(r["ok"])
            self.assertNotIn("postdog-topic-xyz", json.dumps(r, ensure_ascii=False))
            self.assertEqual(os.environ["NTFY_TOPIC"], "postdog-topic-xyz")
        text = vault.ENV.read_text()
        self.assertIn("NTFY_TOPIC=postdog-topic-xyz\n", text)
        self.assertIn(f"OPENAI_API_KEY={SECRET}", text)                                         # ほかの行は残る
        vault.save_key("NTFY_TOPIC", "second", backup_after=False)
        self.assertEqual(vault.ENV.read_text().count("NTFY_TOPIC="), 1)

    def test_status_never_shows_values(self):
        s = json.dumps(vault.status(), ensure_ascii=False)
        self.assertNotIn(SECRET, s)
        names = {k["name"]: k for k in vault.status()["keys"]}
        self.assertTrue(names["OPENAI_API_KEY"]["set"])
        self.assertFalse(names["HEALTHCHECK_URL"]["set"])
        self.assertEqual(names["NTFY_TOPIC"]["service"], "ntfy")

    def test_client_ok(self):
        for ip in ("127.0.0.1", "::1", "100.64.0.1", "100.101.102.103", "::ffff:127.0.0.1"):
            self.assertTrue(vault.client_ok(ip), ip)
        for ip in ("192.168.1.20", "10.0.0.5", "100.128.0.1", "8.8.8.8", "", "x"):
            self.assertFalse(vault.client_ok(ip), ip)

    def test_kit_refuses_outside_terminal(self):
        vault.store_passphrase("KIT-PASS")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = vault.cmd_kit()
        self.assertEqual(code, 2)
        self.assertNotIn("KIT-PASS", buf.getvalue())
        self.assertIn("KIT-PASS", vault.kit_text("KIT-PASS"))


if __name__ == "__main__":
    unittest.main()
