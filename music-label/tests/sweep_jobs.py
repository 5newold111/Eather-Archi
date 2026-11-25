"""全部を試しに流す点検（1）：作り物のレーベルで、定期実行の全部の命令と各道具の主な命令・--help を流して、落ちる所を集める（決定 180）

使い方（music-label で）
  python3 tests/sweep_jobs.py [Python の場所] [結果の JSON]   # 省くと いまの Python・一時フォルダ
  最後に「落ちた命令」の一覧を出す。外には出ない（通信は止めた状態で流す）。リポジトリの out/ も変えない
スキル：.claude/skills/system-sweep/SKILL.md
"""
import json, os, re, subprocess, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from _fixture import Project, _DROP_ENV  # noqa: E402

import tempfile
PY = sys.argv[1] if len(sys.argv) > 1 else sys.executable
OUTF = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp()) / "sweep_jobs.json"
p = Project(ssd=True)
# 曲をいろいろな段階で
keys = {}
keys["brief"] = p.weekly("hollowplan", p.week(0), "Not Yet", stage="brief")
keys["imported"] = p.weekly("tidewool", p.week(-1), "Low Tide", stage="imported", days_ago=1)
keys["mastered"] = p.weekly("declan_fay", p.week(-1), "Mastered One", stage="mastered", days_ago=1)
keys["ready"] = p.weekly("hollowplan", p.week(-2), "Ready One", stage="ready", days_ago=2)
keys["registered"] = p.weekly("tidewool", p.week(-3), "Reg One", stage="registered", days_ago=3)
keys["live"] = p.weekly("declan_fay", p.week(-6), "Live One", stage="live", days_ago=30)
keys["debut"] = p.debut("declan_fay", "Side B", stage="live", days_ago=40)
try:
    keys["irregular"] = p.irregular("hollowplan", p.week(-1), "Irr One", stage="brief")
except Exception as e:
    print("irregular を作れませんでした", e)
p.drop("Some New Song.wav", "ssd_inbox", age=400)
p.drop("Unknown Thing.mp3", "dl_inbox", age=400)

env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
env.update(p.env())
env.update({"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": "", "PYTHONWARNINGS": "ignore"})
env.pop("PYTHONPATH", None)

sys.path.insert(0, str(p.scripts))
import importlib
sched = importlib.import_module("schedule")
cmds = []
for job, (_, steps) in sched.JOBS.items():
    for st in steps:
        cmds.append((f"job:{job}", st))
