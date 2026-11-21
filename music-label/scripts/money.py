#!/usr/bin/env python3
"""
お金の記録（収入と費用を 1 か所にまとめて、月ごと・年ごとに見る。決定 175）

  何を記録するか
    ・収入 … 配信の売上（DistroKid の明細から自動で）・その他（手で）
    ・費用 … サブスク（Suno・DistroKid・Claude・Notion など）・API 利用料（Claude・OpenAI）・機材・ソフト・広告・その他
             毎月・毎年決まって出ていくもの（固定費）は 1 回登録すれば、月ごとの記録を自動で作る
  事業の割合（按分）：自宅の回線など、仕事と私用で分けるものは「事業の割合 %」を入れる。集計には、その割合ぶんだけを費用として数える
  ドルの金額：1 ドル何円かは自分で入れる（money.py set usd_jpy 150。月ごとに変えるときは --month 2026-10）。外の為替サービスは呼ばない
               レートが決まるまでは「レート待ち」として 0 円で数え、決めたときに円の金額を入れる

  置き場所
    out/money/ledger.json     … 全部の記録（1 件ずつ）
    out/money/settings.json   … ドル円のレート・固定費の一覧・最後に明細を取り込んだ日
    out/money/収支_<年>.csv    … 表計算ソフトで開ける書き出し（1 行 1 件。税理士に渡せる形）
    SSD の 01_管理表/収支.xlsx … SSD がつながっているときだけ（記録の全部と、月ごとの集計）

  DistroKid の明細（Bank → See excruciating detail → Download）は、ダウンロードフォルダか out/metrics/inbox（読み終わりの done も）から
  自動で見つける。同じ明細を何度取り込んでも二重にならない（「売れた月 × アーティスト」ごとに 1 件。中身は報告日 × ストアごとに覚える）。
  収入の日付は「売れた月」の 1 日にしている（振り込まれた日ではない）。

  ※ これは記録を見やすくするための道具で、税金の判断はしない。申告のときは税理士か税務署に確かめること。

使い方
  python3 scripts/money.py summary [--year 2026]            # 月ごとの収入・費用・差し引き
  python3 scripts/money.py add --date 2026-11-19 --kind expense --category 機材 --item "マイク" --amount 12000 [--pct 50] [--memo …]
  python3 scripts/money.py add --date 2026-11-04 --kind expense --category サブスク --item "Suno Pro" --amount 10 --currency USD
  python3 scripts/money.py delete <id>                      # 1 件を取り消す
  python3 scripts/money.py import [明細のファイル]            # DistroKid の明細を取り込む（ファイルを省くと自動で探す）
  python3 scripts/money.py recurring add --item "Suno Pro" --amount 10 --currency USD --cycle monthly --start 2026-10-04
  python3 scripts/money.py recurring stop <番号> / list
  python3 scripts/money.py apply                            # 固定費の、まだ無い月の記録を作る
  python3 scripts/money.py set usd_jpy 150 [--month 2026-10]
  python3 scripts/money.py export [--year 2026]             # CSV（と SSD の収支.xlsx）に書き出す
  python3 scripts/money.py monthly                          # 毎月 1 日の定期実行：固定費 → 明細の取り込み → 書き出し
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, read_json, step  # noqa: E402

# 置き場所（試すときだけ、環境変数 MONEY_DIR・MONEY_DOWNLOADS・MONEY_INBOX で別の場所に向けられる）
DIR = Path(os.environ.get("MONEY_DIR") or OUT / "money")
DOWNLOADS = Path(os.environ.get("MONEY_DOWNLOADS") or Path.home() / "Downloads")
PLAYS_IMPORTED = Path(os.environ.get("MONEY_PLAYS") or OUT / "plays" / "imported")   # 再生数の取り込みが明細を片付ける先
INBOX = Path(os.environ.get("MONEY_INBOX") or OUT / "metrics" / "inbox")   # collect_metrics.py の受け取り箱（読み終わりは done/ に移る）
BOOK_NAME = "収支.xlsx"

KINDS = {"income": "収入", "expense": "費用"}
CATEGORIES = {"income": ["配信の売上", "その他"],
              "expense": ["サブスク", "API 利用料", "機材", "ソフト", "広告", "その他"]}
CURRENCIES = ("JPY", "USD")
SOURCES = {"manual": "手で", "recurring": "固定費", "distrokid": "DistroKid の明細", "api": "API"}
MAX_AMOUNT = 1_000_000_000
# よくある固定費の見本（金額は docs/11_plans_and_costs.md に書いてあるものだけ。始めた日は自分で入れる。書いていないものは金額を空にしてある）
SEEDS = [
    {"item": "Suno Pro", "amount": "10", "currency": "USD", "cycle": "monthly", "category": "サブスク"},
    {"item": "DistroKid Ultimate（5 組）", "amount": "89.99", "currency": "USD", "cycle": "yearly", "category": "サブスク"},
    {"item": "Supabase Pro", "amount": "25", "currency": "USD", "cycle": "monthly", "category": "サブスク"},
    {"item": "Claude（月額プラン）", "amount": "", "currency": "USD", "cycle": "monthly", "category": "サブスク"},
    {"item": "Notion", "amount": "", "currency": "JPY", "cycle": "monthly", "category": "サブスク"},
]


# ---------------------------------------------------------------------------
# 読み書き（途中で止まっても壊れないように、別名で書いてから置き換える）
def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def load() -> list[dict]:
    p = DIR / "ledger.json"
    return read_json(p).get("entries", []) if p.exists() else []


def save(entries: list[dict]) -> None:
    _write(DIR / "ledger.json", {"entries": sorted(entries, key=lambda e: (e.get("date", ""), e.get("id", "")))})


def settings() -> dict:
    p = DIR / "settings.json"
    s = read_json(p) if p.exists() else {}
    s.setdefault("usd_jpy", None)
    s.setdefault("usd_jpy_by_month", {})
    s.setdefault("recurring", [])
    s.setdefault("skipped", [])
    return s


def save_settings(s: dict) -> None:
    _write(DIR / "settings.json", s)


# ---------------------------------------------------------------------------
# 値の確かめ
class BadInput(ValueError):
    pass


def _date(v) -> str:
    try:
        return date.fromisoformat(str(v or "").strip()[:10]).isoformat()
    except ValueError:
        raise BadInput(f"日付は 2026-11-19 の形で入れてください（入っていたもの：{str(v)[:20]}）") from None


def _month(v) -> str:
    v = str(v or "").strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
        raise BadInput(f"月は 2026-11 の形で入れてください（入っていたもの：{v[:20]}）")
    return v


def _amount(v, allow_zero: bool = False) -> float:
    try:
        x = float(str(v).replace(",", "").replace("¥", "").replace("$", "").replace("円", "").strip())
    except (TypeError, ValueError):
        raise BadInput(f"金額は数字で入れてください（入っていたもの：{str(v)[:20]}）") from None
    if x != x or x < 0 or (x == 0 and not allow_zero) or x >= MAX_AMOUNT:
        raise BadInput("金額は 0 より大きい数字で入れてください")
    return round(x, 2)


def _pct(v) -> int:
    try:
        x = int(round(float(str(v if v not in (None, "") else 100).replace("%", "").strip())))
    except (TypeError, ValueError):
        raise BadInput("事業の割合は 0〜100 の数字で入れてください") from None
    if not 0 <= x <= 100:
        raise BadInput("事業の割合は 0〜100 の数字で入れてください")
    return x


def _text(v, n: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", unicodedata.normalize("NFC", str(v or ""))).strip()[:n]


def _currency(v) -> str:
    c = str(v or "JPY").strip().upper()
    c = {"円": "JPY", "¥": "JPY", "ドル": "USD", "$": "USD"}.get(c, c)
    if c not in CURRENCIES:
        raise BadInput("通貨は JPY（円）か USD（ドル）です")
    return c


def _kind(v) -> str:
    k = str(v or "").strip().lower()
    k = {"収入": "income", "費用": "expense"}.get(str(v or "").strip(), k)
    if k not in KINDS:
        raise BadInput("種類は 収入（income）か 費用（expense）です")
    return k


def _category(kind: str, v) -> str:
    c = _text(v, 20)
    return c if c in CATEGORIES[kind] else "その他"


def _key_id(key: str) -> str:
    return "k" + hashlib.sha1(key.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# ドル → 円
def rate_for(month: str, s: dict | None = None) -> float | None:
    s = s or settings()
    r = (s.get("usd_jpy_by_month") or {}).get(month) or s.get("usd_jpy")
    return float(r) if r else None


def _to_jpy(amount: float, currency: str, month: str, s: dict | None = None) -> tuple[int, float | None]:
    """(円の金額, 使ったレート)。ドルでレートが決まっていなければ (0, None)＝レート待ち"""
    if currency == "JPY":
        return int(round(amount)), None
    r = rate_for(month, s)
    return (int(round(amount * r)), r) if r else (0, None)


def pending(e: dict) -> bool:
    """ドルの記録で、まだ円に直せていないもの"""
    return e.get("currency") == "USD" and not e.get("rate")


def set_rate(value, month: str | None = None) -> int:
    """ドル円のレートを決める。月を指定するとその月のドルの記録を全部そのレートに、指定しなければレート待ちの記録だけを円に直す。直した件数"""
    r = _amount(value)
    if not 50 <= r <= 500:
        raise BadInput("1 ドルが何円か（例 150）を入れてください")
    s = settings()
    if month:
        s["usd_jpy_by_month"][_month(month)] = r
    else:
        s["usd_jpy"] = r
    save_settings(s)
    entries, n = load(), 0
    for e in entries:
        if e.get("currency") != "USD":
            continue
        m = e["date"][:7]
        if (month and m == month) or (not month and pending(e)):
            new = rate_for(m, s)
            if new and e.get("rate") != new:
                e["rate"], e["amount_jpy"] = new, int(round(float(e["amount_orig"]) * new))
                n += 1
    if n:
        save(entries)
    return n


# ---------------------------------------------------------------------------
# 記録を足す・取り消す
def make_entry(d, kind, category, item, amount, currency="JPY", pct=100, artist_slug="", memo="", source="manual",
               key: str | None = None, s: dict | None = None) -> dict:
    kind = _kind(kind)
    d, cur, amt = _date(d), _currency(currency), _amount(amount, allow_zero=(source == "distrokid"))
    item = _text(item, 80)
    if not item:
        raise BadInput("内容（何のお金か）を入れてください")
    jpy, rate = _to_jpy(amt, cur, d[:7], s)
    e = {"id": _key_id(key) if key else secrets.token_hex(4), "date": d, "kind": kind, "category": _category(kind, category),
         "item": item, "amount_jpy": jpy, "amount_orig": amt, "currency": cur, "rate": rate,
         "artist_slug": _text(artist_slug, 40), "source": source if source in SOURCES else "manual", "memo": _text(memo, 200),
         "business_pct": 100 if kind == "income" else _pct(pct)}
    if key:
        e["key"] = key
    return e


def add(**kw) -> dict:
    e = make_entry(**kw)
    entries = load()
    entries.append(e)
    save(entries)
    return e


def delete(entry_id: str) -> dict | None:
    entries = load()
    hit = next((e for e in entries if e.get("id") == entry_id), None)
    if not hit:
        return None
    save([e for e in entries if e.get("id") != entry_id])
    if hit.get("source") == "recurring" and hit.get("key"):   # 固定費から作った記録は、作り直さないように覚えておく
        s = settings()
        if hit["key"] not in s["skipped"]:
            s["skipped"].append(hit["key"])
            save_settings(s)
    return hit


# ---------------------------------------------------------------------------
# 固定費（毎月・毎年）
def recurring_add(item, amount, currency="JPY", cycle="monthly", start=None, category="サブスク", pct=100, end=None) -> dict:
    cycle = {"毎月": "monthly", "毎年": "yearly"}.get(str(cycle).strip(), str(cycle).strip().lower())
    if cycle not in ("monthly", "yearly"):
        raise BadInput("周期は monthly（毎月）か yearly（毎年）です")
    item = _text(item, 80)
    if not item:
        raise BadInput("内容（サービスの名前など）を入れてください")
    r = {"id": "r" + secrets.token_hex(3), "item": item, "amount": _amount(amount), "currency": _currency(currency), "cycle": cycle,
         "start": _date(start or date.today().isoformat()), "end": _month(end) if end else None,
         "category": _category("expense", category), "business_pct": _pct(pct)}
    s = settings()
    s["recurring"].append(r)
    save_settings(s)
    return r


def recurring_stop(rid: str, end: str | None = None) -> dict | None:
    """固定費を止める（その月の分までは残す。次の月から作らない）"""
    s = settings()
    r = next((x for x in s["recurring"] if x["id"] == rid), None)
    if not r:
        return None
    r["end"] = _month(end) if end else date.today().isoformat()[:7]
    save_settings(s)
    return r


def _due_dates(r: dict, today: date) -> list[date]:
    """その固定費の、今日までの支払い日（毎月は同じ日、月末を超える日はその月の末日。毎年は同じ月日）"""
    start = date.fromisoformat(r["start"])
    last = today
    if r.get("end"):
        y, m = map(int, r["end"].split("-"))
        last = min(today, date(y, m, calendar.monthrange(y, m)[1]))
    out, y, m = [], start.year, start.month
    while (y, m) <= (last.year, last.month):
        d = date(y, m, min(start.day, calendar.monthrange(y, m)[1]))
        if start <= d <= last:
            out.append(d)
        if r["cycle"] == "yearly":
            y += 1
        else:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def apply_recurring(today: date | None = None) -> int:
    """固定費の、まだ記録が無い月の分を作る（何度動かしても二重にならない）。作った件数"""
    today = today or date.today()
    s, entries = settings(), load()
    have = {e.get("key") for e in entries if e.get("key")} | set(s["skipped"])
    n = 0
    for r in s["recurring"]:
        for d in _due_dates(r, today):
            key = f"recurring:{r['id']}:{d:%Y-%m}"
            if key in have:
                continue
            entries.append(make_entry(d.isoformat(), "expense", r["category"], r["item"], r["amount"], r["currency"],
                                      r.get("business_pct", 100), memo="固定費（" + ("毎月" if r["cycle"] == "monthly" else "毎年") + "）",
                                      source="recurring", key=key, s=s))
            have.add(key)
            n += 1
    if n:
        save(entries)
    return n


# ---------------------------------------------------------------------------
# DistroKid の明細
def _norm(sv: str) -> str:
    return re.sub(r"[^a-z0-9぀-ヿ一-鿿]", "", unicodedata.normalize("NFKC", str(sv or "")).lower())


def artist_map() -> dict[str, dict]:
    """名前（記号・大文字小文字を無視）→ 組。外す手続き中・外した組も入れる（昔の売上が届くため）"""
    arts = list(all_artists(include_retiring=True))
    retired = ROOT / "templates" / "archive" / "retired"
    if retired.exists():
        for p in sorted(retired.glob("*.json")):
            if not p.stem.startswith("_"):
                try:
                    arts.append(read_json(p))
                except (OSError, ValueError):
                    pass
    return {_norm(a.get("name") or a.get("slug")): a for a in arts if a.get("slug")}


def _col(first: dict, *names) -> str | None:
    low = {k.strip().lower(): k for k in first if k}
    for n in names:
        for k_low, k in low.items():
            if k_low == n or k_low.startswith(n):
                return k
    return None


def _read_table(p: Path) -> list[dict]:
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    head = text.split("\n", 1)[0]
    delim = "\t" if head.count("\t") > head.count(",") else ","
    return list(csv.DictReader(text.splitlines(), delimiter=delim))


def is_distrokid(p: Path) -> bool:
    """DistroKid の明細か（最初の行に「売れた月」と「売上」の列があるか）"""
    try:
        with p.open(encoding="utf-8-sig", errors="replace") as f:
            head = f.readline(4000).lower()
    except OSError:
        return False
    return "sale month" in head and "earnings" in head


def find_statements() -> list[Path]:
    """明細らしいファイルを探す：ダウンロードフォルダ（直下だけ）・成績の受け取り箱・その読み終わり・
    再生数の取り込み（plays.py）が片付けた先（ダウンロードから out/plays/imported/ に移される。決定 172）"""
    out = []
    for d in (DOWNLOADS, INBOX, INBOX / "done", PLAYS_IMPORTED):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            try:
                if (p.is_file() and not p.name.startswith(".") and p.suffix.lower() in (".tsv", ".csv", ".txt")
                        and p.stat().st_size < 80 * 1024 * 1024 and is_distrokid(p)):
                    out.append(p)
            except OSError:
                continue
    return out


def import_distrokid(path: Path) -> dict:
    """明細 1 つを取り込む。「売れた月 × アーティスト」ごとに収入 1 件。同じ明細を何度読んでも増えない
    （報告日 × ストアごとの金額を覚えて、同じものは上書き・新しいものだけ足す。絞り込んだ明細を読んでも減らない）"""
    data = _read_table(path)
    if not data:
        return {"file": path.name, "rows": 0, "added": 0, "updated": 0, "usd": 0.0, "unknown": []}
    first = data[0]
    c_month, c_earn = _col(first, "sale month"), _col(first, "earnings")
    if not (c_month and c_earn):
        raise BadInput(f"{path.name}：DistroKid の明細の列（Sale Month・Earnings）が見つかりません（列：{', '.join(k for k in first if k)[:120]}）")
    c_artist, c_store, c_rep = _col(first, "artist"), _col(first, "store"), _col(first, "reporting date", "reporting")
    amap, groups, names, rows = artist_map(), {}, {}, 0
    for r in data:
        m = re.match(r"\d{4}-\d{2}", str(r.get(c_month) or "").strip())
        try:
            usd = float(str(r.get(c_earn) or "0").replace("$", "").replace(",", "").strip() or 0)
        except ValueError:
            continue
        if not m:
            continue
        raw = str(r.get(c_artist) or "").strip() if c_artist else ""
        part = f"{str(r.get(c_rep) or '').strip()[:10] if c_rep else ''}|{str(r.get(c_store) or '').strip() if c_store else ''}"
        g = groups.setdefault((m.group(0), _norm(raw)), {})
        g[part] = g.get(part, 0.0) + usd
        names[_norm(raw)] = raw
        rows += 1
    s, entries = settings(), load()
    by_key = {e["key"]: e for e in entries if e.get("key")}
    added = updated = 0
    unknown = set()
    for (month, an), parts in sorted(groups.items()):
        key = f"distrokid:{month}:{an}"
        art = amap.get(an)
        if not art and names[an]:
            unknown.add(names[an])
        e = by_key.get(key)
        if e is None:
            e = make_entry(f"{month}-01", "income", "配信の売上", f"DistroKid 配信の売上（{month} 分）{'' if art else '：' + (names[an] or '組の名前なし')}"[:80],
                           0, "USD", artist_slug=art["slug"] if art else "", source="distrokid", key=key, s=s)
            e["parts"] = {}
            entries.append(e)
            by_key[key] = e
            added += 1
            before = None
        else:
            before = e.get("amount_orig")
        e.setdefault("parts", {}).update({k: round(v, 6) for k, v in parts.items()})
        e["amount_orig"] = round(sum(e["parts"].values()), 2)
        reported = max((k.split("|")[0] for k in e["parts"]), default="")
        e["memo"] = f"明細から（報告 {len(e['parts'])} 件" + (f"・最後の報告 {reported}" if reported else "") + "）"
        e["amount_jpy"], e["rate"] = _to_jpy(e["amount_orig"], "USD", month, s)
        if before is not None and before != e["amount_orig"]:
            updated += 1
    save(entries)
    s["last_import"] = date.today().isoformat()
    save_settings(s)
    return {"file": path.name, "rows": rows, "added": added, "updated": updated,
            "usd": round(sum(sum(p.values()) for p in groups.values()), 2), "unknown": sorted(unknown)}


def import_all(path: Path | None = None) -> list[dict]:
    files = [path] if path else find_statements()
    return [import_distrokid(p) for p in files]


# ---------------------------------------------------------------------------
# 集計
def effective(e: dict) -> int:
    """集計に数える円の金額（費用は事業の割合ぶんだけ）"""
    v = int(e.get("amount_jpy") or 0)
    return v if e.get("kind") == "income" else int(round(v * int(e.get("business_pct", 100)) / 100))


def summary(year: int | None = None, entries: list[dict] | None = None) -> dict:
    year = year or date.today().year
    entries = load() if entries is None else entries
    months = [{"month": f"{year}-{m:02d}", "income": 0, "expense": 0, "profit": 0} for m in range(1, 13)]
    cats, arts, wait = {}, {}, 0
    for e in entries:
        if not str(e.get("date", "")).startswith(f"{year}-"):
            continue
        v, row = effective(e), months[int(e["date"][5:7]) - 1]
        if pending(e):
            wait += 1
        if e["kind"] == "income":
            row["income"] += v
            slug = e.get("artist_slug") or ""
            arts[slug] = arts.get(slug, 0) + v
        else:
            row["expense"] += v
            cats[e.get("category", "その他")] = cats.get(e.get("category", "その他"), 0) + v
    for r in months:
        r["profit"] = r["income"] - r["expense"]
    tot = {k: sum(r[k] for r in months) for k in ("income", "expense", "profit")}
    return {"year": year, "months": months, "total": tot, "pending": wait,
            "categories": sorted(cats.items(), key=lambda x: -x[1]), "artists": sorted(arts.items(), key=lambda x: -x[1]),
            "years": sorted({int(e["date"][:4]) for e in entries if e.get("date")} | {date.today().year})}


def status() -> dict:
    """ダッシュボードのホームに出す：今月と今年の合計、やっておくこと（軽い。通信しない）"""
    try:
        entries, s, today = load(), settings(), date.today()
        sm = summary(today.year, entries)
        needs = []
        usd_fixed = any(r.get("currency") == "USD" and not r.get("end") for r in s["recurring"])
        if any(pending(e) for e in entries) or (usd_fixed and not s.get("usd_jpy")):
            needs.append("ドル円のレートが未設定（1 ドル何円かを入れると、ドルの記録が円になります）")
        last = s.get("last_import") or ""
        if not last:
            needs.append("DistroKid の明細をまだ取り込んでいません（配信が始まって売上が出たら）")
        elif last[:7] != today.isoformat()[:7]:
            needs.append("今月はまだ DistroKid の明細を取り込んでいません")
        return {"month": sm["months"][today.month - 1], "year": {"year": today.year, **sm["total"]}, "needs": needs,
                "entries": len(entries), "pending": sm["pending"]}
    except Exception as e:  # noqa: BLE001  壊れたファイルがあってもホームは出す
        return {"month": {"income": 0, "expense": 0, "profit": 0}, "year": {"income": 0, "expense": 0, "profit": 0}, "needs": [], "error": str(e)}


# ---------------------------------------------------------------------------
# 書き出し
EXPORT_HEAD = ["日付", "種類", "区分", "内容", "金額（円）", "事業の割合（%）", "集計に数える額（円）", "元の金額", "通貨", "レート",
               "組", "入力元", "メモ", "番号"]


def _export_row(e: dict, names: dict[str, str]) -> list:
    return [e.get("date", ""), KINDS.get(e.get("kind"), ""), e.get("category", ""), e.get("item", ""), int(e.get("amount_jpy") or 0),
            int(e.get("business_pct", 100)), effective(e), e.get("amount_orig", ""), e.get("currency", ""),
            e.get("rate") or ("レート待ち" if pending(e) else ""), names.get(e.get("artist_slug") or "", e.get("artist_slug") or ""),
            SOURCES.get(e.get("source"), e.get("source", "")), e.get("memo", ""), e.get("id", "")]


def _names() -> dict[str, str]:
    return {a["slug"]: a.get("name") or a["slug"] for a in artist_map().values()}


def export_csv(year: int | None = None) -> Path:
    year = year or date.today().year
    names = _names()
    p = DIR / f"収支_{year}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8-sig", newline="") as f:   # BOM つき：Excel でそのまま開ける
        w = csv.writer(f)
        w.writerow(EXPORT_HEAD)
        for e in load():
            if str(e.get("date", "")).startswith(f"{year}-"):
                w.writerow(_export_row(e, names))
    return p


def target_path() -> tuple[Path | None, str]:
    import _archive as archive
    base, why = archive.root()
    if base is None:
        return None, why
    return archive.child(base, archive.BOOK_DIR) / BOOK_NAME, str(base)


def export_xlsx(out: Path | None = None) -> Path | None:
    """SSD の 01_管理表/収支.xlsx（記録の全部と、年ごとの月の集計）。SSD が無ければ書かない"""
    path, why = (out, "") if out else target_path()
    if path is None:
        print(f"   － SSD の収支.xlsx は書きませんでした：{why}")
        return None
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        print("   － SSD の収支.xlsx は書きませんでした：表計算の部品（openpyxl）が入っていません（python3 -m pip install --user openpyxl）")
        return None
    entries, names = load(), _names()
    wb = Workbook()
    ws = wb.active
    ws.title = "記録"
    ws.append(EXPORT_HEAD)
    for e in entries:
        ws.append(_export_row(e, names))
    for i, w in enumerate([12, 8, 14, 40, 14, 10, 16, 12, 8, 10, 20, 16, 40, 14], 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    ws.freeze_panes = "A2"
    ms = wb.create_sheet("月ごと")
    ms.append(["月", "収入（円）", "費用（円）", "差し引き（円）"])
    for y in sorted({int(e["date"][:4]) for e in entries if e.get("date")} or {date.today().year}):
        sm = summary(y, entries)
        for r in sm["months"]:
            ms.append([r["month"], r["income"], r["expense"], r["profit"]])
        ms.append([f"{y} 年の合計", sm["total"]["income"], sm["total"]["expense"], sm["total"]["profit"]])
        for c in ms[ms.max_row]:
            c.font = Font(bold=True)
    for sh in (ws, ms):
        for c in sh[1]:
            c.font = Font(bold=True)
    for col_letter in "ABCD":
        ms.column_dimensions[col_letter].width = 18
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# ダッシュボードのページ（/money）
PAGE_CSS = """<style>
.bar{display:inline-block;height:8px;border-radius:4px;vertical-align:middle;min-width:1px}
.bar.in{background:var(--good)} .bar.ex{background:var(--crit)} .bars{display:grid;gap:3px;min-width:120px}
.plus{color:var(--good)} .minus{color:var(--crit)} .years a{margin-right:10px} .years b{margin-right:10px}
.form{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;align-items:end}
.form label{min-width:0} .form input,.form select{width:100%;min-width:0}
.form .wide{grid-column:span 2} @media (max-width:520px){.form .wide{grid-column:auto}}
.note{border-left:3px solid var(--warn);padding:6px 12px;margin:0}
</style>"""

PAGE_JS = """
(function(){const kind=document.getElementById("m-kind"),cat=document.getElementById("m-cat"),pct=document.getElementById("m-pct");
  function sync(){if(!kind||!cat)return;let first=null;for(const o of cat.options){const ok=o.dataset.kind===kind.value;o.hidden=!ok;o.disabled=!ok;if(ok&&!first)first=o}
    if(cat.selectedOptions[0]&&cat.selectedOptions[0].disabled&&first)cat.value=first.value;if(pct)pct.closest("label").hidden=kind.value==="income"}
  if(kind){kind.addEventListener("change",sync);sync()}
  document.addEventListener("click",e=>{const b=e.target.closest("button[data-seed]");if(!b)return;const s=JSON.parse(b.dataset.seed);
    for(const [k,v] of Object.entries(s)){const el=document.getElementById("r-"+k);if(el)el.value=v}
    const a=document.getElementById("r-amount");if(a){a.focus();a.scrollIntoView({block:"center"})}})})();
