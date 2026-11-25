"""「鍵と見守り」のページ（keys_page.py）の試験：鍵の値を画面に出さない・入力欄とボタンがそろっている（決定 174）"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alert  # noqa: E402
import keys_page  # noqa: E402
import vault  # noqa: E402

SECRET = "sk-test-PAGE-SECRET-987654321"

from _mock_s3 import pin  # noqa: E402


class KeysPageTest(unittest.TestCase):
    def setUp(self):
        pin(keys_page, alert, vault, keys_page._ui)
        self.tmp = Path(tempfile.mkdtemp(prefix="keys-page-test-"))
        v, a = self.tmp / "vault", self.tmp / "alert"
        (self.tmp / ".env").write_text(f"OPENAI_API_KEY={SECRET}\nNTFY_TOPIC=postdog-secret-topic\n", encoding="utf-8")
        self.patches = [
            mock.patch.multiple(vault, ENV=self.tmp / ".env", DIR=v, STATE=v / "state.json", BLOB=v / "env.enc", CHECKS=v / "checks.json",
                                PASS_FILE=v / "passphrase"),
            mock.patch.multiple(alert, DIR=a, SENT=a / "sent.json", STATE=a / "state.json", HEART=a / "heartbeat.json", load_dotenv=lambda **_: None),
            mock.patch.dict(os.environ, {"VAULT_PASSPHRASE": "", "NTFY_TOPIC": "postdog-secret-topic", "HEALTHCHECK_URL": ""}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page(self):
        html = keys_page.page("TOKEN123")
        self.assertNotIn(SECRET, html)
        self.assertNotIn("postdog-secret-topic", html)
        self.assertIn('id="k-OPENAI_API_KEY"', html)
        self.assertIn('type="password"', html)
        self.assertIn('data-post="/api/keys/save"', html)
        self.assertIn('data-fields="value:k-NTFY_TOPIC"', html)
        self.assertIn("vault_init", html)                     # 金庫がまだ → 「金庫を作る」を出す
        self.assertIn("keys_check", html)
        self.assertIn("vault.py restore", html)
        self.assertIn('const TOKEN="TOKEN123"', html)
        self.assertLess(html.index("スマホへの知らせ（ntfy）"), html.index("OPENAI_API_KEY"))   # 見守りの鍵を先に


if __name__ == "__main__":
    unittest.main()
