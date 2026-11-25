"""全部を試しに流す点検（2）：ダッシュボードの全ページ・全操作を作り物のレーベルで試す（リポジトリは変えない。決定 180）

使い方（music-label で。押して安全な操作まで流すときは --run）
  python3 tests/sweep_web.py [結果のフォルダ] [--run]
  DistroKid・Suno の入力・ブラウザ・ターミナル・削除・外す・送るなどの操作は、--run でも流さない（命令の組み立てだけ見る）
スキル：.claude/skills/system-sweep/SKILL.md
"""
import json, re, sys, threading, time, urllib.request, urllib.error, contextlib, io, traceback
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from _fixture import Project, STAGES  # noqa

import tempfile
args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUTDIR = Path(args[0]) if args else Path(tempfile.mkdtemp()); OUTDIR.mkdir(parents=True, exist_ok=True)
import test_deep_analysis as TD  # noqa  （作り物のレーベルを作る前に。あとで読み込むと本物の scripts が混ざる）
import deep_analysis as _DA  # noqa
deep = TD._fake_deep(); deep["card"] = _DA.card(deep)
RUN = "--run" in sys.argv
p = Project(ssd=True, debuted=("declan_fay", "hollowplan"))
keys = [p.weekly("hollowplan", p.week(-i - 1), f"Song {st}", stage=st, days_ago=i) for i, st in enumerate(STAGES)]
kd = p.debut("tidewool", "The Low Part", "imported")
try:
    ki = p.irregular("declan_fay", p.week(1), "Irregular One", "brief")
except Exception as e:  # noqa
    ki = None; print("irregular fixture failed", e)
# 参考曲の深い分析のデータ
ac = p.out / "reference_db" / "audio"; ac.mkdir(parents=True, exist_ok=True)
(ac / "aud-000000000001.json").write_text(json.dumps({"reference": {"artist_name": "Some Band", "title": "Some Song"},
    "deep": {k: v for k, v in deep.items() if k != "_grid"}}, ensure_ascii=False), encoding="utf-8")
w = p.mod("webapp")
from http.server import ThreadingHTTPServer
errors_srv = []
class H(w.Handler):
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
def handle_error(req, addr):
    errors_srv.append(traceback.format_exc())
srv.handle_error = handle_error
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
def get(path):
    try:
        with urllib.request.urlopen(base + path, timeout=120) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa
        return -1, f"{type(e).__name__}: {e}"
def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Label-Token": w.TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except Exception as e:  # noqa
        return -1, {"error": f"{type(e).__name__}: {e}"}

songs = w._songs.all_songs()
ids = [s["id"] for s in songs]
print("曲", ids)
ready = next((s["id"] for s in songs if w._songs.state(s).get("status") == "ready"), ids[0])
cover = next(iter(sorted(p.out.rglob("cover_final.jpg"))), None)
paths = ["/", "/suno", "/paste", f"/paste/{ids[0]}", "/cover-trial", "/deep", "/deep?id=aud-000000000001", "/plays", "/keys", "/pitch",
         f"/pitch?id={ready}", "/money", "/money?year=2026", "/listen", f"/listen?id={ready}", "/photos", "/phone", f"/phone/{ids[0]}",
         "/check", f"/song/{ids[0]}", "/api/busy", "/api/book/act?id=x", "/api/retire/impact?slug=hollowplan", "/api/status", "/nope"]
paths += [f"/song/{i}" for i in ids[1:]] + [f"/phone/{i}" for i in ids[1:]] + [f"/listen?id={i}" for i in ids] + [f"/pitch?id={i}" for i in ids]
if cover:
    paths.append(f"/file?p={urllib.request.quote(str(cover))}")
res = {"get": [], "actions": [], "srv_errors": errors_srv}
pages = {}
for pa in paths:
    t = time.time(); code, body = get(pa)
    bad = code >= 500 or code < 0 or "Traceback" in body or (code == 200 and len(body) < 60 and not pa.startswith("/api") and not pa.startswith("/file"))
    res["get"].append({"path": pa, "code": code, "len": len(body), "bad": bad, "sec": round(time.time() - t, 1), "head": body[:300] if bad else ""})
    if code == 200 and "text" not in pa and not pa.startswith("/api"):
        pages[pa] = body
(OUTDIR / "pages").mkdir(exist_ok=True)
for pa, body in pages.items():
    (OUTDIR / "pages" / (re.sub(r"[^\w]+", "_", pa).strip("_") or "root")).with_suffix(".html").write_text(body, encoding="utf-8")
# ページの HTML のボタン
found = {}
for pa, body in pages.items():
    for a in re.findall(r'data-act="([a-z_0-9]+)"', body):
        found.setdefault(a, set()).add(pa)
    for js in re.findall(r'data-p="([^"]+)"', body):
        try:
            d = json.loads(js.replace("&quot;", '"').replace("&amp;", "&").replace("&#x27;", "'"))
            found.setdefault(d.get("action"), set()).add(pa)
        except Exception:  # noqa
            pass
