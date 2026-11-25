"""鍵の案内役の入力（決定 180）：実機で起きた入れ間違い（R2 にメールアドレス・Claude に鍵の ID）をその場で直してもらう"""
from __future__ import annotations

import builtins
import getpass
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import setup_keys as K  # noqa: E402

ACC = "0123456789abcdef0123456789abcdef"


class Tidy(unittest.TestCase):
    def test_r2_account_from_email_is_refused(self):
        got, note = K.tidy("R2_ACCOUNT_ID", "someone@example.com")
        self.assertIsNone(got)
        self.assertIn("メールアドレスではなく", note)

    def test_r2_account_from_endpoint_url(self):
        self.assertEqual(K.tidy("R2_ACCOUNT_ID", f"https://{ACC}.r2.cloudflarestorage.com/label-backup")[0], ACC)
        self.assertEqual(K.tidy("R2_ACCOUNT_ID", ACC.upper())[0], ACC)
        self.assertEqual(K.tidy("R2_BUCKET", f"https://{ACC}.r2.cloudflarestorage.com/label-backup/")[0], "label-backup")

    def test_anthropic_key_id_is_refused(self):
        got, note = K.tidy("ANTHROPIC_API_KEY", "apikey_01" + "x" * 22)
        self.assertIsNone(got)
        self.assertIn("sk-ant-", note)
        self.assertEqual(K.tidy("ANTHROPIC_API_KEY", "sk-ant-api03-abc")[0], "sk-ant-api03-abc")


class Ask(unittest.TestCase):
    def test_wrong_then_right_is_asked_again(self):
        answers = iter(["someone@example.com", f"https://{ACC}.r2.cloudflarestorage.com/label-backup"])
        with mock.patch.object(K, "env", return_value=""), mock.patch.object(builtins, "input", lambda *_: next(answers)):
            self.assertEqual(K.ask_key("R2_ACCOUNT_ID", False), ACC)

    def test_owner_name_is_not_echoed(self):
        seen = []
        with mock.patch.object(K, "env", return_value=""), \
                mock.patch.object(getpass, "getpass", lambda *_: "Taro Yamada"), \
                mock.patch.object(builtins, "input", lambda *_: seen.append("input") or "Taro Yamada"), \
                mock.patch.object(builtins, "print", lambda *a, **k: seen.append(" ".join(map(str, a)))):
            self.assertEqual(K.ask_key("OWNER_LEGAL_NAME", False), "Taro Yamada")
        self.assertNotIn("input", seen, "本名は画面に出ない入力欄で受け取る")
        self.assertFalse(any("Taro" in x for x in seen))


class Dashboard(unittest.TestCase):
    def test_vault_save_refuses_wrong_shape(self):
        import vault
        with mock.patch.object(vault, "known_keys", return_value={"R2_ACCOUNT_ID"}), mock.patch.object(vault, "_write_env") as w:
            r = vault.save_key("R2_ACCOUNT_ID", "someone@example.com", backup_after=False)
            self.assertFalse(r["ok"])
            self.assertIn("Account ID", r["error"])
            w.assert_not_called()
            r = vault.save_key("R2_ACCOUNT_ID", f"https://{ACC}.r2.cloudflarestorage.com/label-backup", backup_after=False)
            self.assertTrue(r["ok"])
            w.assert_called_once_with("R2_ACCOUNT_ID", ACC)


if __name__ == "__main__":
    unittest.main()