"""


def _signed(v: int) -> str:
    import _ui
    cls = "plus" if v > 0 else ("minus" if v < 0 else "")
    return f'<span class="{cls}">{"−" if v < 0 else ""}{_ui.yen(abs(v))}</span>'


def _input(label: str, id_: str, typ: str = "text", value: str = "", attrs: str = "", cls: str = "") -> str:
    import _ui
    return (f'<label class="field {cls}">{_ui.E(label)}<input id="{id_}" type="{typ}" value="{_ui.E(str(value))}" {attrs}></label>')


def _select(label: str, id_: str, options: list[tuple], cls: str = "") -> str:
    import _ui
    opts = "".join(f'<option value="{_ui.E(v)}"{" " + extra if extra else ""}>{_ui.E(t)}</option>' for v, t, extra in options)
    return f'<label class="field {cls}">{_ui.E(label)}<select id="{id_}">{opts}</select></label>'


def page(token: str, year=None) -> str:
    import _ui
    E, today = _ui.E, date.today()
    entries, s = load(), settings()
    try:
        year = int(year) if year else today.year
    except (TypeError, ValueError):
        year = today.year
    year = year if 2000 <= year <= 2100 else today.year
    sm, now = summary(year, entries), summary(today.year, entries)
    names = _names()
    this = now["months"][today.month - 1]
    body = ['<h1>お金の記録</h1><p class="sub">収入（配信の売上）と費用（サブスク・API・機材など）を月ごとに見るページです。'
            '記録を見やすくするための道具で、税金の判断はしません（申告のときは税理士か税務署に確かめてください）。</p>']
    body.append(_ui.kpis([(_ui.yen(this["income"]), "今月の収入", f"{today.month} 月"), (_ui.yen(this["expense"]), "今月の費用", "事業の割合ぶん"),
                          (("−" if this["profit"] < 0 else "") + _ui.yen(abs(this["profit"])), "今月の差し引き", "収入 − 費用"),
                          (("−" if now["total"]["profit"] < 0 else "") + _ui.yen(abs(now["total"]["profit"])), "今年の差し引き", f"{today.year} 年")]))
    needs = status().get("needs", [])
    if not entries and not s["recurring"]:
        body.append('<section class="card"><h2>まだ記録がありません</h2><p>はじめに、次の 3 つをすると表ができます。</p><ol>'
                    '<li><b>ドル円のレート</b>を入れる（下の「ドル円のレート」。Suno や DistroKid はドル払いのため）</li>'
                    '<li><b>固定費</b>を足す（下の「固定費」。Suno・DistroKid など、毎月・毎年決まって払うもの。始めた日を入れると過去の月の分も作ります）</li>'
                    '<li>配信が始まって売上が出たら、DistroKid の <b>Bank → See excruciating detail → Download</b> で明細を保存して「DistroKid の明細を取り込む」</li>'
                    '</ol></section>')
    elif needs:
        body.append('<section class="card"><h2>やっておくこと</h2>' + "".join(f'<p class="note">{E(n)}</p>' for n in needs) + "</section>")

    # 月ごと
    top = max([1] + [max(r["income"], r["expense"]) for r in sm["months"]])
    rows = []
    for r in sm["months"]:
        future = r["month"] > today.isoformat()[:7]
        bars = (f'<div class="bars"><span class="bar in" style="width:{r["income"] * 100 // top}%"></span>'
                f'<span class="bar ex" style="width:{r["expense"] * 100 // top}%"></span></div>')
        rows.append([f'{int(r["month"][5:])} 月', "" if future and not r["income"] else _ui.yen(r["income"]),
                     "" if future and not r["expense"] else _ui.yen(r["expense"]),
                     "" if future and not (r["income"] or r["expense"]) else _signed(r["profit"]), bars])
    rows.append([f"<b>{year} 年の合計</b>", f'<b>{_ui.yen(sm["total"]["income"])}</b>', f'<b>{_ui.yen(sm["total"]["expense"])}</b>',
                 f'<b>{_signed(sm["total"]["profit"])}</b>', ""])
    years = "".join(f"<b>{y} 年</b>" if y == year else f'<a href="/money?year={y}">{y} 年</a>' for y in sm["years"])
    body.append(f'<section class="card"><h2>{year} 年の月ごと</h2><p class="years sub">{years}</p>'
                + _ui.table(["月", "収入", "費用", "差し引き", "緑＝収入・赤＝費用"], rows, num=(1, 2, 3), raw=(0, 1, 2, 3, 4))
                + (f'<p class="sub">レート待ちのドルの記録が {sm["pending"]} 件あります（0 円で数えています）。</p>' if sm["pending"] else "")
                + "</section>")

    # 内訳
    cat_rows = [[c, _ui.yen(v), f'{v * 100 // max(1, sm["total"]["expense"])}%'] for c, v in sm["categories"]]
    art_rows = [[names.get(a, a) or "（組が分からないもの・その他）", _ui.yen(v)] for a, v in sm["artists"]]
    body.append('<div class="grid2">'
                f'<section class="card"><h2>費用の内訳（{year} 年）</h2>'
                + (_ui.table(["区分", "金額", "割合"], cat_rows, num=(1, 2)) if cat_rows else '<p class="empty">この年の費用の記録はまだありません。</p>') + "</section>"
                f'<section class="card"><h2>組ごとの収入（{year} 年）</h2>'
                + (_ui.table(["組", "収入"], art_rows, num=(1,)) if art_rows else '<p class="empty">この年の収入の記録はまだありません。</p>') + "</section></div>")

    # 記録を足す
    cat_opts = [(c, c, f'data-kind="{k}"') for k in ("expense", "income") for c in CATEGORIES[k]]
    art_opts = [("", "（組を選ばない）", "")] + [(sl, n, "") for sl, n in sorted(names.items(), key=lambda x: x[1])]
    body.append('<section class="card"><h2>記録を足す</h2><div class="form">'
                + _input("日付", "m-date", "date", today.isoformat())
                + _select("種類", "m-kind", [("expense", "費用", ""), ("income", "収入", "")])
                + _select("区分", "m-cat", cat_opts)
                + _input("内容（何のお金か）", "m-item", attrs='maxlength="80" placeholder="例：マイク・広告費"', cls="wide")
                + _input("金額", "m-amount", "number", attrs='min="0" step="0.01" inputmode="decimal"')
                + _select("通貨", "m-cur", [("JPY", "円", ""), ("USD", "ドル", "")])
                + _input("事業の割合 %", "m-pct", "number", "100", 'min="0" max="100" step="1" title="仕事と私用で分けるものは、仕事の割合を入れる"')
                + _select("組（任意）", "m-artist", art_opts)
                + _input("メモ（任意）", "m-memo", attrs='maxlength="200"', cls="wide")
                + "</div><p>" + _ui.btn({"action": "money_add", "label": "記録する"}, primary=True,
                                        fields={"date": "m-date", "kind": "m-kind", "category": "m-cat", "item": "m-item", "amount": "m-amount",
                                                "currency": "m-cur", "pct": "m-pct", "artist": "m-artist", "memo": "m-memo"})
                + "</p></section>")

    # 固定費
    rec_rows = []
    for r in s["recurring"]:
        amt = (f'${r["amount"]:,.2f}' if r["currency"] == "USD" else _ui.yen(r["amount"]))
        state = _ui.badge("info", f'{r["end"]} まで') if r.get("end") else _ui.badge("done", "続いている")
        stop = "" if r.get("end") else _ui.btn({"action": "money_recurring_stop", "id": r["id"], "label": "止める",
                                                "confirm": f'「{r["item"]}」を今月の分までにします（来月から記録を作りません）。よいですか？'}, cls="ghost")
        rec_rows.append([E(r["item"]), amt, "毎月" if r["cycle"] == "monthly" else "毎年", E(r["start"]), E(r["category"]),
                         f'{r.get("business_pct", 100)}%', state + stop])
    seeds = "".join(f'<button class="ghost" data-seed="{E(json.dumps({**sd, "start": today.isoformat()}, ensure_ascii=False))}">＋ {E(sd["item"])}'
                    + (f'（{"月" if sd["cycle"] == "monthly" else "年"} {"$" if sd["currency"] == "USD" else "¥"}{E(sd["amount"])}）' if sd["amount"] else "（金額は自分で）")
                    + "</button>" for sd in SEEDS if not any(r["item"] == sd["item"] and not r.get("end") for r in s["recurring"]))
    body.append('<section class="card"><h2>固定費（毎月・毎年決まって払うもの）</h2>'
                '<p class="sub">1 回登録すると、月ごとの記録を自動で作ります（毎月 1 日と、このページで足したとき）。始めた日を入れると、そこからの分も作ります。</p>'
                + (_ui.table(["内容", "金額", "周期", "始めた日", "区分", "事業の割合", ""], rec_rows, num=(1, 5), raw=(0, 3, 4, 6)) if rec_rows
                   else '<p class="empty">固定費はまだありません。下の見本を押すと、入力欄に入ります。</p>')
                + (f'<p class="row"><span class="sub">見本（押すと下の欄に入ります。金額は説明書の値です。いまの契約と合っているか確かめてください）：</span>{seeds}</p>' if seeds else "")
                + '<div class="form">'
                + _input("内容", "r-item", attrs='maxlength="80" placeholder="例：Suno Pro"', cls="wide")
                + _input("金額", "r-amount", "number", attrs='min="0" step="0.01" inputmode="decimal"')
                + _select("通貨", "r-currency", [("USD", "ドル", ""), ("JPY", "円", "")])
                + _select("周期", "r-cycle", [("monthly", "毎月", ""), ("yearly", "毎年", "")])
                + _input("始めた日（最初に払った日）", "r-start", "date", today.isoformat())
                + _select("区分", "r-category", [(c, c, "") for c in CATEGORIES["expense"]])
                + _input("事業の割合 %", "r-pct", "number", "100", 'min="0" max="100" step="1"')
                + "</div><p>" + _ui.btn({"action": "money_recurring_add", "label": "固定費を足す"},
                                        fields={"item": "r-item", "amount": "r-amount", "currency": "r-currency", "cycle": "r-cycle",
                                                "start": "r-start", "category": "r-category", "pct": "r-pct"})
                + "</p></section>")

    # ドル円のレート・取り込み・書き出し
    cur_rate = s.get("usd_jpy")
    by_month = "・".join(f"{m}：{v:g} 円" for m, v in sorted(s["usd_jpy_by_month"].items())[-6:])
    body.append('<div class="grid2"><section class="card"><h2>ドル円のレート</h2>'
                f'<p>いまの設定：<b>{f"1 ドル {cur_rate:g} 円" if cur_rate else "未設定"}</b>'
                + (f'<br><span class="sub">月ごと：{E(by_month)}</span>' if by_month else "") + "</p>"
                '<p class="sub">自分で決めた値を入れます（外の為替サービスは使いません）。月を入れると、その月のドルの記録だけをそのレートに直します。</p>'
                '<div class="form">' + _input("1 ドル何円か", "x-rate", "number", f"{cur_rate:g}" if cur_rate else "", 'min="50" max="500" step="0.01" inputmode="decimal"')
                + _input("月（任意）", "x-month", "month") + "</div><p>"
                + _ui.btn({"action": "money_set_rate", "label": "レートを決める"}, fields={"rate": "x-rate", "month": "x-month"}) + "</p></section>"
                '<section class="card"><h2>取り込み・書き出し</h2>'
                '<p class="sub">DistroKid の <b>Bank → See excruciating detail → Download</b> で保存した明細を、ダウンロードフォルダから自動で見つけて取り込みます。'
                '何度取り込んでも二重になりません。</p><p class="row">'
                + _ui.btn({"action": "money_import", "label": "DistroKid の明細を取り込む"}, primary=True)
                + _ui.btn({"action": "money_export", "year": year, "label": f"表に書き出す（{year} 年）"})
                + f'</p><p class="sub">最後に取り込んだ日：{E(s.get("last_import") or "まだ")}　／　書き出し先：out/money/収支_{year}.csv と、SSD の 01_管理表/収支.xlsx</p>'
                "</section></div>")

    # 最近の記録
    recent = sorted(entries, key=lambda e: (e.get("date", ""), e.get("id", "")), reverse=True)[:20]
    rrows = []
    for e in recent:
        orig = f' <span class="sub">（${e["amount_orig"]:,.2f}{"・レート待ち" if pending(e) else ""}）</span>' if e.get("currency") == "USD" else ""
        pct = f' <span class="sub">× {e.get("business_pct", 100)}%</span>' if e["kind"] == "expense" and e.get("business_pct", 100) != 100 else ""
        act = (_ui.badge("info", "明細から") if e.get("source") == "distrokid"
               else _ui.btn({"action": "money_delete", "id": e["id"], "label": "取り消す",
                             "confirm": f'{e["date"]}「{e["item"]}」の記録を取り消します。よいですか？'}, cls="ghost"))
        rrows.append([E(e["date"]), _ui.badge("done" if e["kind"] == "income" else "wait", KINDS[e["kind"]]), E(e.get("category", "")),
                      E(e["item"]) + (f'　<b>{E(names.get(e["artist_slug"], e["artist_slug"]))}</b>' if e.get("artist_slug") else "")
                      + (f'<br><span class="sub">{E(e["memo"])}</span>' if e.get("memo") else ""),
                      _ui.yen(e.get("amount_jpy")) + orig + pct, act])
    body.append('<section class="card"><h2>最近の記録（新しい順に 20 件）</h2>'
                + (_ui.table(["日付", "種類", "区分", "内容", "金額", ""], rrows, num=(4,), raw=(0, 1, 2, 3, 4, 5)) if rrows
                   else '<p class="empty">まだ記録がありません。</p>')
                + (f'<p class="sub">全部で {len(entries)} 件。全部を見るときは「表に書き出す」。</p>' if len(entries) > 20 else "") + "</section>")
    return _ui.page("お金の記録", "".join(body), token, active="/money", head=PAGE_CSS, script=PAGE_JS)


# ---------------------------------------------------------------------------
# ダッシュボードのボタン → 動かすコマンド（値はここで確かめる。webapp.py の command_for から呼ぶ）
def web_command(action: str, p: dict) -> tuple[list[str], str] | None:
    """(money.py に渡す引数, 仕事の名前)。値がおかしければ None（ダッシュボードは「その操作はできません」と出す）"""
    try:
        if action == "money_add":
            kind = _kind(p.get("kind"))
            args = ["add", f"--date={_date(p.get('date'))}", f"--kind={kind}", f"--category={_category(kind, p.get('category'))}",
                    f"--item={_text(p.get('item'), 80)}", f"--amount={_amount(p.get('amount'))}", f"--currency={_currency(p.get('currency'))}",
                    f"--pct={_pct(p.get('pct'))}"]
            if not _text(p.get("item"), 80):
                return None
            artist = _text(p.get("artist"), 40)
            if artist:
                if not re.fullmatch(r"[a-z0-9_\-]+", artist):
                    return None
                args.append(f"--artist={artist}")
            if _text(p.get("memo"), 200):
                args.append(f"--memo={_text(p.get('memo'), 200)}")
            return args, "お金の記録を足す"
        if action == "money_delete":
            eid = str(p.get("id") or "")
            return (["delete", eid], "お金の記録を取り消す") if re.fullmatch(r"[0-9a-z]{6,12}", eid) else None
        if action == "money_import":
            return ["import"], "DistroKid の明細を取り込む"
        if action == "money_export":
            y = int(p.get("year") or date.today().year)
            return (["export", f"--year={y}"], "収支を表に書き出す") if 2000 <= y <= 2100 else None
        if action == "money_apply":
            return ["apply"], "固定費の記録を作る"
        if action == "money_recurring_add":
            cycle = str(p.get("cycle") or "monthly")
            if cycle not in ("monthly", "yearly") or not _text(p.get("item"), 80):
                return None
            return (["recurring", "add", f"--item={_text(p.get('item'), 80)}", f"--amount={_amount(p.get('amount'))}",
                     f"--currency={_currency(p.get('currency'))}", f"--cycle={cycle}", f"--start={_date(p.get('start'))}",
                     f"--category={_category('expense', p.get('category'))}", f"--pct={_pct(p.get('pct'))}"], "固定費を足す")
        if action == "money_recurring_stop":
            rid = str(p.get("id") or "")
            return (["recurring", "stop", rid], "固定費を止める") if re.fullmatch(r"r[0-9a-f]{6}", rid) else None
        if action == "money_set_rate":
            r = _amount(p.get("rate"))
            if not 50 <= r <= 500:
                return None
            args = ["set", "usd_jpy", f"{r:g}"]
            if str(p.get("month") or "").strip():
                args.append(f"--month={_month(p.get('month'))}")
            return args, "ドル円のレートを決める"
    except (BadInput, TypeError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------
def _print_summary(year: int) -> None:
    sm = summary(year)
    print(f"   {year} 年　　収入　　　　費用　　　　差し引き")
    for r in sm["months"]:
        if r["income"] or r["expense"]:
            print(f"   {r['month']}  ¥{r['income']:>10,}  ¥{r['expense']:>10,}  ¥{r['profit']:>10,}")
    t = sm["total"]
    print(f"   合計     ¥{t['income']:>10,}  ¥{t['expense']:>10,}  ¥{t['profit']:>10,}")
    if sm["pending"]:
        print(f"   ※ レート待ちのドルの記録が {sm['pending']} 件（0 円で数えています。money.py set usd_jpy 150 で円になります）")


def _do_import(path: Path | None) -> str:
    step("DistroKid の明細を探して取り込んでいます" if not path else f"明細を取り込んでいます：{path.name}")
    res = import_all(path)
    if not res:
        return ("明細が見つかりませんでした。DistroKid の Bank → See excruciating detail → Download で保存してから、もう一度押してください"
                "（ダウンロードフォルダに置いたままで大丈夫です）")
    for r in res:
        print(f"   ○ {r['file']}：{r['rows']} 行（新しい記録 {r['added']} 件・金額が変わった記録 {r['updated']} 件・合計 ${r['usd']:,.2f}）")
        if r["unknown"]:
            print(f"     － 組が分からなかった名前：{'、'.join(r['unknown'][:8])}（収入には数えています）")
    wait = sum(1 for e in load() if pending(e))
    return (f"明細 {len(res)} 個を取り込みました（新しい記録 {sum(r['added'] for r in res)} 件・更新 {sum(r['updated'] for r in res)} 件）"
            + (f"。ドル円のレートが未設定なので {wait} 件が 0 円のままです" if wait else ""))


def _do_export(year: int) -> str:
    step(f"{year} 年の収支を表に書き出しています")
    p = export_csv(year)
    print(f"   ○ {p}")
    x = export_xlsx()
    if x:
        print(f"   ○ {x}")
    return f"書き出しました：{p.name}" + ("・SSD の 01_管理表/収支.xlsx" if x else "（SSD の収支.xlsx は書けませんでした。SSD をつないで、もう一度）")


def main() -> None:
    ap = argparse.ArgumentParser(description="お金の記録（収入と費用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="1 件足す")
    a.add_argument("--date", default=date.today().isoformat())
    a.add_argument("--kind", required=True, help="income（収入）か expense（費用）")
    a.add_argument("--category", default="その他")
    a.add_argument("--item", required=True)
    a.add_argument("--amount", required=True)
    a.add_argument("--currency", default="JPY")
    a.add_argument("--pct", default="100", help="事業の割合 %%（費用だけ）")
    a.add_argument("--artist", default="")
    a.add_argument("--memo", default="")
    d = sub.add_parser("delete", help="1 件取り消す")
    d.add_argument("id")
    i = sub.add_parser("import", help="DistroKid の明細を取り込む")
    i.add_argument("path", nargs="?", type=Path)
    r = sub.add_parser("recurring", help="固定費")
    rs = r.add_subparsers(dest="rcmd", required=True)
    ra = rs.add_parser("add")
    ra.add_argument("--item", required=True)
    ra.add_argument("--amount", required=True)
    ra.add_argument("--currency", default="JPY")
    ra.add_argument("--cycle", default="monthly")
    ra.add_argument("--start", default=date.today().isoformat())
    ra.add_argument("--end", default=None)
    ra.add_argument("--category", default="サブスク")
    ra.add_argument("--pct", default="100")
    rp = rs.add_parser("stop")
    rp.add_argument("id")
    rp.add_argument("--end", default=None)
    rs.add_parser("list")
    sub.add_parser("apply", help="固定費の、まだ無い月の記録を作る")
    st = sub.add_parser("set", help="設定（usd_jpy）")
    st.add_argument("name", choices=["usd_jpy"])
    st.add_argument("value")
    st.add_argument("--month", default=None)
    e = sub.add_parser("export", help="CSV と SSD の収支.xlsx に書き出す")
    e.add_argument("--year", type=int, default=date.today().year)
    sm = sub.add_parser("summary", help="月ごとの集計を表示")
    sm.add_argument("--year", type=int, default=date.today().year)
    sub.add_parser("monthly", help="毎月の定期実行：固定費 → 明細の取り込み → 書き出し")
    args = ap.parse_args()
    try:
        if args.cmd == "add":
            en = add(d=args.date, kind=args.kind, category=args.category, item=args.item, amount=args.amount, currency=args.currency,
                     pct=args.pct, artist_slug=args.artist, memo=args.memo)
            wait = "（ドル円のレートが未設定なので、円の金額はレートを決めたときに入ります）" if pending(en) else ""
            print(f"【結果】記録しました：{en['date']} {KINDS[en['kind']]}「{en['item']}」¥{en['amount_jpy']:,}{wait}")
        elif args.cmd == "delete":
            hit = delete(args.id)
            if not hit:
                print("【結果】その番号の記録は見つかりませんでした")
                sys.exit(1)
            print(f"【結果】取り消しました：{hit['date']}「{hit['item']}」")
        elif args.cmd == "import":
            if args.path and not args.path.exists():
                print(f"【結果】ファイルが見つかりません：{args.path}")
                sys.exit(1)
            print("【結果】" + _do_import(args.path))
        elif args.cmd == "recurring":
            if args.rcmd == "add":
                rr = recurring_add(args.item, args.amount, args.currency, args.cycle, args.start, args.category, args.pct, args.end)
                n = apply_recurring()
                print(f"【結果】固定費「{rr['item']}」を足しました（{rr['start']} から。記録を {n} 件作りました）")
            elif args.rcmd == "stop":
                rr = recurring_stop(args.id, args.end)
                if not rr:
                    print("【結果】その番号の固定費は見つかりませんでした")
                    sys.exit(1)
                print(f"【結果】固定費「{rr['item']}」を {rr['end']} の分までにしました")
            else:
                for rr in settings()["recurring"]:
                    print(f"   {rr['id']}  {rr['item']}  {rr['amount']} {rr['currency']}  {'毎月' if rr['cycle'] == 'monthly' else '毎年'}  "
                          f"{rr['start']} 〜 {rr.get('end') or '続いている'}  事業 {rr.get('business_pct', 100)}%")
        elif args.cmd == "apply":
            step("固定費の、まだ無い月の記録を作っています")
            print(f"【結果】固定費の記録を {apply_recurring()} 件作りました")
        elif args.cmd == "set":
            n = set_rate(args.value, args.month)
            more = apply_recurring()
            print(f"【結果】1 ドル {float(args.value):g} 円にしました" + (f"（{args.month} の分）" if args.month else "")
                  + f"。円に直した記録 {n} 件" + (f"・固定費の記録 {more} 件を作りました" if more else ""))
        elif args.cmd == "export":
            print("【結果】" + _do_export(args.year))
        elif args.cmd == "summary":
            _print_summary(args.year)
        elif args.cmd == "monthly":
            step("固定費の、まだ無い月の記録を作っています")
            n = apply_recurring()
            msg = _do_import(None)
            today = date.today()
            years = {today.year} | ({today.year - 1} if today.month == 1 else set())   # 1 月は、前の年の分も書き出し直す
            for y in sorted(years):
                _do_export(y)
            print(f"【結果】固定費 {n} 件。{msg}")
    except BadInput as ex:
        print(f"【結果】入れた値を確かめてください：{ex}")
        sys.exit(1)


if __name__ == "__main__":
    main()