# 受ける側の全操作
src = (p.scripts / "webapp.py").read_text(encoding="utf-8")
i = src.index("def command_for"); body = src[i:src.index("\ndef ", i + 10)]
names = set(re.findall(r'action == "([a-z_0-9]+)"', body))
for m in re.findall(r"action in \(([^)]*)\)", body):
    names |= set(re.findall(r'"([a-z_0-9]+)"', m))
res["page_buttons"] = {k: sorted(v) for k, v in found.items() if k}
res["unhandled_page_buttons"] = sorted(k for k in found if k and k not in names)
slugs = ["hollowplan", "declan_fay", "tidewool"]
cands = [{}] + [{"id": i} for i in ids] + [{"slug": s} for s in slugs] + [{"job": "health"}, {"service": "r2"}, {"service": "anthropic"}, {"site": "suno"},
        {"left": "10"}, {"week": "this"}, {"key": "playwright"}]
import money as M  # noqa
extra = {"money_add": {"date": "2026-11-01", "kind": "expense", "amount": "1000", "memo": "試し", "item": "回線"},
         "money_recurring_add": {"item": "Suno", "amount": "10", "currency": "USD", "cycle": "monthly", "start": "2026-11-01"},
         "money_set_rate": {"rate": "150"}, "suno_retake": {"why": "サビが弱い"},
         "artist_page": {"page": "https://open.spotify.com/artist/0123456789abcdefghijkl"}, "fill_photo": {"service": "spotify"}, "photo_undo": {"service": "spotify"},
         "rename": {"title": "Renamed Song"}, "listen_ok": {"note": "ok"}, "remaster": {"style": "warm", "strength": "low"},
         "registered": {"date": "2026-11-20"}}
SKIP_RUN = {"fill_suno", "fill_distrokid", "fill_distrokid_all", "fill_lyrics", "fill_photo", "browser_login", "browser_parts", "browser_reset",
            "terminal", "open_folder", "open_artist", "self_update", "rerun_job", "retire", "retire_restore", "send_diag"}
cmds = {}
for a in sorted(names):
    got = None
    for c in cands:
        pp = {**c, **extra.get(a, {})}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                f = w.command_for(a, pp)
        except Exception as e:  # noqa
            got = ("EXC", pp, f"{type(e).__name__}: {e}"); break
        if f:
            got = (f, pp); break
    if got is None:
        cmds[a] = {"cmd": None}
    elif got[0] == "EXC":
        cmds[a] = {"exc": got[2], "p": got[1]}
    else:
        cmds[a] = {"cmd": [str(x) for x in got[0][0]], "label": got[0][1], "p": got[1]}
res["commands"] = cmds
# ターミナルの命令の文字列
res["terminal"] = {k: (v if isinstance(v, str) else " ".join(map(str, v))) for k, v in w.TERMINAL.items()}
if RUN:
    order = sorted(cmds, key=lambda a: (a in ("rename", "back", "retire", "remaster", "money_delete", "recover_ssd_apply", "daily_stop"), a))
    for a in order:
        c = cmds[a]
        if not c.get("cmd") or a in SKIP_RUN:
            continue
        t = time.time()
        code, r = post("/api/action", {"action": a, **c["p"]})
        if code != 200 or "job" not in r:
            res["actions"].append({"action": a, "post": code, "resp": r}); continue
        jid = r["job"]; st = {}
        while time.time() - t < 240:
            time.sleep(1.0)
            _, st = (lambda x: (x[0], json.loads(x[1]) if x[0] == 200 else {}))(get(f"/api/jobs/{jid}"))
            if st.get("state") != "running":
                break
        else:
            w.stop_job(jid) if hasattr(w, "stop_job") else None
            st["state"] = "timeout"
        res["actions"].append({"action": a, "p": c["p"], "state": st.get("state"), "summary": st.get("summary"), "sec": round(time.time() - t),
                               "tail": "\n".join((st.get("out") or "").splitlines()[-12:])})
        print(a, st.get("state"), round(time.time() - t), flush=True)
(OUTDIR / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
badget = [g for g in res["get"] if g["bad"]]
badact = [a for a in res["actions"] if a.get("state") not in ("done", None) or a.get("post", 200) != 200]
exc = {k: v for k, v in res["commands"].items() if v.get("exc")}
print(f"\n【結果】Python {sys.version.split()[0]}：ページ {len(res['get'])}（おかしい {len(badget)}）・サーバーのエラー {len(errors_srv)}・"
      f"受け手の無いボタン {len(res['unhandled_page_buttons'])}・命令の組み立てで落ちた {len(exc)}" + (f"・流して失敗 {len(badact)}" if RUN else "") + f"／詳しく：{OUTDIR / 'result.json'}")
for g in badget:
    print(f"   ✕ GET {g['path']}：{g['code']} {g['head'][:120]!r}")
for k in res["unhandled_page_buttons"]:
    print(f"   ✕ 受け手の無いボタン：{k}")
for k, v in exc.items():
    print(f"   ✕ {k}：{v['exc'][:140]}")
for a in badact:
    print(f"   ・{a['action']}：{a.get('state') or a.get('post')} {str(a.get('summary') or a.get('resp') or '')[:120]}")
srv.shutdown(); p.close()
