#!/usr/bin/env python3
"""
ダッシュボードのウェブアプリ（Mac の中で動く小さなサーバー。外のサービスにデータを送らない）

ブラウザで http://localhost:8765 を開くと：
  ・アーティストごとの進み具合と詰まり（担当・何日止まっているか・次の一歩）。30 秒ごとに自動で新しくなる
  ・仕組みの詰まり（鍵・ffmpeg・SSD・定期実行）と、管理表の「詰まっているところ」
  ・曲の一覧（公開の段階・ISRC・公開セットのフォルダ）
  ・ボタン：曲を取り込む／配信を確かめる／SSD に保管する／写真とジャケットを作る／DistroKid に登録したことを記録する／
    公開セットのフォルダを Finder で開く／Suno に貼る内容のページ
動きの中身は今までのコマンド（publish.py・archive_sync.py・kit_images.py）と同じ。ボタンはそれを代わりに実行するだけ。

使い方：
  python3 scripts/webapp.py                 # この Mac のブラウザだけから見る（http://localhost:8765）
  python3 scripts/webapp.py --lan           # 家の Wi-Fi のスマホからも見る（合鍵つきの URL を表示。合鍵は .env の WEBAPP_KEY）
  python3 scripts/webapp.py --port 8800     # 番号を変える
  python3 scripts/schedule.py install       # Mac の起動中はいつも動かしておく（仕事の名前 webapp）

安全のため：
  ・ふだんはこの Mac の中からしか開けない（--lan のときだけ家の Wi-Fi から。合鍵が無いと開けない）
  ・ボタンは決まった仕事しか実行できない。ページに埋め込んだ毎回変わる合言葉が無いと受け付けない（ほかのサイトから押させない）
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_dotenv  # noqa: E402

load_dotenv()
import _songs  # noqa: E402
import _status  # noqa: E402

S = Path(__file__).resolve().parent
PY = sys.executable
TOKEN = secrets.token_urlsafe(24)          # ボタン用の合言葉（起動のたびに変わる）
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
STATUS_JA = {"": "Suno で作曲", "imported": "取り込み済み", "waiting_ffmpeg": "音量調整待ち", "waiting_cover": "ジャケット待ち",
             "ready": "登録の準備ができた", "registered": "DistroKid 登録済み", "live": "配信中"}


# ---------------------------------------------------------------------------
# データ
def _iso(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else x


def status_json() -> dict:
    arts = []
    for r in _status.artists_overview():
        steps = [{**s, "since": _iso(s["since"]),
                  "days": (date.today() - s["since"]).days if s["since"] else None} for s in r["steps"]]
        b = r["blocker"]
        arts.append({**{k: r[k] for k in ("slug", "name", "label", "days")}, "steps": steps,
                     "blocker": {**b, "since": _iso(b["since"])} if b else None})
    songs = []
    import _archive as archive
    for s in _songs.all_songs():
        if str(s["week"]).startswith("2000-"):
            continue
        st = _songs.state(s)
        folder = archive.local_folder(s["artist"], "music", _songs.folder_name(s)) if (OUT / "masters" / s["key"] / "master.wav").exists() else None
        songs.append({"id": s["id"], "artist": s["artist"], "slug": s["slug"], "title": s["title"], "kind": s["kind"],
                      "status": st.get("status", ""), "status_ja": STATUS_JA.get(st.get("status", ""), st.get("status", "")),
                      "release_date": st.get("released") or st.get("release_date") or (str(s.get("release_at") or "")[:10]),
                      "isrc": st.get("isrc", ""), "url": st.get("url", ""), "folder": str(folder) if folder else "",
                      "draft": (OUT / "publish" / s["key"] / "DistroKid下書き.md").exists()})
    rows, src = _status.tracker_rows()
    import _timeline
    try:
        from dashboard import todos
        weekly = [{"sev": a, "what": b, "who": c, "where": d} for a, b, c, d in todos()]
    except Exception:  # noqa: BLE001  毎週の制作のやることが読めなくても画面は出す
        weekly = []
    for x in songs:
        x["flow"] = "regular" if _timeline.in_regular(x["slug"]) else "debut"
    tl = _timeline.timeline()
    import _guide
    for key in ("debut", "regular"):
        for r in tl[key]["rows"]:
            for ph in r["phases"]:
                try:
                    ph["guide"] = _guide.explain(r, ph)
                except Exception as e:  # noqa: BLE001  説明が作れなくても画面は出す
                    ph["guide"] = {"cause": ph.get("detail", ""), "steps": [], "actions": [], "error": str(e)}
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "artists": arts, "timeline": tl,
            "weekly_todos": weekly,
            "system": [{"sev": a, "what": b, "how": c} for a, b, c in _status.system_blockers()],
            "tracker": {"rows": rows, "src": src}, "songs": songs,
            "jobs": [{k: j[k] for k in ("id", "label", "state", "started")} for j in list(JOBS.values())[-8:]]}


# ---------------------------------------------------------------------------
# ボタンで動かす仕事（決まったものだけ）
def _slugs() -> set[str]:
    return {a["slug"] for a in all_artists() if a.get("slug")}


def command_for(action: str, p: dict) -> tuple[list[str], str] | None:
    if action == "scan":
        return [PY, str(S / "publish.py"), "scan"], "曲の取り込み"
    if action == "track":
        return [PY, str(S / "publish.py"), "track"], "配信の確認"
    if action == "sync":
        return [PY, str(S / "archive_sync.py")], "保管（ローカルと SSD）"
    if action == "dashboard":
        return [PY, str(S / "dashboard.py")], "ダッシュボードの書き出し"
    if action == "images" and p.get("slug") in _slugs():
        return [PY, str(S / "kit_images.py"), "--only", p["slug"]], f"写真とジャケットを作る（{p['slug']}）"
    if action == "registered":
        song = _songs.by_id(str(p.get("id", "")))
        day = str(p.get("date") or date.today().isoformat())
        if song and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return [PY, str(S / "publish.py"), "registered", song["id"], "--date", day], f"登録の記録（{song['artist']}「{song['title']}」）"
    if action == "open_folder":
        song = _songs.by_id(str(p.get("id", "")))
        if song:
            import _archive as archive
            folder = archive.local_folder(song["artist"], "music", _songs.folder_name(song))
            if not any(folder.iterdir()):   # ローカルから片付けたあと：SSD の公開セット
                sf, _ = archive.artist_folder(song["artist"], "music", _songs.folder_name(song))
                folder = sf or folder
            return ["open", str(folder)], f"フォルダを開く（{song['title']}）"
    slug = p.get("slug") if p.get("slug") in _slugs() else None
    if action == "cover" and slug:
        return [PY, str(S / "kit_images.py"), "--only", slug, "--kind", "cover"], f"ジャケットを作る（{slug}）"
    if action == "check_name" and slug:
        return [PY, str(S / "check_names.py"), "--artist", slug, "--apply"], f"名前の確認（{slug}）"
    if action == "refresh_photo" and slug:
        return [PY, str(S / "refresh_photos.py"), "--only", slug, "--force"], f"写真の刷新（{slug}）"
    if action == "open_artist" and slug:
        import _archive as archive
        name = next((a.get("name") for a in all_artists() if a.get("slug") == slug), slug)
        return ["open", str(archive.local_folder(name))], f"フォルダを開く（{name}）"
    if action == "brief":
        week = str(p.get("week") or "this")
        if week == "this" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", week):
            return [PY, str(S / "run_week.py"), "brief", "--week", week], f"ブリーフを作る（{week}）"
    if action == "terminal" and p.get("key") in TERMINAL:
        cmd = f"cd {shlex.quote(str(ROOT))} && " + " ".join(shlex.quote(x) for x in TERMINAL[p["key"]])
        esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", 'tell application "Terminal" to activate', "-e", f'tell application "Terminal" to do script "{esc}"'], \
            f"ターミナルを開く（{p['key']}）"
    return None


# ターミナルで始める作業（鍵の入力など、Gon さんが打ち込む必要があるもの）
TERMINAL = {"openai": [PY, "scripts/setup_keys.py", "--only", "openai"], "spotify": [PY, "scripts/setup_keys.py", "--only", "spotify"],
            "owner": [PY, "scripts/setup_keys.py", "--only", "owner"], "ffmpeg": ["brew", "install", "ffmpeg"]}


def start_job(cmd: list[str], label: str) -> str:
    jid = secrets.token_hex(4)
    job = {"id": jid, "label": label, "state": "running", "started": datetime.now().strftime("%H:%M:%S"), "out": ""}
    with LOCK:
        JOBS[jid] = job

    def run():
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3600,
                               env={**os.environ, "PATH": os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"})
            job["out"] = (r.stdout + r.stderr)[-20000:]
            job["state"] = "done" if r.returncode == 0 else "error"
        except Exception as e:  # noqa: BLE001
            job["out"], job["state"] = str(e), "error"
    threading.Thread(target=run, daemon=True).start()
    return jid


# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Label Dashboard</title><style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#22201c;--sub:#6f6a60;--muted:#9a948a;--line:#e2ddd2;--grid:#efebe3;--wk:#f1ede5;--accent:#a07a3c;
--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--good-t:rgba(12,163,12,.16);--warn-t:rgba(250,178,25,.22);--crit-t:rgba(208,59,59,.16);--plan-t:rgba(110,106,96,.07)}
@media (prefers-color-scheme:dark){:root{--bg:#181715;--card:#22211e;--ink:#ece8df;--sub:#a49e92;--muted:#7d786f;--line:#34322d;--grid:#2a2926;--wk:#1f1e1b;--accent:#d2ad6b;
--good-t:rgba(12,163,12,.26);--warn-t:rgba(250,178,25,.22);--crit-t:rgba(208,59,59,.30);--plan-t:rgba(236,232,223,.05)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
.bar{max-width:1280px;margin:0 auto;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bar h1{font-size:17px;font-weight:500;letter-spacing:.04em;margin:0 auto 0 0}.bar a{color:var(--ink);text-decoration:none;font-size:14px}
main{max-width:1280px;margin:0 auto;padding:12px 16px 64px}
h2{font-weight:500;font-size:16px;margin:24px 0 10px}.sub{color:var(--sub);font-size:12px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:16px 0 12px}
.tab{background:none;color:var(--sub);border:1px solid transparent;border-bottom:0;border-radius:8px 8px 0 0;padding:8px 16px;font:inherit;cursor:pointer}
.tab[aria-selected=true]{background:var(--card);color:var(--ink);border-color:var(--line);margin-bottom:-1px;font-weight:600}
.tab .n{font-size:12px;color:var(--sub);margin-left:4px;font-weight:400}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--sub);margin:0 0 8px}.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{display:inline-block;width:22px;height:12px;border-radius:3px;border:1.5px solid}
.gwrap{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:auto;max-height:70vh}
.g{display:grid;position:relative}.gl{position:sticky;left:0;z-index:2;background:var(--card);border-right:1px solid var(--line);padding:6px 10px;border-bottom:1px solid var(--grid);min-width:0}
.gl b{font-weight:600;font-size:13px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.gl small{color:var(--sub);font-size:11px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gl.first{border-top:1px solid var(--line)}.gh{position:sticky;top:0;z-index:3;background:var(--card);border-bottom:1px solid var(--line)}
.gt{position:relative;border-bottom:1px solid var(--grid)}.day{position:absolute;top:0;bottom:0;border-left:1px solid var(--grid)}.day.we{background:var(--wk)}
.hd{position:absolute;top:0;font-size:11px;color:var(--sub);padding:3px 0 0 3px;white-space:nowrap}.hd.mon{color:var(--ink);font-weight:600}
.today{position:absolute;top:0;bottom:0;width:2px;background:var(--accent);z-index:1}.todaylab{position:absolute;top:0;font-size:10px;color:#fff;background:var(--accent);padding:0 4px;border-radius:3px;transform:translateX(-50%);z-index:4}
.b{position:absolute;height:20px;border-radius:4px;border:1.5px solid;font-size:11px;line-height:17px;padding:0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer;color:var(--ink);z-index:2}
.b:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.b.done{background:var(--good-t);border-color:var(--good)}.b.wait{background:var(--warn-t);border-color:var(--warn)}.b.ng{background:var(--crit-t);border-color:var(--crit);font-weight:600}
.b.run{background:transparent;border-color:var(--ink);border-style:dashed}.b.plan{background:var(--plan-t);border-color:var(--muted);border-style:dotted;color:var(--sub)}
.m{position:absolute;width:14px;height:14px;transform:rotate(45deg);border:2px solid;cursor:pointer;z-index:2}.m.done{background:var(--good);border-color:var(--good)}.m.plan{background:var(--card);border-color:var(--muted)}
#tip{position:fixed;z-index:20;max-width:320px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.15);pointer-events:none;display:none}
.panel{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:12px 14px;margin:12px 0}
.panel.ng{border-left-color:var(--crit)}.panel.done{border-left-color:var(--good)}
code{font-size:12px;color:var(--sub);word-break:break-all}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}
button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}button:disabled{opacity:.5;cursor:default}
.tools{display:flex;gap:8px;flex-wrap:wrap}ul.list{list-style:none;padding:0;margin:0}ul.list li{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;padding:8px 12px;margin:6px 0;font-size:14px}
ul.list li.ng{border-left-color:var(--crit)}ul.list li.run{border-left-color:var(--muted)}.who{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-right:6px;color:var(--sub)}
.days{color:var(--crit);font-size:12px;margin-left:4px}.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--sub);font-weight:500;white-space:nowrap}tr:last-child td{border-bottom:none}.empty{color:var(--sub)}
details{margin:10px 0}summary{cursor:pointer;color:var(--ink)}pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:12px;max-height:320px;overflow:auto}
input[type=date]{font:inherit;font-size:13px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.static .act-only{display:none!important}
.board{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
.board table{min-width:760px;font-size:13px;table-layout:fixed}.board th:first-child{width:220px}.board th{text-align:center;white-space:nowrap;padding:8px 4px;font-size:12px}.board th:first-child{text-align:left;padding-left:12px}
.board td{padding:6px 4px;text-align:center;vertical-align:middle}.board td.an{text-align:left;padding:8px 12px;min-width:170px;position:sticky;left:0;background:var(--card);z-index:1;border-right:1px solid var(--line)}
.board td.an b{display:block;font-size:14px}.board td.an small{display:block;color:var(--sub);font-size:12px;line-height:1.4}
.cell{width:100%;min-height:44px;border-radius:8px;border:1.5px solid var(--line);background:transparent;color:var(--sub);font:inherit;font-size:12px;cursor:pointer;padding:4px;line-height:1.35}
.cell:hover{outline:2px solid var(--accent);outline-offset:1px}.cell.done{background:var(--good-t);border-color:var(--good);color:var(--ink)}
.cell.wait{background:var(--warn-t);border-color:var(--warn);color:var(--ink);font-weight:600}.cell.ng{background:var(--crit-t);border-color:var(--crit);color:var(--ink);font-weight:700}
.cell.run{border-style:dashed;border-color:var(--ink);color:var(--ink)}.cell.plan{border-style:dotted;border-color:var(--muted)}
.cell .d{display:block;font-weight:400;font-size:11px;color:var(--crit)}
dialog{border:1px solid var(--line);border-radius:14px;padding:0;max-width:min(640px,calc(100vw - 32px));width:100%;background:var(--card);color:var(--ink);box-shadow:0 10px 40px rgba(0,0,0,.25)}
dialog::backdrop{background:rgba(0,0,0,.35)}.dlg-in{padding:18px 20px 20px}.dlg-in h3{margin:2px 0 8px;font-size:18px;font-weight:600}
.dlg-in h4{margin:16px 0 6px;font-size:14px;color:var(--sub);font-weight:600}.dlg-in ol{margin:0;padding-left:1.3em}.dlg-in ol li{margin:4px 0}
.x{position:absolute;right:10px;top:8px;background:transparent;color:var(--sub);font-size:22px;padding:2px 10px}
.badge{display:inline-block;font-size:12px;border-radius:999px;padding:2px 10px;border:1.5px solid}.badge.done{border-color:var(--good)}.badge.wait{border-color:var(--warn)}.badge.ng{border-color:var(--crit)}.badge.run{border-style:dashed}.badge.plan{border-style:dotted;border-color:var(--muted)}
.acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center}a.btn{display:inline-block;text-decoration:none;border-radius:6px;padding:6px 12px;font-size:13px;border:1px solid var(--line);color:var(--ink)}
#dlg-job{margin-top:10px;font-size:12px;color:var(--sub)}ul.list li.click{cursor:pointer}ul.list li.click:hover{outline:2px solid var(--accent)}
</style></head><body>
<header><div class="bar"><h1>Postdog Records</h1><a href="/suno" class="act-only">Suno に貼る内容</a><span class="sub" id="upd"></span></div></header>
<main>
<div class="tools act-only">
 <button data-act="scan">曲を取り込む</button><button data-act="track" class="ghost">配信を確かめる</button>
 <button data-act="sync" class="ghost">保管する（ローカル・SSD）</button>
</div>
<div id="jobs"></div>
<div class="tabs" role="tablist">
 <button class="tab" role="tab" id="tab-regular" aria-controls="pane-regular">定期配信<span class="n" id="n-regular"></span></button>
 <button class="tab" role="tab" id="tab-debut" aria-controls="pane-debut">デビュー<span class="n" id="n-debut"></span></button>
</div>
<div class="legend" aria-label="凡例">
 <span><i class="sw" style="background:var(--good-t);border-color:var(--good)"></i>✓ 済み</span>
 <span><i class="sw" style="background:var(--warn-t);border-color:var(--warn)"></i>● Gon さん（または Claude）の番</span>
 <span><i class="sw" style="background:var(--crit-t);border-color:var(--crit)"></i>！ 止まっている・遅れ</span>
 <span><i class="sw" style="border-style:dashed;border-color:var(--ink)"></i>… 自動で進行中</span>
 <span><i class="sw" style="border-style:dotted;border-color:var(--muted)"></i>○ 予定</span>
 <span>◆ 配信日（日程）</span>
</div>
<section id="pane-regular" role="tabpanel" aria-labelledby="tab-regular">
 <p class="sub">デビュー曲が配信された組の、いま作っている曲がどのフェーズにいるか。マスを押すと、止まっている原因と Gon さんがやること・ボタンが出ます。</p>
 <div class="board" id="b-regular"></div>
 <h2>今やること（定期配信）</h2><div id="todo-regular"></div>
 <details><summary>日程を見る（ガントチャート）</summary><div class="gwrap"><div class="g" id="g-regular"></div></div></details>
 <details><summary>曲の一覧</summary><div id="songs-regular"></div></details>
</section>
<section id="pane-debut" role="tabpanel" aria-labelledby="tab-debut" hidden>
 <p class="sub">デビュー前の組。デビュー曲が配信されると、自動で「定期配信」に移ります。マスを押すと、止まっている原因と Gon さんがやること・ボタンが出ます。</p>
 <div class="board" id="b-debut"></div>
 <h2>今やること（デビュー）</h2><div id="todo-debut"></div>
 <details><summary>日程を見る（ガントチャート）</summary><div class="gwrap"><div class="g" id="g-debut"></div></div></details>
 <details><summary>曲の一覧</summary><div id="songs-debut"></div></details>
</section>
<h2>仕組みの詰まり</h2><div id="system"></div>
<details><summary>管理表の「詰まっているところ」</summary><div id="tracker"></div></details>
</main>
<div id="tip" role="tooltip"></div>
<dialog id="dlg" aria-labelledby="dlg-title"><form method="dialog"><button class="x" aria-label="閉じる">×</button></form><div class="dlg-in" id="dlg-body"></div></dialog>
<script>
const TOKEN="__TOKEN__";const STATIC=__STATIC__;
if(STATIC)document.body.classList.add("static");
const E=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ICON={done:"✓",wait:"●",ng:"！",run:"…",plan:"○"},STATE={done:"済み",wait:"だれかの番",ng:"止まっている・遅れ",run:"自動で進行中",plan:"予定"};
const DAY=86400000,D=s=>new Date(s+"T00:00:00"),md=d=>`${d.getMonth()+1}/${d.getDate()}`,WD="日月火水木金土";
let DATA=null,TAB="regular";
function selectTab(t){TAB=t;for(const k of["regular","debut"]){document.getElementById("tab-"+k).setAttribute("aria-selected",k===t);document.getElementById("pane-"+k).hidden=k!==t}
  if(!location.hash.startsWith("#"+t))history.replaceState(null,"","#"+t)}
document.getElementById("tab-regular").onclick=()=>selectTab("regular");document.getElementById("tab-debut").onclick=()=>selectTab("debut");
selectTab(location.hash.startsWith("#debut")?"debut":"regular");
const OPEN={};
function gantt(key){
  const sec=DATA.timeline[key],el=document.getElementById("g-"+key);
  const small=window.innerWidth<640,from=D(sec.range.from),to=D(sec.range.to),n=Math.round((to-from)/DAY)+1,W=small?22:30,LW=small?150:240,today=D(DATA.timeline.today);
  el.style.gridTemplateColumns=`${LW}px ${n*W}px`;
  if(!sec.rows.length){el.style.gridTemplateColumns="1fr";el.innerHTML=`<div class="gl" style="position:static"><span class="empty">${key==="debut"?"デビュー前の組はありません。全組が定期配信に移りました。":"定期配信の組はまだありません。デビュー曲が配信されるとここに並びます。"}</span></div>`;return}
  const x=d=>Math.round((D(d)-from)/DAY)*W,tx=Math.round((today-from)/DAY)*W+W/2,showToday=today>=from&&today<=to;
  let days="";for(let i=0;i<n;i++){const d=new Date(+from+i*DAY);days+=`<div class="day ${d.getDay()%6===0?"we":""}" style="left:${i*W}px;width:${W}px"></div>`}
  const tline=showToday?`<div class="today" style="left:${tx-1}px"></div>`:"";
  let html=`<div class="gl gh" style="height:30px"><small>${key==="debut"?"組・デビュー曲／工程":"組・曲（制作週）／工程"}</small></div><div class="gt gh" style="height:30px">`;
  for(let i=0;i<n;i++){const d=new Date(+from+i*DAY);if(d.getDay()===1||i===0)html+=`<span class="hd mon" style="left:${i*W}px">${md(d)}（${WD[d.getDay()]}）</span>`}
  if(showToday)html+=`<span class="todaylab" style="left:${tx}px;top:14px">今日</span>`;
  html+=`</div>`;
  let prev="";
  sec.rows.forEach((r,ri)=>{
    const gid=key+":"+ri,steps=r.phases.filter(p=>!p.milestone),ms=r.phases.find(p=>p.milestone);
    const open=!r.planned&&(OPEN[gid]??(!!r.blocker||key==="debut"));
    const first=r.artist!==prev;prev=r.artist;
    const blk=r.blocker?`<small>${ICON[r.blocker.state]||""} ${E(r.blocker.who||"")}${r.blocker.who?"：":""}${E(r.blocker.name)}</small>`:(r.planned?"":`<small>${ICON.done} 進行中の工程なし</small>`);
    // 見出しの行（曲ごと）：全体の期間の細い線と ◆ 配信日
    const s0=Math.max(0,x(steps.reduce((a,p)=>p.start<a?p.start:a,steps[0].start))),e0=Math.min(n*W,x((ms||steps[steps.length-1]).end)+W);
    const hh=r.planned?32:60;
    html+=`<div class="gl ${first&&ri?"first":""}" style="height:${hh}px;cursor:${r.planned?"default":"pointer"}" ${r.planned?"":`data-g="${gid}"`}>`
      +`<b>${first?E(r.artist):""}${r.planned?"":` <span style="font-weight:400;color:var(--sub)">${open?"▾":"▸"}</span>`}</b><small>${E(r.sub)}</small>${blk}</div>`;
    html+=`<div class="gt ${first&&ri?"first":""}" style="height:${hh}px">${days}${tline}`;
    if(e0>s0)html+=`<div class="b ${r.planned?"plan":"run"}" style="left:${s0+1}px;top:${r.planned?7:20}px;width:${Math.max(8,e0-s0-2)}px;height:16px;line-height:13px;border-style:${r.planned?"dotted":"solid"};border-color:var(--muted);background:var(--plan-t);color:var(--sub)" data-p="${gid}:${r.phases.indexOf(ms||steps[0])}">${r.planned?"○ 予定：ブリーフ → 配信":""}</div>`;
    if(ms){const mx=x(ms.start)+W/2-7;if(mx>=0&&mx<=n*W)html+=`<div class="m ${ms.state==="done"?"done":"plan"}" tabindex="0" data-p="${gid}:${r.phases.indexOf(ms)}" style="left:${mx}px;top:${r.planned?9:21}px" aria-label="${E(ms.name)} ${E(ms.start)}"></div>`}
    html+=`</div>`;
    if(!open)return;
    steps.forEach(p=>{
      const id=`${gid}:${r.phases.indexOf(p)}`,s=Math.max(0,x(p.start)),e=Math.min(n*W,x(p.end)+W),d1=D(p.start),d2=D(p.end);
      const range=p.start===p.end?md(d1):`${md(d1)}〜${md(d2)}`;
      html+=`<div class="gl" style="height:28px;padding:4px 10px 4px 22px"><small style="color:var(--ink);font-size:12px">${ICON[p.state]} ${E(p.name)}${p.who?` <span style="color:var(--sub)">・${E(p.who)}</span>`:""}</small></div>`;
      html+=`<div class="gt" style="height:28px">${days}${tline}`;
      if(e>0&&s<n*W){const w=Math.max(10,e-s-2);html+=`<div class="b ${p.state}" tabindex="0" data-p="${id}" style="left:${s+1}px;top:4px;width:${w}px" aria-label="${E(p.name)}：${E(STATE[p.state])} ${range}">${w>70?`${ICON[p.state]} ${range}`:ICON[p.state]}</div>`}
      html+=`</div>`});
  });
  el.innerHTML=html;
  el.parentElement.scrollLeft=Math.max(0,tx-W*6);
}
document.addEventListener("click",e=>{const g=e.target.closest("[data-g]");if(!g)return;const[k,ri]=g.dataset.g.split(":");const r=DATA.timeline[k].rows[+ri];
  OPEN[g.dataset.g]=!(OPEN[g.dataset.g]??(!!r.blocker||k==="debut"));gantt(k)});
function phaseOf(id){const[k,ri,pi]=id.split(":");const r=DATA.timeline[k].rows[+ri];return[k,r,r.phases[+pi]]}
function tipHTML(r,p){const d1=D(p.start),d2=D(p.end);return `<b>${E(r.artist)}</b> ${E(r.sub)}<br><b>${ICON[p.state]} ${E(p.name)}</b>（${E(STATE[p.state])}）<br>${md(d1)}${p.start!==p.end?"〜"+md(d2):""}${p.who?`　担当：${E(p.who)}`:""}<br>${E(p.detail)}`}
const tip=document.getElementById("tip");
document.addEventListener("mouseover",e=>{const b=e.target.closest("[data-p]");if(!b){tip.style.display="none";return}const[,r,p]=phaseOf(b.dataset.p);tip.innerHTML=tipHTML(r,p);tip.style.display="block"});
document.addEventListener("mousemove",e=>{if(tip.style.display==="block"){tip.style.left=Math.min(e.clientX+14,innerWidth-330)+"px";tip.style.top=(e.clientY+14)+"px"}});
const WHO=w=>String(w||"").replace(/あなた/g,"Gon さん");
const LABEL={done:"済み",wait:"Gon さん（または Claude）の番",ng:"止まっている・遅れ",run:"自動で進行中",plan:"まだ先"};
const daysSince=s=>Math.max(0,Math.round((D(DATA.timeline.today)-D(s))/DAY));
let ACTS=[];
function actHTML(a,i,r){const id=E(a.id||r.id||""),slug=E(a.slug||r.slug||"");
  switch(a.kind){
   case "run":return `<button class="act-only" data-act="${E(a.action)}" ${a.slug?`data-slug="${slug}"`:""} ${a.id?`data-id="${id}"`:""} ${a.week?`data-week="${E(a.week)}"`:""} ${a.confirm?`data-confirm="${E(a.confirm)}"`:""}>${E(a.label)}</button>`;
   case "copy":return `<button class="ghost" data-copy="${i}">${E(a.label)}</button>`;
   case "link":return `<a class="btn" href="${E(a.url)}" target="_blank" rel="noopener">${E(a.label)} ↗</a>`;
   case "terminal":return `<button class="ghost act-only" data-act="terminal" data-key="${E(a.key)}">${E(a.label)}</button>`;
   case "folder":return `<button class="ghost act-only" data-act="open_folder" data-id="${id}">${E(a.label)}</button>`;
   case "artist_folder":return `<button class="ghost act-only" data-act="open_artist" data-slug="${slug}">${E(a.label)}</button>`;
   case "registered":return `<span class="act-only"><input type="date" id="d-${id}" value="${DATA.timeline.today}"> <button data-act="registered" data-id="${id}">${E(a.label)}</button></span>`;
  }return""}
function openGuide(gid){const[k,ri,pi]=gid.split(":");const r=DATA.timeline[k].rows[+ri],p=r.phases[+pi],g=p.guide||{cause:p.detail,steps:[],actions:[]};
  ACTS=g.actions||[];const cur=["wait","ng","run"].includes(p.state)&&!p.milestone;const d=cur?daysSince(p.start):0;
  const dates=p.start===p.end?md(D(p.start)):`${md(D(p.start))}〜${md(D(p.end))}`;
  document.getElementById("dlg-body").innerHTML=`<p class="sub">${E(r.artist)}・${E(r.sub)}</p>
   <h3 id="dlg-title">${ICON[p.state]} ${E(p.name)}</h3>
   <span class="badge ${p.state}">${E(LABEL[p.state])}${cur&&p.who?`・${E(WHO(p.who))}`:""}${d?`・${d} 日`:""}</span> <span class="sub">${p.state==="plan"?"予定 ":""}${dates}</span>
   <h4>${p.state==="ng"?"止まっている原因":"いまの状態"}</h4><p>${E(WHO(g.cause))}</p>
   ${(g.steps||[]).length?`<h4>Gon さんがやること</h4><ol>${g.steps.map(x=>`<li>${E(WHO(x))}</li>`).join("")}</ol>`:""}
   ${ACTS.length?`<div class="acts">${ACTS.map((a,i)=>actHTML(a,i,r)).join("")}</div>`:""}<div id="dlg-job"></div>`;
  const dlg=document.getElementById("dlg");if(!dlg.open)dlg.showModal()}
document.addEventListener("click",e=>{const c=e.target.closest("[data-copy]");if(!c)return;const t=(ACTS[+c.dataset.copy]||{}).text||"";
  const done=()=>{const o=c.textContent;c.textContent="コピーしました";setTimeout(()=>c.textContent=o,1600)};
  if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(t).then(done);else{const ta=document.createElement("textarea");ta.value=t;document.body.appendChild(ta);ta.select();document.execCommand("copy");ta.remove();done()}});
document.addEventListener("click",e=>{const b=e.target.closest("[data-p],[data-g2]");if(b)openGuide(b.dataset.p||b.dataset.g2)});
document.addEventListener("keydown",e=>{const b=e.target.closest&&e.target.closest("[data-p],li[data-g2]");if(b&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openGuide(b.dataset.p||b.dataset.g2)}});
const COLS={regular:[["ブリーフ","ブリーフ"],["Suno で作曲","作曲（Suno）"],["仕上げ（音量・ジャケット）","仕上げ"],["DistroKid に登録","DistroKid 登録"],["配信待ち","配信待ち"],["配信","配信中"]],
  debut:[["名前の確認","名前の確認"],["写真・ジャケット","写真・ジャケット"],["Suno で作曲","作曲（Suno）"],["仕上げ（音量・ジャケット）","仕上げ"],["DistroKid に登録","DistroKid 登録"],["配信待ち","配信待ち"],["デビュー","デビュー"]]};
function boardRows(key){const rows=DATA.timeline[key].rows,by={};const order=[];
  rows.forEach((r,ri)=>{if(!by[r.slug]){by[r.slug]=[];order.push(r.slug)}by[r.slug].push(ri)});
  const out=[];order.forEach(slug=>{const idx=by[slug];
    const inflight=idx.filter(ri=>!rows[ri].planned&&rows[ri].phases.some(p=>!p.milestone&&p.state!=="done"));
    if(inflight.length){inflight.forEach((ri,n)=>out.push([ri,n===0]));return}
    const next=idx.find(ri=>rows[ri].planned);out.push([next!==undefined?next:idx[idx.length-1],true])});return out}
function board(key){const el=document.getElementById("b-"+key),rows=DATA.timeline[key].rows;
  if(!rows.length){el.innerHTML=`<p class="empty" style="padding:12px">${key==="debut"?"デビュー前の組はありません。全組が定期配信に移りました。":"定期配信の組はまだありません。デビュー曲が配信されると、ここに並びます（いまは「デビュー」のタブ）。"}</p>`;return}
  const cols=COLS[key];
  let h=`<table><tr><th>アーティスト・曲</th>${cols.map(c=>`<th>${E(c[1])}</th>`).join("")}</tr>`;
  boardRows(key).forEach(([ri,first])=>{const r=rows[ri];
    const c=r.phases.find(p=>["ng","wait","run"].includes(p.state)&&!p.milestone);
    const now=c?`いま：${ICON[c.state]} ${E(c.name)}（${E(WHO(c.who)||STATE[c.state])}）`:(r.planned?"次の制作の予定":"✓ 進行中の工程なし");
    h+=`<tr><td class="an">${first?`<b>${E(r.artist)}</b>`:""}<small>${E(r.sub)}</small><small>${now}</small></td>`;
    cols.forEach(([name])=>{const pi=r.phases.findIndex(p=>p.name===name);
      if(pi<0){h+=`<td><span class="sub">—</span></td>`;return}
      const p=r.phases[pi],cur=["wait","ng","run"].includes(p.state)&&!p.milestone,d=cur?daysSince(p.start):0;
      let txt=p.state==="done"?"✓":p.state==="plan"?"○":`${ICON[p.state]} ${E(WHO(p.who)||STATE[p.state])}`;
      if(p.milestone&&p.state!=="done")txt=`○ ${md(D(p.start))}`;
      h+=`<td><button class="cell ${p.state}" data-g2="${key}:${ri}:${pi}" aria-label="${E(p.name)}：${E(LABEL[p.state])}">${txt}${d?`<span class="d">${d} 日</span>`:""}</button></td>`});
    h+="</tr>"});
  el.innerHTML=h+"</table>"}
function todoList(key){
  const rows=DATA.timeline[key].rows;const items=[];
  rows.forEach((r,ri)=>{if(r.planned)return;r.phases.forEach((p,pi)=>{if(["wait","ng","run"].includes(p.state)&&!p.milestone)items.push([r,p,`${key}:${ri}:${pi}`])})});
  let html=items.sort((a,b)=>({ng:0,wait:1,run:2}[a[1].state]-{ng:0,wait:1,run:2}[b[1].state])).map(([r,p,gid])=>`<li class="${p.state} click" data-g2="${gid}" tabindex="0"><span class="who">${E(WHO(p.who)||"—")}</span><b>${E(r.artist)}</b> ${E(r.sub)}　${ICON[p.state]} <b>${E(p.name)}</b>：${E(WHO((p.guide||{}).cause||p.detail))} <span class="sub">→ 押すと、やることとボタン</span></li>`).join("");
  if(key==="regular")html+=DATA.weekly_todos.map(t=>`<li class="${t.sev==="ng"?"ng":""}"><b>${E(t.what)}</b>（${t.who.length}）<br>${E(t.who.join("、"))}<br><code>${E(t.where)}</code></li>`).join("");
  document.getElementById("todo-"+key).innerHTML=html?`<ul class="list">${html}</ul>`:`<p class="empty">いまやることはありません</p>`;
}
function songsTable(key){const ss=DATA.songs.filter(s=>s.flow===key&&(key==="debut"?s.kind==="debut":s.kind==="weekly"));
  document.getElementById("songs-"+key).innerHTML=ss.length?`<div class="tbl"><table><tr><th>曲 ID</th><th>アーティスト</th><th>曲名</th><th>段階</th><th>配信日</th><th>ISRC</th><th class="act-only"></th></tr>${ss.map(s=>{
    let ops="";if(s.folder)ops+=`<button class="ghost" data-act="open_folder" data-id="${E(s.id)}">フォルダを開く</button> `;if(s.url)ops+=`<a href="${E(s.url)}" target="_blank" rel="noopener">Spotify</a>`;
    return `<tr><td>${E(s.id)}</td><td>${E(s.artist)}</td><td>${E(s.title||"（曲名未定）")}</td><td>${E(s.status_ja)}</td><td>${E(s.release_date||"—")}</td><td>${E(s.isrc||"—")}</td><td class="act-only">${ops}</td></tr>`}).join("")}</table></div>`:`<p class="empty">曲はまだありません</p>`}
async function act(action,params={},btn){if(STATIC)return;if(btn)btn.disabled=true;
  try{const r=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json","X-Label-Token":TOKEN},body:JSON.stringify({action,...params})});
    const j=await r.json();if(!r.ok){alert(j.error||"実行できませんでした");return}if(j.job)watch(j.job)}finally{if(btn)setTimeout(()=>btn.disabled=false,1500)}}
const watching={};
async function watch(id){watching[id]=true;const box=document.getElementById("jobs");
  for(;;){const r=await fetch("/api/jobs/"+id);const j=await r.json();let el=document.getElementById("job-"+id);
    if(!el){el=document.createElement("details");el.id="job-"+id;el.open=true;box.prepend(el)}
    el.innerHTML=`<summary>${E(j.label)}：${j.state==="running"?"実行中…":j.state==="done"?"終わりました":"エラーがありました"}（${E(j.started)}）</summary><pre>${E(j.out||"")}</pre>`;
    const dj=document.getElementById("dlg-job");if(dj&&document.getElementById("dlg").open)dj.textContent=`${j.label}：${j.state==="running"?"実行中…":j.state==="done"?"終わりました ✓":"エラーがありました（画面上のログを確認）"}`;
    if(j.state!=="running"){load();break}await new Promise(r=>setTimeout(r,2000))}}
document.addEventListener("click",e=>{const b=e.target.closest("button[data-act]");if(!b)return;
  const p={};if(b.dataset.slug)p.slug=b.dataset.slug;if(b.dataset.id)p.id=b.dataset.id;
  if(b.dataset.act==="registered"){p.date=document.getElementById("d-"+b.dataset.id).value;if(!confirm(`DistroKid に登録した（配信日 ${p.date}）と記録しますか？`))return}
  if(b.dataset.week)p.week=b.dataset.week;if(b.dataset.key)p.key=b.dataset.key;
  if(b.dataset.confirm&&!confirm(b.dataset.confirm))return;
  act(b.dataset.act,p,b)});
function render(){
  document.getElementById("upd").textContent=DATA.generated+" 時点";
  for(const k of["regular","debut"]){const rs=DATA.timeline[k].rows;const nArt=new Set(rs.map(r=>r.slug)).size;
    document.getElementById("n-"+k).textContent=k==="debut"?`${nArt} 組`:`${nArt} 組・${rs.filter(r=>!r.planned).length} 曲`;board(k);gantt(k);todoList(k);songsTable(k)}
  document.getElementById("system").innerHTML=DATA.system.length?`<ul class="list">${DATA.system.map(x=>`<li class="${x.sev}"><b>${E(x.what)}</b><br><code>${E(x.how)}</code></li>`).join("")}</ul>`:`<p class="empty">仕組みの詰まりはありません</p>`;
  const t=DATA.tracker,cols=["順番","詰まっていること","担当","次の一歩","状態"];
  document.getElementById("tracker").innerHTML=t.rows.length?`<div class="tbl"><table><tr>${cols.map(c=>`<th>${c}</th>`).join("")}</tr>${t.rows.map(r=>`<tr>${cols.map(c=>`<td>${E(r[c])}</td>`).join("")}</tr>`).join("")}</table></div><p class="sub">読んだ場所：${E(t.src)}</p>`:`<p class="empty">完了していない項目はありません（${E(t.src)}）</p>`;
  if(!STATIC)DATA.jobs.filter(j=>j.state==="running"&&!watching[j.id]).forEach(j=>watch(j.id));
}
let opened=false;
function deepLink(){if(opened)return;opened=true;const m=location.hash.match(/open=([a-z]+:\d+:\d+)/);if(m)try{openGuide(m[1])}catch(e){}}
async function load(){if(STATIC){DATA=STATIC;render();deepLink();return}const r=await fetch("/api/status");DATA=await r.json();
  render();deepLink()}
let rz;addEventListener("resize",()=>{clearTimeout(rz);rz=setTimeout(()=>DATA&&render(),200)});
load();if(!STATIC)setInterval(load,30000);
</script></body></html>"""


