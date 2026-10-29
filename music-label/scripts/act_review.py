#!/usr/bin/env python3
"""
新しい組の承認：管理表に『提案』で入った組を、項目（管理表のタブ）ごとに要約して、1 つずつ承認・却下する（オーナーの指示 2026-10-28。決定 119）

  ダッシュボードの「アーティスト追加」タブが使う。
  ・項目：人物（メンバー）・音楽・見た目・アートの好み・人生の出来事・デビューの経緯・見える傷やタトゥー・影響・歌のアイデア・歌詞の試作・
    最近の出来事・参考曲の候補 … その組の行があるタブごとに、主な中身を短く要約して見せる
  ・承認 ＝ そのタブのその組の『提案』の行を『採用』に（artist_book.py adopt --tabs と同じ）。却下 ＝『却下』に（理由を残す）
  ・全部の項目を決めたら「この組をデビューさせる」：基本情報（アーティストのタブ）を『採用』→ 設定書に取り込み（artist_book.py pull）→
    デビュー日を予約（expand_label.py book。毎月 1 日のある週・4 週間以上あと）
  ・ダッシュボードのタブには、承認待ちの項目の数としるしを出す。デビュー日が決まった組・デビュー間近の組も、このタブに知らせとして出す
  ・中身はダッシュボードで見て直せる（承認待ちの組も、すでに採用した組も。決定 119）。直すと管理表に書き込み、設定書にも取り込み直す

使い方：
  python3 scripts/act_review.py                      # 承認待ちの組と項目の一覧
  python3 scripts/act_review.py approve <ID> <タブ>   # 項目を承認（タブは members・music など）
  python3 scripts/act_review.py reject <ID> <タブ> --reason "…"
  python3 scripts/act_review.py debut <ID>           # 全部の項目を決めたあと、組を採用してデビューを予約
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, as_date  # noqa: E402

LOG = OUT / "artist_book" / "review_log.jsonl"
SKIP_TABS = {"acts", "changes", "collabs"}   # 基本情報は最後に、変更の提案・コラボの相性は別のところで扱う
# 要約に使う項目（タブごとに、上から目立つものを）
SUMMARY = {
    "acts": ["reading", "formation", "label", "gender", "birth", "main_instrument", "origin", "base_now", "scene", "strengths",
             "debut_summary", "values", "future"],
    "members": ["name", "role", "age", "gender", "birthplace", "personality", "fashion", "instruments"],
    "music": ["genres", "dna", "v_sex", "v_range", "primary_tech", "bpm_min", "bpm_max", "themes", "lang", "tone", "lyric_mode"],
    "visual": ["space", "light", "palette", "taste", "conceal", "cover_rule", "logo_concept"],
    "art": ["mediums", "imagery", "taste", "graphic", "manga_real", "films_real", "from_works"],
}
_CACHE: dict = {}


def book_data() -> tuple[object, dict]:
    """管理表を読む（Excel は変わっていなければ前の読み込みを使う。Google の表は 60 秒は前の読み込みを使う：ダッシュボードが 30 秒ごとに読むため）"""
    import time
    from _artist_book import open_book
    book = open_book()
    p = getattr(book, "path", None)
    key = (str(p), p.stat().st_mtime) if p is not None and Path(p).exists() else ("remote", int(time.time() // 60))
    if _CACHE.get("key") == key:
        return book, _CACHE["data"]
    data = book.read()
    _CACHE.update(key=key, data=data)
    return book, data


def _val(v) -> str:
    if isinstance(v, list):
        return "・".join(str(x) for x in v if x)
    return str(v or "").strip()


def summarize(tab: str, rows: list[dict], limit: int = 220) -> str:
    """その組のそのタブの行を、短い日本語の要約に"""
    from _artist_book import key_to_header
    heads = key_to_header(tab)
    fields = SUMMARY.get(tab)
    parts = []
    for r in rows[:4]:
        keys = fields or [k for k in heads if k not in ("act_id", "status", "id", "updated", "_pos") and r.get(k)][:3]
        bits = [f"{heads.get(k, k).split('（')[0]}：{_val(r.get(k))}" for k in keys if _val(r.get(k))]
        parts.append("／".join(bits))
    text = "　｜　".join(p for p in parts if p)
    if len(rows) > 4:
        text += f"　ほか {len(rows) - 4} 件"
    return text[:limit] + ("…" if len(text) > limit else "")


def state_of(rows: list[dict]) -> str:
    st = {str(r.get("status") or "").strip() for r in rows}
    if "提案" in st:
        return "pending"
    if st <= {"却下"}:
        return "rejected"
    return "approved" if st <= {"採用", "", "反映済み"} else "mixed"


def review(data: dict | None = None) -> dict:
    """{acts: [{id, name, label, formation, summary, items:[{tab, name, state, count, summary}], ready}], pending, upcoming}"""
    from _artist_book import TABS, TAB_ORDER
    if data is None:
        _, data = book_data()
    out = []
    for a in data.get("acts", []):
        if str(a.get("status") or "").strip() != "提案" or not a.get("id"):
            continue
        aid = a["id"]
        items = []
        for tab in TAB_ORDER:
            if tab in SKIP_TABS:
                continue
            rows = [r for r in data.get(tab, []) if r.get("act_id") == aid]
            if not rows:
                continue
            items.append({"tab": tab, "name": TABS[tab][0], "state": state_of(rows), "count": len(rows), "summary": summarize(tab, rows)})
        out.append({"id": aid, "name": a.get("name", aid), "label": a.get("label", ""), "formation": a.get("formation", ""),
                    "summary": summarize("acts", [a], 400), "items": items,
                    "ready": all(i["state"] in ("approved", "rejected") for i in items)})
    pending = sum(1 for a in out for i in a["items"] if i["state"] in ("pending", "mixed")) + sum(1 for a in out if a["ready"])
    return {"acts": out, "pending": pending, "upcoming": upcoming()}


def upcoming(days: int = 45) -> list[dict]:
    """デビュー日が決まっていて、まだデビューしていない組（知らせに出す）"""
    today = date.today()
    out = []
    for a in all_artists():
        d = as_date(a.get("debut_week"))
        if d and d >= today - __import__("datetime").timedelta(days=6) and a.get("cadence") != "paused":
            import _songs
            if _songs.debut_registered(a.get("slug", "")) is True and d < today:
                continue
            out.append({"slug": a["slug"], "name": a.get("name"), "debut_week": d.isoformat(), "days": (d - today).days,
                        "soon": (d - today).days <= days})
    return sorted(out, key=lambda x: x["debut_week"])


def _log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **entry}, ensure_ascii=False) + "\n")


def set_status(aid: str, tab: str, to: str, only_from: tuple[str, ...] = ("提案",)) -> int:
    from _artist_book import ReadOnlyBook
    book, data = book_data()
    idkey = "id" if tab == "acts" else "act_id"
    edits = [(tab, r["_pos"], "status", to) for r in data.get(tab, [])
             if r.get(idkey) == aid and str(r.get("status") or "").strip() in only_from]
    if not edits:
        return 0
    try:
        book.update_many(edits)
    except ReadOnlyBook:
        sys.exit("[案内] Google の表を閲覧のみで読んでいるので書き込めません。表の『状態』を直接変えてください（docs/13）")
    _CACHE.clear()
    return len(edits)


def approve(aid: str, tab: str) -> int:
    n = set_status(aid, tab, "採用")
    _log({"act": aid, "tab": tab, "do": "承認", "rows": n})
    print(f"【結果】{aid} の「{_tab_ja(tab)}」を承認しました（{n} 行を『採用』に）")
    return 0


def reject(aid: str, tab: str, reason: str) -> int:
    n = set_status(aid, tab, "却下", ("提案", "採用", ""))
    _log({"act": aid, "tab": tab, "do": "却下", "reason": reason, "rows": n})
    print(f"【結果】{aid} の「{_tab_ja(tab)}」を却下しました（{n} 行）。直すときは管理表で中身を直し、状態を『提案』に戻すと、また承認できます")
    return 0


def debut(aid: str) -> int:
    r = next((a for a in review()["acts"] if a["id"] == aid), None)
    if r is None:
        print(f"【結果】{aid} は承認待ちの組ではありません")
        return 1
    if not r["ready"]:
        left = [i["name"] for i in r["items"] if i["state"] in ("pending", "mixed")]
        print(f"【結果】まだ決めていない項目があります：{'・'.join(left)}")
        return 2
    set_status(aid, "acts", "採用")
    _log({"act": aid, "tab": "acts", "do": "デビュー承認"})
    print(f"▶ {r['name']} を採用しました。設定書に取り込み、デビュー日を予約します", flush=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "artist_book.py"), "pull"], check=False)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "expand_label.py"), "book"], check=False)
    a = next((x for x in all_artists() if x.get("slug") == aid), {})
    print(f"【結果】{r['name']} をデビューさせることにしました" + (f"（デビューの週：{a['debut_week']}）" if a.get("debut_week") else ""))
    return 0


# ---------------------------------------------------------------------------
# 管理表の中身をダッシュボードで見て直す（決定 119）
def acts_index(data: dict | None = None) -> list[dict]:
    if data is None:
        _, data = book_data()
    return [{"id": a.get("id"), "name": a.get("name") or a.get("id"), "status": str(a.get("status") or "").strip() or "採用"}
            for a in data.get("acts", []) if a.get("id")]


def act_sheet(aid: str) -> dict:
    """その組の、管理表の全部のタブの行（項目の名前・種類・値・説明つき）"""
    import _artist_book as ab
    _, data = book_data()
    helps = getattr(ab, "HELP", {}) or {}
    tabs = []
    for tab in ab.TAB_ORDER:
        if tab == "changes":
            continue
        idkey = "id" if tab == "acts" else "act_id"
        rows = [r for r in data.get(tab, []) if r.get(idkey) == aid]
        if not rows:
            continue
        out_rows = []
        for r in rows:
            fields = [{"key": k, "label": h, "kind": kind, "value": r.get(k, [] if kind == "list" else ""),
                       "help": helps.get((tab, k), "")} for k, h, kind in ab.TABS[tab][1] if k not in ("act_id", "updated")]
            out_rows.append({"pos": r["_pos"], "status": str(r.get("status") or "").strip(), "fields": fields})
        tabs.append({"tab": tab, "name": ab.TABS[tab][0], "rows": out_rows})
    name = next((a.get("name") for a in data.get("acts", []) if a.get("id") == aid), aid)
    return {"id": aid, "name": name, "tabs": tabs}


def save_fields(tab: str, pos: int, values: dict) -> tuple[int, str]:
    """1 行（縦型のタブは 1 列）の項目を書き換える。(書いた数, 説明)。リストは 1 行に 1 つ"""
    import _artist_book as ab
    if tab not in ab.TABS or tab == "changes":
        return 0, "そのタブは直せません"
    kinds = {k: kind for k, _, kind in ab.TABS[tab][1]}
    book, data = book_data()
    if int(pos) not in {r.get("_pos") for r in data.get(tab, [])}:
        return 0, "その行が見つかりません（管理表が変わったかもしれません。開き直してください）"
    edits = []
    for k, v in (values or {}).items():
        if k not in kinds or k in ("act_id", "id"):
            continue   # ID は直さない（ほかのタブの行とのつながりが切れるため）
        kind = kinds[k]
        if kind == "list":
            v = [x.strip() for x in (v if isinstance(v, list) else str(v).splitlines()) if str(x).strip()]
        elif kind == "int":
            try:
                v = int(float(str(v).strip())) if str(v).strip() else None
            except ValueError:
                return 0, f"「{k}」は数字で入れてください"
        elif kind == "bool":
            v = bool(v)
        else:
            v = str(v).strip()
        edits.append((tab, int(pos), k, v))
    if "updated" in kinds and edits:
        edits.append((tab, int(pos), "updated", date.today().isoformat()))
    if not edits:
        return 0, "変わったところがありません"
    try:
        book.update_many(edits)
    except ab.ReadOnlyBook:
        return 0, "Google の表を閲覧のみで読んでいるので書き込めません（表で直接直してください。docs/13）"
    _CACHE.clear()
    _log({"tab": tab, "pos": pos, "do": "ダッシュボードで直した", "keys": [e[2] for e in edits]})
    return len(edits), "管理表に書き込みました"


def _tab_ja(tab: str) -> str:
    from _artist_book import TABS
    return TABS.get(tab, (tab,))[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="新しい組の承認（項目ごと）")
    ap.add_argument("cmd", nargs="?", default="list", choices=["list", "approve", "reject", "debut"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("tab", nargs="?")
    ap.add_argument("--reason", default="")
    a = ap.parse_args()
    if a.cmd == "list":
        r = review()
        for x in r["acts"]:
            print(f"■ {x['name']}（{x['id']}・{x['label']}）{'— 全部決めました：デビューの承認待ち' if x['ready'] else ''}\n   {x['summary']}")
            for i in x["items"]:
                print(f"   [{ {'pending': '承認待ち', 'approved': '承認済み', 'rejected': '却下', 'mixed': '一部承認待ち'}[i['state']] }] {i['name']}：{i['summary']}")
        for u in r["upcoming"]:
            print(f"★ デビュー予定：{u['name']} {u['debut_week']} の週（あと {u['days']} 日）")
        if not r["acts"] and not r["upcoming"]:
            print("【結果】承認待ちの組はありません")
        return
    if not a.id or (a.cmd in ("approve", "reject") and not a.tab):
        sys.exit("[案内] 組の ID（と項目のタブ）を指定してください")
    sys.exit({"approve": lambda: approve(a.id, a.tab), "reject": lambda: reject(a.id, a.tab, a.reason),
              "debut": lambda: debut(a.id)}[a.cmd]())


if __name__ == "__main__":
    main()
