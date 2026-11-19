"""お金の記録（scripts/money.py。決定 175）の自動テスト。一時フォルダだけを使い、本物の out/ には触らない

  python3 -m unittest discover -s tests -v      （music-label/ で）
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import money  # noqa: E402

HEAD = ["Reporting Date", "Sale Month", "Store", "Artist", "Title", "ISRC", "UPC", "Quantity", "Team Percentage",
        "Song/Album", "Country of Sale", "Songwriter Royalties Withheld", "Earnings (USD)"]


def tsv(path: Path, rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(HEAD)
        for r in rows:
            w.writerow(r)
    return path


def row(rep, month, store, artist, title, qty, usd):
    return [rep, month, store, artist, title, "QZTEST0000001", "000000000000", qty, 100, "Song", "JP", 0, usd]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self._old = (money.DIR, money.DOWNLOADS, money.INBOX, money.ROOT, money.all_artists)
        money.DIR, money.DOWNLOADS, money.INBOX, money.ROOT = t / "money", t / "Downloads", t / "inbox", t / "root"
        money.all_artists = lambda include_retiring=False: [{"slug": "hollowplan", "name": "HOLLOWPLAN"},
                                                            {"slug": "declan_fay", "name": "DECLAN FAY"}]
        self.t = t

    def tearDown(self):
        money.DIR, money.DOWNLOADS, money.INBOX, money.ROOT, money.all_artists = self._old
        self.tmp.cleanup()


class LedgerTest(Base):
    def test_add_summary_and_business_pct(self):
        money.add(d="2026-11-03", kind="expense", category="機材", item="マイク", amount="10,000", pct=50)
        money.add(d="2026-11-10", kind="income", category="その他", item="ライブの出演料", amount=3000, pct=10)   # 収入は割合を使わない
        money.add(d="2025-12-31", kind="expense", category="広告", item="去年の広告", amount=999)
        sm = money.summary(2026)
        nov = sm["months"][10]
        self.assertEqual((nov["income"], nov["expense"], nov["profit"]), (3000, 5000, -2000))
        self.assertEqual(sm["total"], {"income": 3000, "expense": 5000, "profit": -2000})
        self.assertEqual(sm["categories"], [("機材", 5000)])
        self.assertEqual(money.summary(2025)["total"]["expense"], 999)
        self.assertIn(2025, sm["years"])

    def test_bad_input_is_refused(self):
        for kw in ({"d": "2026-13-01"}, {"amount": "abc"}, {"amount": 0}, {"amount": -5}, {"kind": "gift"}, {"item": "  "},
                   {"currency": "EUR"}, {"pct": 150}):
            base = {"d": "2026-11-01", "kind": "expense", "category": "その他", "item": "x", "amount": 100}
            with self.assertRaises(money.BadInput, msg=str(kw)):
                money.add(**{**base, **kw})
        self.assertEqual(money.load(), [])

    def test_unknown_category_becomes_other_and_text_is_capped(self):
        e = money.add(d="2026-11-01", kind="expense", category="なぞ", item="あ" * 200, amount=1, memo="m" * 500)
        self.assertEqual(e["category"], "その他")
        self.assertEqual((len(e["item"]), len(e["memo"])), (80, 200))

    def test_delete(self):
        e = money.add(d="2026-11-01", kind="expense", category="その他", item="x", amount=100)
        self.assertIsNone(money.delete("nope"))
        self.assertEqual(money.delete(e["id"])["item"], "x")
        self.assertEqual(money.load(), [])

    def test_atomic_file_is_valid_json(self):
        money.add(d="2026-11-01", kind="expense", category="その他", item="x", amount=100)
        self.assertEqual(len(json.loads((money.DIR / "ledger.json").read_text(encoding="utf-8"))["entries"]), 1)
        self.assertEqual([p.name for p in money.DIR.iterdir() if p.name.endswith(".tmp")], [])


class RateTest(Base):
    def test_usd_waits_for_rate_then_fills(self):
        e = money.add(d="2026-10-04", kind="expense", category="サブスク", item="Suno Pro", amount=10, currency="USD")
        self.assertTrue(money.pending(e))
        self.assertEqual(money.summary(2026)["pending"], 1)
        self.assertEqual(money.set_rate(150), 1)
        got = money.load()[0]
        self.assertEqual((got["amount_jpy"], got["rate"]), (1500, 150.0))
        self.assertEqual(money.set_rate(160), 0)                     # いつものレートを変えても、決まった記録は動かさない
        self.assertEqual(money.load()[0]["amount_jpy"], 1500)

    def test_month_rate_rerates_only_that_month(self):
        money.set_rate(150)
        money.add(d="2026-10-04", kind="expense", category="サブスク", item="a", amount=10, currency="USD")
        money.add(d="2026-11-04", kind="expense", category="サブスク", item="b", amount=10, currency="USD")
        self.assertEqual(money.set_rate("155.5", "2026-10"), 1)
        by = {e["item"]: e["amount_jpy"] for e in money.load()}
        self.assertEqual(by, {"a": 1555, "b": 1500})
        with self.assertRaises(money.BadInput):
            money.set_rate(5)
        with self.assertRaises(money.BadInput):
            money.set_rate(150, "2026-1")


class RecurringTest(Base):
    def test_monthly_end_of_month_idempotent_and_skip_deleted(self):
        r = money.recurring_add("Suno Pro", 1000, "JPY", "monthly", "2026-08-31")
        today = date(2026, 11, 19)
        self.assertEqual(money.apply_recurring(today), 3)             # 8/31・9/30・10/31（11/30 はまだ）
        self.assertEqual(sorted(e["date"] for e in money.load()), ["2026-08-31", "2026-09-30", "2026-10-31"])
        self.assertEqual(money.apply_recurring(today), 0)
        gone = next(e for e in money.load() if e["date"] == "2026-09-30")
        money.delete(gone["id"])
        self.assertEqual(money.apply_recurring(today), 0)             # 取り消した月は作り直さない
        self.assertEqual(money.apply_recurring(date(2026, 12, 1)), 1)  # 11/30 の分
        money.recurring_stop(r["id"], "2026-11")
        self.assertEqual(money.apply_recurring(date(2027, 3, 1)), 0)
        self.assertEqual(money.settings()["recurring"][0]["end"], "2026-11")

    def test_yearly_and_usd_pending(self):
        money.recurring_add("DistroKid Ultimate", "89.99", "USD", "yearly", "2024-10-11", pct=100)
        self.assertEqual(money.apply_recurring(date(2026, 11, 19)), 3)
        self.assertTrue(all(money.pending(e) for e in money.load()))
        self.assertIn("ドル円のレートが未設定", " ".join(money.status()["needs"]))
        money.set_rate(150)
        self.assertEqual(sorted((e["date"], e["amount_jpy"]) for e in money.load()),
                         [("2024-10-11", 13498), ("2025-10-11", 13498), ("2026-10-11", 13498)])

    def test_bad_recurring(self):
        with self.assertRaises(money.BadInput):
            money.recurring_add("x", 10, "JPY", "weekly", "2026-01-01")
        with self.assertRaises(money.BadInput):
            money.recurring_add("", 10)
        self.assertIsNone(money.recurring_stop("r000000"))


class DistroKidTest(Base):
    def rows(self):
        return [row("2026-11-05", "2026-10", "Spotify", "HOLLOWPLAN", "After Nights", 100, "0.30"),
                row("2026-11-05", "2026-10", "Apple Music", "Hollowplan", "After Nights", 20, "0.20"),
                row("2026-11-05", "2026-10", "Spotify", "DECLAN FAY", "Side B", 50, "0.15"),
                row("2026-11-05", "2026-10", "Spotify", "Somebody Else", "Other", 1, "0.01")]

    def test_import_groups_by_month_and_artist_and_is_idempotent(self):
        money.set_rate(150)
        f = tsv(self.t / "Downloads" / "DistroKid_1.tsv", self.rows())
        r = money.import_distrokid(f)
        self.assertEqual((r["rows"], r["added"], r["updated"], r["usd"]), (4, 3, 0, 0.66))
        self.assertEqual(r["unknown"], ["Somebody Else"])
        by = {e["artist_slug"]: e for e in money.load()}
        self.assertEqual((by["hollowplan"]["amount_orig"], by["hollowplan"]["amount_jpy"], by["hollowplan"]["date"]), (0.5, 75, "2026-10-01"))
        self.assertEqual(by["declan_fay"]["amount_jpy"], 22)
        r2 = money.import_distrokid(f)                                # 同じ明細をもう一度
        self.assertEqual((r2["added"], r2["updated"], len(money.load())), (0, 0, 3))
        self.assertEqual(money.summary(2026)["total"]["income"], 75 + 22 + 2)

    def test_later_report_adds_and_filtered_file_never_reduces(self):
        money.set_rate(150)
        money.import_distrokid(tsv(self.t / "a.tsv", self.rows()))
        more = self.rows() + [row("2026-12-05", "2026-10", "YouTube Music", "HOLLOWPLAN", "After Nights", 10, "0.10")]
        r = money.import_distrokid(tsv(self.t / "b.tsv", more))
        self.assertEqual((r["added"], r["updated"]), (0, 1))
        hp = lambda: next(e for e in money.load() if e["artist_slug"] == "hollowplan")  # noqa: E731
        self.assertEqual(hp()["amount_orig"], 0.6)
        money.import_distrokid(tsv(self.t / "c.tsv", [self.rows()[0]]))  # Spotify だけに絞った明細
        self.assertEqual(hp()["amount_orig"], 0.6)
        self.assertEqual(len(money.load()), 3)

    def test_find_statements_and_import_all(self):
        tsv(self.t / "Downloads" / "DistroKid_1.tsv", self.rows())
        (self.t / "Downloads" / "ほかの表.csv").write_text("date,song,streams\n2026-11-01,x,3\n", encoding="utf-8")
        tsv(self.t / "inbox" / "done" / "2026-11-01_old.tsv", [row("2026-10-05", "2026-09", "Spotify", "HOLLOWPLAN", "After Nights", 5, "0.02")])
        names = sorted(p.name for p in money.find_statements())
        self.assertEqual(names, ["2026-11-01_old.tsv", "DistroKid_1.tsv"])
        res = money.import_all()
        self.assertEqual(len(res), 2)
        self.assertEqual(len(money.load()), 4)
        self.assertEqual(money.settings()["last_import"], date.today().isoformat())
        self.assertTrue((self.t / "Downloads" / "DistroKid_1.tsv").exists())   # 明細は動かさない（成績の取り込みも読むため）

    def test_not_a_statement(self):
        p = self.t / "x.csv"
        p.write_text("a,b\n1,2\n", encoding="utf-8")
        self.assertFalse(money.is_distrokid(p))
        with self.assertRaises(money.BadInput):
            money.import_distrokid(p)

    def test_delete_button_not_offered_for_statement_rows(self):
        money.import_distrokid(tsv(self.t / "a.tsv", self.rows()))
        html = money.page("tok", 2026)
        self.assertIn("明細から", html)
        self.assertNotIn('"action": "money_delete"', html.replace("&quot;", '"'))


class ExportTest(Base):
    def test_csv_has_bom_and_rows(self):
        money.add(d="2026-11-03", kind="expense", category="機材", item="マイク", amount=10000, pct=50, memo="半分は私用")
        money.add(d="2025-01-03", kind="expense", category="機材", item="去年", amount=1)
        p = money.export_csv(2026)
        raw = p.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
        self.assertEqual(rows[0], money.EXPORT_HEAD)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][:7], ["2026-11-03", "費用", "機材", "マイク", "10000", "50", "5000"])

    def test_xlsx(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl が入っていない")
        money.add(d="2026-11-03", kind="income", category="その他", item="x", amount=700)
        out = money.export_xlsx(self.t / "収支.xlsx")
        wb = load_workbook(out)
        self.assertEqual(wb.sheetnames, ["記録", "月ごと"])
        self.assertEqual(wb["記録"].max_row, 2)
        months = {r[0]: r[1:] for r in wb["月ごと"].iter_rows(min_row=2, values_only=True)}
        self.assertEqual(months["2026-11"], (700, 0, 700))
        self.assertEqual(months["2026 年の合計"], (700, 0, 700))


class PageTest(Base):
    def test_empty_page_explains_first_steps(self):
        html = money.page("tok")
        self.assertIn("まだ記録がありません", html)
        self.assertIn("See excruciating detail", html)
        self.assertIn('const TOKEN="tok"', html)
        self.assertIn('class="on"', html)

    def test_page_with_records_escapes_and_has_actions(self):
        money.add(d=date.today().isoformat(), kind="expense", category="広告", item="<script>alert(1)</script>", amount=1200)
        money.recurring_add("Suno Pro", 10, "USD", "monthly", date.today().isoformat())
        html = money.page("tok", "abc")                               # 年がおかしくても今年で出す
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        plain = html.replace("&quot;", '"')
        for action in ("money_add", "money_delete", "money_import", "money_export", "money_recurring_add", "money_recurring_stop", "money_set_rate"):
            self.assertIn(f'"action": "{action}"', plain, action)
        self.assertIn("¥1,200", html)
        self.assertIn(f"{date.today().year} 年の月ごと", html)

    def test_status(self):
        st = money.status()
        self.assertEqual(st["month"]["profit"], 0)
        self.assertTrue(any("DistroKid の明細をまだ" in n for n in st["needs"]))
        money.add(d=date.today().isoformat(), kind="income", category="その他", item="x", amount=500)
        st = money.status()
        self.assertEqual((st["month"]["income"], st["year"]["income"]), (500, 500))


class WebCommandTest(Base):
    def test_good_commands(self):
        args, label = money.web_command("money_add", {"date": "2026-11-19", "kind": "expense", "category": "機材", "item": "--rm -rf",
                                                      "amount": "12,000", "currency": "JPY", "pct": "50", "artist": "hollowplan", "memo": "m"})
        self.assertEqual(args[0], "add")
        self.assertIn("--item=--rm -rf", args)                         # = でつないで、値が命令の引数として読まれないようにする
        self.assertIn("--amount=12000.0", args)
        self.assertIn("--artist=hollowplan", args)
        self.assertEqual(label, "お金の記録を足す")
        self.assertEqual(money.web_command("money_delete", {"id": "k0123abcde"})[0], ["delete", "k0123abcde"])
        self.assertEqual(money.web_command("money_import", {})[0], ["import"])
        self.assertEqual(money.web_command("money_export", {"year": "2026"})[0], ["export", "--year=2026"])
        self.assertEqual(money.web_command("money_set_rate", {"rate": "150", "month": "2026-10"})[0], ["set", "usd_jpy", "150", "--month=2026-10"])
        self.assertEqual(money.web_command("money_recurring_stop", {"id": "r0a1b2c"})[0], ["recurring", "stop", "r0a1b2c"])
        self.assertEqual(money.web_command("money_recurring_add", {"item": "Suno Pro", "amount": "10", "currency": "USD", "cycle": "monthly",
                                                                   "start": "2026-10-04", "category": "サブスク", "pct": ""})[0][:2], ["recurring", "add"])

    def test_bad_commands_are_refused(self):
        ok = {"date": "2026-11-19", "kind": "expense", "category": "機材", "item": "x", "amount": "100"}
        for bad in ({"amount": "abc"}, {"amount": "-1"}, {"date": "tomorrow"}, {"kind": "x"}, {"item": ""}, {"artist": "../etc"}, {"pct": "900"},
                    {"currency": "BTC"}):
            self.assertIsNone(money.web_command("money_add", {**ok, **bad}), bad)
        self.assertIsNone(money.web_command("money_delete", {"id": "../../x"}))
        self.assertIsNone(money.web_command("money_delete", {"id": "--help"}))
        self.assertIsNone(money.web_command("money_export", {"year": "99999"}))
        self.assertIsNone(money.web_command("money_set_rate", {"rate": "3"}))
        self.assertIsNone(money.web_command("money_set_rate", {"rate": "150", "month": "x"}))
        self.assertIsNone(money.web_command("money_recurring_stop", {"id": "k0123abcde"}))
        self.assertIsNone(money.web_command("money_recurring_add", {"item": "x", "amount": "1", "cycle": "daily", "start": "2026-01-01"}))
        self.assertIsNone(money.web_command("money_unknown", {}))


class CliTest(Base):
    """ダッシュボードのボタンと同じ道筋（web_command → money.py を別プロセスで）を通す"""

    def run_cli(self, args: list[str]) -> subprocess.CompletedProcess:
        env = {**os.environ, "MONEY_DIR": str(self.t / "money"), "MONEY_DOWNLOADS": str(self.t / "Downloads"),
               "MONEY_INBOX": str(self.t / "inbox"), "ARCHIVE_ROOT": str(self.t / "ssd" / "Postdog Records"), "PYTHONUNBUFFERED": "1"}
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "money.py"), *args], capture_output=True, text=True, env=env, cwd=ROOT)

    def test_buttons_end_to_end(self):
        (self.t / "ssd").mkdir()
        r = self.run_cli(money.web_command("money_set_rate", {"rate": "150"})[0])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("【結果】1 ドル 150 円にしました", r.stdout)
        r = self.run_cli(money.web_command("money_recurring_add", {"item": "Suno Pro", "amount": "10", "currency": "USD", "cycle": "monthly",
                                                                   "start": date.today().replace(day=1).isoformat(), "category": "サブスク", "pct": "100"})[0])
        self.assertIn("記録を 1 件作りました", r.stdout, r.stdout + r.stderr)
        r = self.run_cli(money.web_command("money_add", {"date": date.today().isoformat(), "kind": "expense", "category": "機材", "item": "マイク",
                                                         "amount": "10000", "currency": "JPY", "pct": "50"})[0])
        self.assertIn("【結果】記録しました", r.stdout, r.stdout + r.stderr)
        f = tsv(self.t / "Downloads" / "DistroKid_x.tsv", [row("2026-11-05", f"{date.today():%Y-%m}", "Spotify", "HOLLOWPLAN", "After Nights", 100, "1.00")])
        r = self.run_cli(["import"])
        self.assertIn("明細 1 個を取り込みました（新しい記録 1 件", r.stdout, r.stdout + r.stderr)
        self.assertTrue(f.exists())
        r = self.run_cli(["export"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.t / "money" / f"収支_{date.today().year}.csv").exists())
        try:
            import openpyxl  # noqa: F401
            self.assertTrue((self.t / "ssd" / "Postdog Records" / "01_管理表" / "収支.xlsx").exists(), r.stdout)
        except ImportError:
            pass
        r = self.run_cli(["summary"])
        self.assertIn("合計", r.stdout)
        sm = money.summary()
        self.assertEqual((sm["total"]["income"], sm["total"]["expense"]), (150, 1500 + 5000))
        r = self.run_cli(["delete", "zzzzzz"])
        self.assertEqual(r.returncode, 1)
        r = self.run_cli(["add", "--kind=expense", "--item=x", "--amount=abc"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("入れた値を確かめてください", r.stdout)

    def test_monthly_job(self):
        r = self.run_cli(["monthly"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("【結果】固定費 0 件", r.stdout)


if __name__ == "__main__":
    unittest.main()