def render_page(static_data: dict | None = None) -> str:
    """画面の HTML。static_data を渡すと、データを埋め込んだ読むだけの画面（ボタンなし。dashboard.py の書き出し用）"""
    data = json.dumps(static_data, ensure_ascii=False, default=str).replace("</", "<\\/") if static_data else "null"
    return PAGE.replace("__TOKEN__", "" if static_data else TOKEN).replace("__STATIC__", data)


class Handler(BaseHTTPRequestHandler):
    server_version = "LabelDashboard"
    lan_key: str | None = None

    def log_message(self, fmt, *args):   # アクセスの記録は出さない
        pass

    def _allowed(self) -> bool:
        if not self.lan_key:
            return True
        q = parse_qs(urlparse(self.path).query)
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        ok = secrets.compare_digest((q.get("key") or [""])[0], self.lan_key) or \
            ("label_key" in c and secrets.compare_digest(c["label_key"].value, self.lan_key))
        return ok

    def _send(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", extra: dict | None = None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._allowed():
            return self._send(403, "<p>合鍵つきの URL で開いてください（webapp.py --lan を起動したときに表示されます）</p>")
        path = urlparse(self.path).path
        extra = {}
        if self.lan_key:
            extra["Set-Cookie"] = f"label_key={self.lan_key}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
        if path == "/":
            return self._send(200, render_page(), extra=extra)
        if path == "/suno":
            from publish import suno_page
            page, _ = suno_page()
            back = '<p style="max-width:860px;margin:16px auto 0;padding:0 16px"><a href="/">← ダッシュボードへ</a></p>'
            return self._send(200, page.replace("<main>", back + "<main>", 1), extra=extra)
        if path == "/api/status":
            try:
                return self._json(200, status_json())
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"状態を読めませんでした：{e}"})
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})", path)
        if m and m.group(1) in JOBS:
            return self._json(200, JOBS[m.group(1)])
        return self._send(404, "<p>見つかりません</p>")

    def do_POST(self):
        if not self._allowed() or not secrets.compare_digest(self.headers.get("X-Label-Token", ""), TOKEN):
            return self._json(403, {"error": "このページを開き直してから押してください"})
        if urlparse(self.path).path != "/api/action":
            return self._json(404, {"error": "見つかりません"})
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 10000)
            p = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "内容が読めません"})
        found = command_for(str(p.get("action", "")), p)
        if not found:
            return self._json(400, {"error": "その操作はできません"})
        if any(j["state"] == "running" and j["label"] == found[1] for j in JOBS.values()):
            return self._json(409, {"error": "同じ仕事がまだ動いています"})
        return self._json(200, {"job": start_job(*found)})


def lan_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "（この Mac の IP アドレス）"


def lan_key() -> str:
    key = os.environ.get("WEBAPP_KEY", "").strip()
    if key:
        return key
    key = secrets.token_urlsafe(18)
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    env.write_text("\n".join([*lines, f"WEBAPP_KEY={key}"]).rstrip("\n") + "\n", encoding="utf-8")
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description="ダッシュボードのウェブアプリ（Mac の中で動く）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("WEBAPP_PORT", "8765")))
    ap.add_argument("--lan", action="store_true", help="家の Wi-Fi のほかの機器（スマホなど）からも見られるようにする（合鍵つき）")
    a = ap.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        Handler.lan_key = lan_key()
    srv = ThreadingHTTPServer((host, a.port), Handler)
    print(f"▶ ダッシュボード：http://localhost:{a.port}")
    if a.lan:
        print(f"   スマホなど（同じ Wi-Fi）：http://{lan_address()}:{a.port}/?key={Handler.lan_key}")
        print("   （この URL は合鍵つき。ほかの人に教えない。1 回開けば、その機器では次から合鍵なしで開けます）")
    print("   止めるときは control + C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