song = keys["ready"]
extra = [
 ["act_review.py", "list"], ["alert.py", "status"], ["alert.py", "hint"], ["alert.py", "watch"], ["artist_book.py", "status"], 
 ["artist_photo.py", "status"], ["cover_refs.py", "list"], ["daily.py", "status"], ["expand_label.py", "check"], ["explore.py", "report"],
 ["health.py", "check"], ["health.py", "report"], ["health.py", "heal", "--dry-run"], ["health.py", "heal", "--quiet"], ["health.py", "heal"],
 ["irregular.py", "list"], ["irregular.py", "artists"], ["mastering.py", "default", "hollowplan", "--style", "warm"], ["mastering.py", "learn", "--limit", "2", "--quiet"],
 
 ["money.py", "summary"], ["money.py", "monthly"], ["money.py", "export"],
 ["pitch.py", "todos"], ["pitch.py", "auto", "--quiet"], ["pitch.py", "build", song],
 ["plays.py", "status"], ["plays.py", "collect", "--quiet"], ["plays.py", "collect"],
 ["post_social.py", "plan", "--week", p.week(-2)], ["post_social.py", "daily", "--if-on"],
 ["publish.py", "status"], ["publish.py", "scan", "--quiet"], ["publish.py", "scan"], ["publish.py", "track", "--quiet"], ["publish.py", "track"],
 ["publish.py", "artist-pages", "hollowplan"], ["publish.py", "suno-request"],
 ["recover.py", "check", "--offline"], ["recover.py", "check", "--json", "--offline"], ["recover.py", "from-ssd"], ["recover.py", "cloud", "--dry-run"],
 ["retire.py", "list"], ["schedule.py", "list"], ["schedule.py", "status"], ["schedule.py", "cron"],
 ["sprint.py", "status"], ["suno_quota.py", "show"], ["suno_quota.py", "left", "10"], ["suno_quota.py", "plan", "--quiet"],
 ["supabase_sync.py", "all"], ["vault.py", "status"], ["vault.py", "backup", "--if-changed"],
 ["song_book.py"], ["dashboard.py"], ["distrokid_sheet.py", "--week", p.week(-2)], ["reference_db.py", "--show"], ["reference_db.py", "--audio-report"],
 ["reference_db.py", "--artists"], ["reference_db.py", "--audio-deep", "1"], ["reference_db.py", "--audio-melody", "0"],
 ["notion_sync.py", "--if-ready"], ["notion_sync.py"], ["analyze_growth.py", "--demo"], ["collect_metrics.py", "--analyze"],
 ["setup_keys.py", "--check"], ["health.py", "check", "--deep"] if False else ["health.py", "check"],
 ["listen.py", "need", song], ["listen.py", "ok", song], ["listen.py", "undo", song],
 ["publish.py", "rename", keys["brief"], "--title=Renamed Song"],
 ["publish.py", "back", keys["mastered"], "--to", "master"],
 ["run_week.py", "status", "--week", "this"], ["daily.py", "plan"], ["sprint.py", "plan"],
 ["lyrics_ja.py", "all"], ["store_lyrics.py", "--help"], ["check_names.py", "--help"],
 ["self_update.py"], ["backup_r2.py"], ["archive_sync.py"], ["restore_r2.py", "--dry-run"], ["send_diag.py", "--help"],
]
for c in extra:
    cmds.append(("tool", c))
for f in sorted(p.scripts.glob("*.py")):
    if f.name.startswith("_") or f.name in ("melody.py", "audio_tags.py", "set_key.py", "render_profile.py", "social_video.py", "store_check.py", "webapp.py"):
        continue
    cmds.append(("help", [f.name, "--help"]))

res = []
for kind, c in cmds:
    t0 = time.time()
    try:
        r = subprocess.run([PY, str(p.scripts / c[0]), *c[1:]], cwd=p.root, capture_output=True, text=True, timeout=180, env=env, stdin=subprocess.DEVNULL)
        code, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = "TIMEOUT", (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), ""
    tb = "Traceback (most recent call last)" in (out + err)
    last = [l for l in (out + "\n" + err).strip().splitlines() if l.strip()][-3:]
    res.append({"kind": kind, "cmd": " ".join(c), "code": code, "tb": tb, "sec": round(time.time() - t0, 1), "last": last,
                "tail": (out + "\n--stderr--\n" + err)[-2500:]})
    print(f"{'✕' if tb or code not in (0,) else '○'} {code} {round(time.time()-t0,1):5}s {kind} {' '.join(c)[:90]}", flush=True)
OUTF.write_text(json.dumps(res, ensure_ascii=False, indent=1))
p.close()
bad = [r for r in res if r["tb"] or r["code"] == "TIMEOUT"]
odd = [r for r in res if not r["tb"] and r["code"] not in (0, "TIMEOUT")]
print(f"\n【結果】{len(res)} 命令・落ちた（Traceback・時間切れ）{len(bad)}・終了コードが 0 以外 {len(odd)}（鍵が無い・意図した止まり方を含む）／詳しく：{OUTF}")
for r in bad:
    print(f"   ✕ {r['cmd'][:80]}：{(r['last'] or [''])[-1][:160]}")
for r in odd:
    print(f"   ・{r['code']} {r['cmd'][:80]}：{(r['last'] or [''])[-1][:120]}")
