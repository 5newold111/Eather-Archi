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
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "artists": arts,
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
            return ["open", str(folder)], f"フォルダを開く（{song['title']}）"
    return None


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
:root{--bg:#f6f4ef;--card:#fff;--ink:#22201c;--sub:#6f6a60;--line:#e2ddd2;--accent:#a07a3c;--ng:#b3412e;--ok:#4f7a4a}
@media (prefers-color-scheme:dark){:root{--bg:#181715;--card:#22211e;--ink:#ece8df;--sub:#a49e92;--line:#34322d;--accent:#d2ad6b;--ng:#e07a66;--ok:#8fbf86}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line)}
.bar{max-width:1180px;margin:0 auto;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bar h1{font-size:17px;font-weight:500;letter-spacing:.04em;margin:0 auto 0 0}.bar a{color:var(--ink);text-decoration:none;font-size:14px;padding:4px 10px;border-radius:6px}
.bar a.on{background:var(--card);border:1px solid var(--line)}main{max-width:1180px;margin:0 auto;padding:16px 16px 64px}
h2{font-weight:500;font-size:17px;border-bottom:1px solid var(--line);padding-bottom:6px;margin:28px 0 12px}
.sub{color:var(--sub);font-size:12px}.chips{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:13px}
.acts{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr));gap:12px}
.act{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:10px;padding:14px 16px}
.act.ng{border-left-color:var(--ng)}.act.wait{border-left-color:var(--accent)}.act.ok{border-left-color:var(--ok)}
.act h3{margin:0 0 8px;font-weight:500;font-size:16px;display:flex;justify-content:space-between;gap:8px}.act h3 small{color:var(--sub);font-weight:400;font-size:12px}
.steps{display:flex;gap:4px;flex-wrap:wrap;margin:0 0 8px}.st{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--sub)}
.st.ok{background:var(--ok);border-color:var(--ok);color:var(--card)}.st.ng{background:var(--ng);border-color:var(--ng);color:var(--card)}
.st.wait{border-color:var(--accent);color:var(--accent);font-weight:600}.st.run{border-style:dashed;color:var(--ink)}
.do{font-size:13px;margin:6px 0 0;padding-top:6px;border-top:1px dashed var(--line)}.who{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-right:6px;color:var(--sub)}
.days{color:var(--ng);font-size:12px;margin-left:4px}code{font-size:12px;color:var(--sub);word-break:break-all}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}
button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}button:disabled{opacity:.5;cursor:default}
.tools{display:flex;gap:8px;flex-wrap:wrap}ul.list{list-style:none;padding:0;margin:0}ul.list li{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:10px 14px;margin:8px 0}
ul.list li.ng{border-left-color:var(--ng)}.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--sub);font-weight:500;white-space:nowrap}tr:last-child td{border-bottom:none}.empty{color:var(--sub)}
pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:12px;max-height:320px;overflow:auto}
input[type=date]{font:inherit;font-size:13px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
</style></head><body>
<header><div class="bar"><h1>Postdog Records</h1><a href="/" class="on">ダッシュボード</a><a href="/suno">Suno に貼る内容</a>
<span class="sub" id="upd"></span></div></header>
<main>
<div class="tools">
 <button data-act="scan">曲を取り込む</button><button data-act="track" class="ghost">配信を確かめる</button>
 <button data-act="sync" class="ghost">保管する（ローカル・SSD）</button>
</div>
<div id="jobs"></div>
<h2>アーティストごとの進み具合</h2><div id="chips" class="chips"></div><div id="acts" class="acts"></div>
<p class="sub">緑＝済み・赤＝止まっている・金の枠＝だれかの番・点線＝自動で進行中・灰＝まだ先。日数は止まっている日数</p>
<h2>曲の一覧</h2><div id="songs"></div>
<h2>仕組みの詰まり</h2><div id="system"></div>
<h2>管理表の「詰まっているところ」</h2><div id="tracker"></div>
</main>
<script>
const TOKEN="__TOKEN__";
const E=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const sym={ok:"済み",run:"自動で進行中",wait:"番を待っている",ng:"止まっている",off:"まだ先"};
async function act(action,params={},btn){
  if(btn)btn.disabled=true;
  try{const r=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json","X-Label-Token":TOKEN},body:JSON.stringify({action,...params})});
    const j=await r.json(); if(!r.ok){alert(j.error||"実行できませんでした");return}
    if(j.job)watch(j.job);}finally{if(btn)setTimeout(()=>btn.disabled=false,1500)}
}
const watching={};
async function watch(id){
  watching[id]=true;
  const box=document.getElementById("jobs");
  for(;;){const r=await fetch("/api/jobs/"+id);const j=await r.json();
    let el=document.getElementById("job-"+id);if(!el){el=document.createElement("details");el.id="job-"+id;el.open=true;box.prepend(el)}
    el.innerHTML=`<summary>${E(j.label)}：${j.state==="running"?"実行中…":j.state==="done"?"終わりました":"エラーがありました"}（${E(j.started)}）</summary><pre>${E(j.out||"")}</pre>`;
    if(j.state!=="running"){load();break}
    await new Promise(r=>setTimeout(r,2000));}
}
function steps(a){return a.steps.map(s=>`<span class="st ${s.state}" title="${E(sym[s.state])}：${E(s.detail)}">${E(s.name)}</span>`).join("")}
function todo(a){
  const open=a.steps.filter(s=>s.state!=="ok"&&s.state!=="off");
  if(!open.length)return `<div class="do">すべて順調です</div>`;
  return open.map(s=>{
    let btn="";
    if(s.name==="写真・ジャケット"&&s.who==="あなた"&&!/鍵/.test(s.detail))btn=`<br><button data-act="images" data-slug="${E(a.slug)}">写真とジャケットを作る</button>`;
    if(s.name==="デビュー曲"&&/Suno/.test(s.detail))btn=`<br><a href="/suno"><button class="ghost">Suno に貼る内容を開く</button></a>`;
    return `<div class="do"><span class="who">${E(s.who||"—")}</span><b>${E(s.name)}</b>：${E(s.detail)}${s.days>0?`<span class="days">${s.days} 日</span>`:""}`
      +(s.action?`<br><code>${E(s.action)}</code>`:"")+btn+`</div>`}).join("");
}
function songRow(s){
  let ops="";
  if(s.folder)ops+=`<button class="ghost" data-act="open_folder" data-id="${E(s.id)}">フォルダを開く</button> `;
  if(s.status==="ready")ops+=`<input type="date" id="d-${E(s.id)}" value="${new Date().toISOString().slice(0,10)}"> <button data-act="registered" data-id="${E(s.id)}">DistroKid に登録した</button>`;
  if(s.url)ops+=`<a href="${E(s.url)}" target="_blank" rel="noopener">Spotify</a>`;
  return `<tr><td>${E(s.id)}</td><td>${E(s.artist)}</td><td>${E(s.title||"（曲名未定）")}</td><td>${E(s.status_ja)}</td><td>${E(s.release_date||"—")}</td><td>${E(s.isrc||"—")}</td><td>${ops}</td></tr>`;
}
async function load(){
  const r=await fetch("/api/status");const d=await r.json();
  document.getElementById("upd").textContent=d.generated+" 時点";
  const cnt={};d.artists.forEach(a=>{const b=a.blocker;const k=!b?"すべて順調":b.state==="ng"?"止まっている":b.state==="wait"?b.who+"の番":"自動で進行中";cnt[k]=(cnt[k]||0)+1});
  document.getElementById("chips").innerHTML=Object.entries(cnt).map(([k,v])=>`<span class="chip">${E(k)}：${v} 組</span>`).join("");
  document.getElementById("acts").innerHTML=d.artists.map(a=>`<div class="act ${a.blocker?a.blocker.state:"ok"}"><h3><span>${E(a.name)}</span><small>${E(a.label)}</small></h3><div class="steps">${steps(a)}</div>${todo(a)}</div>`).join("")||`<p class="empty">組がまだありません</p>`;
  document.getElementById("songs").innerHTML=d.songs.length?`<div class="tbl"><table><tr><th>曲 ID</th><th>アーティスト</th><th>曲名</th><th>段階</th><th>配信日</th><th>ISRC</th><th></th></tr>${d.songs.map(songRow).join("")}</table></div>`:`<p class="empty">曲はまだありません</p>`;
  document.getElementById("system").innerHTML=d.system.length?`<ul class="list">${d.system.map(x=>`<li class="${x.sev}"><b>${E(x.what)}</b><br><code>${E(x.how)}</code></li>`).join("")}</ul>`:`<p class="empty">仕組みの詰まりはありません</p>`;
  const t=d.tracker;const cols=["順番","詰まっていること","担当","次の一歩","状態"];
  document.getElementById("tracker").innerHTML=t.rows.length?`<div class="tbl"><table><tr>${cols.map(c=>`<th>${c}</th>`).join("")}</tr>${t.rows.map(r=>`<tr>${cols.map(c=>`<td>${E(r[c])}</td>`).join("")}</tr>`).join("")}</table></div><p class="sub">読んだ場所：${E(t.src)}</p>`:`<p class="empty">完了していない項目はありません（${E(t.src)}）</p>`;
  d.jobs.filter(j=>j.state==="running"&&!watching[j.id]).forEach(j=>watch(j.id));
}
document.addEventListener("click",e=>{const b=e.target.closest("button[data-act]");if(!b)return;
  const p={};if(b.dataset.slug)p.slug=b.dataset.slug;if(b.dataset.id)p.id=b.dataset.id;
  if(b.dataset.act==="registered"){p.date=document.getElementById("d-"+b.dataset.id).value;if(!confirm(`DistroKid に登録した（配信日 ${p.date}）と記録しますか？`))return}
  if(b.dataset.act==="images"&&!confirm("写真とジャケットを作ります（OpenAI の画像の料金がかかります）。よいですか？"))return;
  act(b.dataset.act,p,b)});
load();setInterval(load,30000);
</script></body></html>"""


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
            return self._send(200, PAGE.replace("__TOKEN__", TOKEN), extra=extra)
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
