#!/usr/bin/env python3
"""
管理画面（out/dashboard.html）を作る。ブラウザで開くと、レーベル全体の今の状態が 1 枚で分かる。

  ・人がやること（今すぐ）：ロゴや写真の選択待ち、Suno の節・テイク待ち、最終確認の ✕、DistroKid 登録の期限、ISRC の記入
  ・今週と来週の制作の進み具合（組ごと）
  ・これから配信される曲と、最近配信した曲
  ・伸びている組・下がっている組（成長分析）
  ・今月の Suno のダウンロード数、鍵の設定状況、定期実行の最後の動き

使い方
  python3 scripts/dashboard.py            # 作る
  python3 scripts/dashboard.py --open     # 作ってブラウザで開く
  （定期実行では 15 分ごとに自動で作り直す）
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, as_date, load_dotenv, monday_of, read_json  # noqa: E402

load_dotenv()
JST = ZoneInfo("Asia/Tokyo")
CSS = """
:root{--bg:#f6f4ef;--card:#fff;--ink:#22201c;--sub:#6f6a60;--line:#e2ddd2;--accent:#a07a3c;--ng:#b3412e;--ok:#4f7a4a}
@media (prefers-color-scheme: dark){:root{--bg:#181715;--card:#22211e;--ink:#ece8df;--sub:#a49e92;--line:#34322d;--accent:#d2ad6b;--ng:#e07a66;--ok:#8fbf86}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 64px}h1{font-weight:500;letter-spacing:.04em;margin:0}
.sub{color:var(--sub);margin:2px 0 24px;font-size:13px}h2{font-weight:500;font-size:18px;border-bottom:1px solid var(--line);padding-bottom:6px;margin:32px 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(220px,100%),1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.big{font-size:26px;font-variant-numeric:tabular-nums}.label{color:var(--sub);font-size:13px}
ul.todo{list-style:none;padding:0;margin:0}ul.todo li{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:10px 14px;margin:8px 0}
ul.todo li.ng{border-left-color:var(--ng)}code{font-size:12px;color:var(--sub);word-break:break-all}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--sub);font-weight:500}tr:last-child td{border-bottom:none}.ok{color:var(--ok)}.ngc{color:var(--ng)}.empty{color:var(--sub)}
"""


def esc(x) -> str:
    return html.escape(str(x))


def todos() -> list[tuple[str, str, list[str], str]]:
    """(重要度 ng/normal, やること, 対象の一覧, 場所やコマンド)。同じ種類はまとめる"""
    now = datetime.now(timezone.utc)
    names = {a["slug"]: a["name"] for a in all_artists()}
    groups: dict[tuple[str, str, str], list[str]] = {}

    def add(sev: str, what: str, who: str, where: str) -> None:
        groups.setdefault((sev, what, where), []).append(who)

    crit = read_json(ROOT / "templates" / "visual_criteria.json")
    for cand in sorted((OUT / "visuals").glob("**/candidates.json")):
        folder = cand.parent
        kinds = {c["kind"] for c in read_json(cand) if c.get("file")}
        chosen = {read_json(p).get("kind") for p in folder.glob("selection_*.json")}
        need = kinds - chosen
        if folder.relative_to(OUT / "visuals").parts[0] not in names:   # 保管庫へ移した組の古い候補
            continue
        if need and (not crit.get("locked") or "reshoot" in folder.parts):
            rel = folder.relative_to(OUT / "visuals").as_posix()
            add("normal", "ロゴ・写真・ジャケットを選ぶ", f"{names.get(folder.relative_to(OUT / 'visuals').parts[0], rel)}（{'・'.join(sorted(need))}）",
                "out/visuals/index.html")
    week = monday_of(date.today())
    for bp in sorted((OUT / "briefs").glob(f"{week}_*.json")):
        if "." in bp.stem:
            continue
        b = read_json(bp)
        if b.get("artist_slug") not in names:
            continue
        who = names.get(b["artist_slug"], b["artist_slug"])
        if b.get("lyrics_mode") in ("core_fixed", "topic_only") and not bp.with_suffix(".lyrics_final.txt").exists():
            add("normal", "Suno の Write Lyrics で節を書かせて保存", who, f"out/briefs/{week}_<組>.verses.txt")
        sel = OUT / "takes" / bp.stem / "selection.json"
        if b.get("suno_style_prompt") and not sel.exists():
            add("normal", "Suno で作り、聴いて選んだ 1 本を置く", who, f"out/takes/{week}_<組>/take_01.mp3")
        elif sel.exists():
            st = read_json(sel)
            r = next((t for t in st.get("takes", []) if t["take"] == st.get("decision", {}).get("selected")), None)
            if r and not r["passed"]:
                add("ng", "最終確認で ✕（選び直すか、このまま進めるか）", f"{who}（{'、'.join(r['hard_fail'])}）", "run_week.py status")
            if st.get("decision", {}).get("action") == "human_from_shortlist" and not st["decision"].get("selected"):
                add("normal", "機械が絞った 2 本から選ぶ", who, "select_takes.py <ブリーフ> --choose 番号")
    for mp in sorted((OUT / "distrokid").glob("*/*.metadata.json")):
        if mp.parent.name.startswith("2000-"):
            continue
        m = read_json(mp)
        if m.get("artist_slug") not in names:
            continue
        who = names.get(m["artist_slug"], m["artist_slug"])
        days = (datetime.fromisoformat(m["release_at_utc"].replace("Z", "+00:00")) - now).days
        untitled = str(m.get("title", "")).startswith("（")
        if untitled and 0 <= days <= 16:
            add("ng", f"タイトルを決める（制作週 {mp.parent.name}）", f"{who}（あと {days} 日）",
                f"distrokid_sheet.py --week {mp.parent.name} --title <組>=\"…\"")
        elif m.get("status") in ("planned", "mastered") and 0 <= days <= 16:
            add("ng" if days <= 9 else "normal", f"DistroKid に登録（制作週 {mp.parent.name}）", f"{who}「{m.get('title')}」（あと {days} 日）",
                f"out/distrokid/{mp.parent.name}/sheet.md")
        if m.get("status") in ("uploaded", "live") and not m.get("isrc"):
            add("normal", "登録後の ISRC を書く", f"{who}「{m.get('title')}」", "out/distrokid/<週>/<組>.metadata.json")
    for a in all_artists():
        d = as_date(a.get("debut_week"))
        if d and d > date.today() and (a.get("expansion") or a.get("source")):
            add("normal", "新人のデビュー予約（取り消すなら今）", f"{a['name']}（{d} の週）", "expand_label.py cancel --artist <組>")
    for sev, what, who, where in book_todos(names):
        add(sev, what, who, where)
    return [(sev, what, who, where) for (sev, what, where), who in groups.items()]


def book_todos(names: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """アーティスト台帳の返事待ち（提案）と、採用したのに入手していない参考曲"""
    try:
        from _artist_book import TAB_ORDER, TABS, open_book
        b = open_book()
        if hasattr(b, "path") and not b.path.exists():
            return [("normal", "アーティスト台帳を作る", "（まだ無い）", "artist_book.py init → import-json proposals/<日付>")]
        data = b.read()
    except Exception as e:  # noqa: BLE001
        return [("normal", "アーティスト台帳を読めない（共有設定か鍵を確認）", str(e)[:60], "docs/13_artist_book.md")]
    out = []
    acts = {r["id"]: r for r in data["acts"]}
    st = lambda r: str(r.get("status") or "").strip()  # noqa: E731
    for r in data["acts"]:
        if st(r) == "提案":
            out.append(("normal", "新しい組の提案を読んで、採用・保留・却下を決める", r.get("name", r["id"]),
                        f"proposals/{r['id']}.md（artist_book.py adopt {r['id']}）"))
        elif st(r) in ("", "採用") and r["id"] not in names:
            out.append(("normal", "採用した組を設定書に反映（火曜の朝に自動でも動く）", r.get("name", r["id"]), "artist_book.py pull"))
    for tab in TAB_ORDER:
        if tab in ("acts", "changes"):
            continue
        for r in data[tab]:
            act = acts.get(r.get("act_id"), {})
            if st(r) == "提案" and st(act) != "提案":   # 組ごと提案中のものは上でまとめて出す
                out.append(("normal", f"『{TABS[tab][0]}』の提案に返事（採用・却下）", act.get("name", r.get("act_id")),
                            "artists.xlsx の状態の列"))
    for r in data["changes"]:
        if st(r) == "提案":
            out.append(("normal", "変更の提案（方針転換など）に返事", f"{acts.get(r.get('act_id'), {}).get('name', r.get('act_id'))}"
                        f"（{r.get('tab')}『{r.get('field')}』）", "採用 → artist_book.py apply-changes → pull"))
    for r in data["choices"]:
        act = acts.get(r.get("act_id"), {})
        if st(r) in ("", "採用") and st(act) in ("", "採用") and not r.get("acquired"):
            out.append(("normal", "参考曲を正規に入手して解析（入手に ○）", f"{r.get('title')}／{r.get('artist')}（{act.get('name', '')}）",
                        "out/references/ → analyze_track.py"))
    return out


def week_table(week: date) -> str:
    try:
        from run_week import status_rows
        rows = status_rows(week)
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return f"<p class='empty'>{week} の週のブリーフはまだありません</p>"
    cols = list(rows[0])
    body = "".join("<tr>" + "".join(f"<td class='{'ok' if str(r[c]).startswith('○') else ''}'>{esc(r[c])}</td>" for c in cols) + "</tr>"
                   for r in rows)
    return f"<div class='tbl'><table><tr>{''.join(f'<th>{esc(c)}</th>' for c in cols)}</tr>{body}</table></div>"


STATUS_JA = {"planned": "未登録", "mastered": "音源完成・未登録", "uploaded": "登録済み", "live": "配信中", "takedown": "取り下げ"}


def releases_table() -> str:
    now = datetime.now(timezone.utc)
    rows = []
    for mp in (OUT / "distrokid").glob("*/*.metadata.json"):
        if mp.parent.name.startswith("2000-"):
            continue
        m = read_json(mp)
        rel = datetime.fromisoformat(m["release_at_utc"].replace("Z", "+00:00"))
        if -14 <= (rel - now).days <= 21:
            rows.append((rel, m))
    if not rows:
        return "<p class='empty'>前後 3 週間の配信はありません</p>"
    rows.sort(key=lambda x: x[0])
    names = {a["slug"]: a["name"] for a in all_artists()}
    body = "".join(f"<tr><td>{r.astimezone(JST):%m/%d %H:%M}</td><td>{esc(names.get(m['artist_slug'], m['artist_slug']))}</td>"
                   f"<td>{esc(m.get('title'))}</td><td>{esc(STATUS_JA.get(m.get('status'), m.get('status')))}</td><td>{esc(m.get('isrc') or '—')}</td></tr>"
                   for r, m in rows)
    return ("<div class='tbl'><table><tr><th>配信（日本時間）</th><th>組</th><th>曲</th><th>状態</th><th>ISRC</th></tr>"
            f"{body}</table></div>")


def growth_cards() -> str:
    p = OUT / "growth" / "hints.json"
    if not p.exists():
        return "<p class='empty'>成長分析はまだです（成績が集まると木曜に自動で作られます）</p>"
    names = {a["slug"]: a["name"] for a in all_artists()}
    mark = {"up": ("伸びている", "ok"), "down": ("下がっている", "ngc"), "flat": ("横ばい", ""), "new": ("データ不足", "")}
    cards = []
    for slug, h in sorted(read_json(p).items(), key=lambda x: ["up", "flat", "new", "down"].index(x[1].get("artist_trend", "new"))):
        t, cls = mark.get(h.get("artist_trend"), ("—", ""))
        ratio = f"（{h['growth_ratio']}）" if h.get("growth_ratio") else ""
        piv = f"<div class='label ngc'>方針転換 レベル {h['pivot']['level']}</div>" if h.get("pivot") else ""
        cards.append(f"<div class='card'><div class='label'>{esc(names.get(slug, slug))}</div><div class='{cls}'>{t}{ratio}</div>{piv}</div>")
    return f"<div class='cards'>{''.join(cards)}</div>"


def key_cards() -> str:
    groups = {"Claude": ["ANTHROPIC_API_KEY"], "OpenAI": ["OPENAI_API_KEY"], "Supabase": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"],
              "R2": ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"],
              "YouTube": ["YOUTUBE_REFRESH_TOKEN"], "Instagram": ["IG_ACCESS_TOKEN"], "TikTok": ["TIKTOK_REFRESH_TOKEN"]}
    cards = []
    for name, keys in groups.items():
        ok = all(os.environ.get(k) for k in keys)
        cards.append(f"<div class='card'><div class='label'>{name}</div><div class='{'ok' if ok else 'ngc'}'>{'設定済み' if ok else '未設定'}</div></div>")
    return f"<div class='cards'>{''.join(cards)}</div><p class='sub'>接続まで確かめるときは <code>python3 scripts/setup_keys.py --check</code></p>"


def schedule_cards() -> str:
    logs = OUT / "logs"
    cards = []
    for job in ("post", "trends", "brief", "auto", "growth", "backup", "monthly"):
        f = logs / f"{job}.log"
        if not f.exists():
            cards.append(f"<div class='card'><div class='label'>{job}</div><div class='empty'>まだ動いていません</div></div>")
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        starts = re.findall(r"===== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) ", text)
        codes = re.findall(r"→ 終了コード (\d+)", text.split("=====")[-1])
        ok = all(c == "0" for c in codes)
        cards.append(f"<div class='card'><div class='label'>{job}</div><div>{esc(starts[-1] if starts else '—')}</div>"
                     f"<div class='{'ok' if ok else 'ngc'}'>{'正常' if ok else 'エラーあり（ログを確認）'}</div></div>")
    return f"<div class='cards'>{''.join(cards)}</div>"


def downloads_cards() -> str:
    try:
        from run_week import downloads_this_month
        dl = downloads_this_month()
    except Exception:  # noqa: BLE001
        dl = {}
    if not dl:
        return "<p class='empty'>今月のダウンロードはまだありません</p>"
    return "<div class='cards'>" + "".join(
        f"<div class='card'><div class='label'>{esc(l)}</div><div class='big {'ngc' if n >= cap * 0.8 else ''}'>{n}<span class='label'> / {cap}</span></div></div>"
        for l, (n, cap) in sorted(dl.items())) + "</div>"


def build() -> Path:
    now = datetime.now(JST)
    week = monday_of(date.today())
    items = todos()
    todo_html = "".join(f"<li class='{sev}'><b>{esc(what)}</b>（{len(who)}）<br>{esc('、'.join(who))}<br><code>{esc(where)}</code></li>"
                        for sev, what, who, where in sorted(items, key=lambda x: x[0] != "ng")) or "<li>今すぐやることはありません</li>"
    page = (f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta http-equiv='refresh' content='900'><title>EtherArchi Label</title><style>{CSS}</style></head><body><main>"
            f"<h1>EtherArchi Label</h1><p class='sub'>{now:%Y-%m-%d %H:%M} 時点（15 分ごとに自動で作り直し）</p>"
            f"<h2>人がやること</h2><ul class='todo'>{todo_html}</ul>"
            f"<h2>今週の制作（{week} の週）</h2>{week_table(week)}"
            f"<h2>来週の制作（{week + timedelta(weeks=1)} の週）</h2>{week_table(week + timedelta(weeks=1))}"
            f"<h2>配信の予定と最近の配信</h2>{releases_table()}"
            f"<h2>組の伸び</h2>{growth_cards()}"
            f"<h2>今月の Suno のダウンロード</h2>{downloads_cards()}"
            f"<h2>定期実行</h2>{schedule_cards()}"
            f"<h2>鍵</h2>{key_cards()}"
            f"</main></body></html>")
    p = OUT / "dashboard.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page, encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description="管理画面（out/dashboard.html）を作る")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    p = build()
    print(f"▶ 管理画面を作りました: {p.relative_to(ROOT)}")
    if a.open:
        webbrowser.open(p.resolve().as_uri())


if __name__ == "__main__":
    main()
