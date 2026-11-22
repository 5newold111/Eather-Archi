#!/usr/bin/env python3
"""
毎日の自動点検と、安全なものだけ自動で直す（決定 173）

  なぜ：曲の進み具合の記録（out/publish/state）と、実際のファイル（テイク・音源・ジャケット・下書き・公開セット・DistroKid に渡す写し）が
        食い違うと、ダッシュボードは「進んでいる」と言うのに先へ進めない。これまで、そのたびに Gon さんが気づいて知らせていた。
  どうする：
    check()  … 食い違い・止まっている曲・取り込めていないファイル・失敗した定期実行などを一覧にする（何も変えない）
    heal()   … そのうち「安全に自動で直せるもの」だけを直し、記録して、もう一度点検する
    status() … ダッシュボードに出す要約（最後の点検の記録を読むだけ。速い）

  自動で直すもの（どれも Mac の中だけ・何度やっても同じ結果・消さない）：
    ・取り込んだのに進んでいない曲 → 取り込みの仕事（intake）をもう一度
    ・受け口に、曲名で見分けられるファイルが残っている → 同上
    ・選んだテイクのファイルが無いが、Suno の元データが曲のフォルダにある → 元データから写し直す
    ・「公開の準備ができた」のに配信用の音源が無い → 音量を整え直す（publish.py back --to master → 取り込みの仕事）
    ・DistroKid の下書きが無い → 作り直す　・公開セットが無い → 保管をやり直す（archive_sync.py --only <組>）
    ・DistroKid に渡す写し（out/distrokid_upload）が古い → 写し直す
    ・配信日を過ぎたのに配信中になっていない → 配信の確認（release）をもう一度
    ・失敗した定期実行（取り込み・配信の確認・保管・既存楽曲の解析・Notion・再生数）→ 失敗した手順だけ 1 日 1 回やり直す
    ・定期実行が Mac に登録されていない → 登録し直す（Mac だけ）
  自動で直さないもの（Gon さんに知らせる。ボタンの名前つき）：
    Suno・DistroKid・ブラウザに触ること、ジャケットを選ぶこと、聴いて確かめること、曲名を変えること、鍵を入れること。
    同じ直しは 1 日 1 回だけ試し、直らなければ「自動では直りませんでした」として知らせる側に回す。

  仕組みそのものの見守り（SSD・R2・鍵の金庫・ディスクの空き・スマホへの知らせ）は alert.py（決定 174）。ここは曲の流れの食い違いを見る。

使い方
  python3 scripts/health.py check [--json] [--deep]   # 点検だけ（何も変えない）
  python3 scripts/health.py heal [--quiet] [--deep]    # 点検 → 自動で直す → もう一度点検（定期実行 health から）
  python3 scripts/health.py report                     # 最後の点検の結果

記録：out/health/last.json（最後の点検）・log.jsonl（自動で直したことの記録）・state.json（今日試した直し）
"""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_dotenv, read_json, write_json  # noqa: E402

load_dotenv()
import _songs  # noqa: E402

PY = sys.executable
S = ROOT / "scripts"
DIR = OUT / "health"
LAST, LOG, STATE = DIR / "last.json", DIR / "log.jsonl", DIR / "state.json"
STALE_HOURS = 26                     # 最後の点検がこれより古ければ「点検が止まっている」
# どのくらい同じ段階にいたら知らせるか
WAIT_SUNO_DAYS, WAIT_COVER_DAYS, WAIT_LISTEN_DAYS, WAIT_REGISTER_DAYS, NOT_LIVE_DAYS = 7, 2, 1, 3, 3
IMPORT_STUCK_MIN = 30                # 取り込んでからこの分数たっても進んでいなければ、取り込みの仕事をもう一度
RERUN_JOBS = {"intake": "曲の取り込み", "release": "配信の確認", "backup": "保管", "refs": "既存楽曲の解析", "notion": "Notion に写す",
              "plays": "再生数を集める"}   # 失敗した手順をやり直してよい仕事（外に何かを出さない・何度やっても同じ）
AFTER_MASTER = ("waiting_cover", "ready")
IN_PROGRESS = ("imported", "waiting_ffmpeg", "waiting_cover", "ready")
SEV_ORDER = {"ng": 0, "warn": 1, "info": 2}


def _f(id_: str, sev: str, what: str, why: str = "", fix: str = "none", action: dict | None = None, heal: str = "", **args) -> dict:
    """見つけたこと 1 つ。fix：auto（自動で直す）・owner（Gon さんの番）・none（知らせるだけ）"""
    d = {"id": id_, "sev": sev, "what": what, "why": why, "fix": fix}
    if action:
        d["action"] = action
    if heal:
        d["heal"], d["args"] = heal, args
    return d


def _dt(x) -> datetime | None:
    try:
        return datetime.fromisoformat(str(x)) if x else None
    except ValueError:
        return None


def _d(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10]) if x else None
    except ValueError:
        return None


def _name(song: dict) -> str:
    return f"{song['artist']}「{song.get('title') or song.get('week')}」"


# ---------------------------------------------------------------------------
# 点検：曲ごと
def _take_file(song: dict) -> tuple[Path | None, dict]:
    tdir = OUT / "takes" / song["key"]
    sel = read_json(tdir / "selection.json") if (tdir / "selection.json").exists() else {}
    name = (sel.get("decision") or {}).get("selected")
    return (tdir / name if name else None), sel


def _set_files(song: dict) -> tuple[bool, str]:
    """公開セット（音源とジャケット）が、ローカル保存か SSD の曲のフォルダにそろっているか"""
    import _archive as archive
    import publish
    folder, audio, cover = publish.publish_folder(song)
    dirs = [folder]
    try:
        dirs += archive.song_folders(song["artist"], _songs.folder_name(song))
    except Exception:  # noqa: BLE001
        pass
    for d in dirs:
        if (d / audio).exists() and (not cover or (d / cover).exists()):
            return True, ""
    return False, f"{audio}" + (f"・{cover}" if cover else "")


def check_song(song: dict, now: datetime) -> list[dict]:
    import publish
    st = _songs.state(song)
    s, sid, nm = st.get("status", ""), song["id"], _name(song)
    out: list[dict] = []
    if s == "stopped":
        return out
    master = OUT / "masters" / song["key"] / "master.wav"
    take, sel = _take_file(song)

    if s and not song.get("title"):
        out.append(_f(f"title:{sid}", "warn", f"{song['artist']} の曲（{sid}）に曲名がありません", "ジャケットの文字と DistroKid の登録に曲名が要ります",
                      "owner"))
    # ---- 記録とファイルの食い違い
    if s in IN_PROGRESS and (take is None or not take.exists()):
        src = Path(str(st.get("source") or ""))
        if take is not None and src.is_file():
            out.append(_f(f"take:{sid}", "warn", f"{nm}：選んだテイクのファイルがありません", "Suno の元データが曲のフォルダにあるので、そこから写し直せます",
                          "auto", heal="retake_copy", key=song["key"]))
        else:
            out.append(_f(f"take:{sid}", "ng", f"{nm}：選んだテイクのファイルがありません",
                          "記録は「取り込み済み」ですが、取り込んだ音源が見つかりません。Suno で作曲に戻して、いいねした 1 本をもう一度ダウンロードしてください",
                          "owner", {"action": "back", "id": sid, "to": "suno", "label": "Suno で作曲に戻す"}))
        return out   # 先にテイクを直す（あとの点検は、テイクがある前提）
    if s in AFTER_MASTER and not master.exists():
        if s == "ready":
            out.append(_f(f"master:{sid}", "warn", f"{nm}：「公開の準備ができた」のに配信用の音源がありません", "音量を整え直します（取り込んだ曲はそのまま）",
                          "auto", heal="remaster", id=sid))
        else:
            out.append(_f(f"master:{sid}", "warn", f"{nm}：配信用の音源がありません", "取り込みの仕事で音量を整え直します", "auto", heal="intake"))
        return out
    if s == "ready":
        if _songs.cover_source(song) is None:
            out.append(_f(f"cover:{sid}", "ng", f"{nm}：「公開の準備ができた」のにジャケットがありません", "ジャケットを作り直してください",
                          "owner", {"action": "cover", "id": sid, "label": "ジャケットを作る"}))
            return out
        if not (publish.PUB / song["key"] / "DistroKid下書き.md").exists():
            out.append(_f(f"draft:{sid}", "warn", f"{nm}：DistroKid の下書きがありません", "作り直します", "auto", heal="draft", id=sid))
        ok, miss = _set_files(song)
        if not ok:
            out.append(_f(f"set:{sid}", "warn", f"{nm}：公開セット（{miss}）がそろっていません", "保管をやり直します", "auto", heal="store_set", slug=song["slug"]))
        else:
            stale = _stale_stage(song, master)
            if stale:
                out.append(_f(f"stage:{sid}", "warn", f"{nm}：DistroKid に渡す写しが古いままです（{stale}）", "いまの音源・ジャケットで写し直します",
                              "auto", heal="restage", id=sid))
    # ---- 同じ段階で止まっている
    since = _dt(st.get("imported_at"))
    if s in ("imported", "waiting_ffmpeg") and since and now - since > timedelta(minutes=IMPORT_STUCK_MIN):
        if s == "waiting_ffmpeg" and not shutil.which("ffmpeg"):
            out.append(_f(f"ffmpeg:{sid}", "ng", f"{nm}：音量を整える道具（ffmpeg）が無くて止まっています", "ターミナルで brew install ffmpeg を 1 回",
                          "owner", {"action": "terminal", "key": "ffmpeg", "label": "ffmpeg を入れる（ターミナルが開きます）"}))
        else:
            out.append(_f(f"stuck:{sid}", "warn", f"{nm}：取り込んでから {_ago(now - since)}、先へ進んでいません", "取り込みの仕事をもう一度動かします",
                          "auto", heal="intake"))
    if s == "waiting_cover" and since and now - since > timedelta(days=WAIT_COVER_DAYS) and master.exists():
        if song["kind"] != "debut" and publish.cover_candidates(song):
            out.append(_f(f"cover_pick:{sid}", "warn", f"{nm}：ジャケットの候補から 1 枚選ぶのを {_ago(now - since)} 待っています", "",
                          "owner", {"action": "cover", "id": sid, "label": "ジャケットを選ぶ"}))
        elif not os.environ.get("OPENAI_API_KEY"):
            out.append(_f(f"cover_key:{sid}", "ng", f"{nm}：ジャケットを作る OpenAI の鍵が無くて止まっています", "",
                          "owner", {"action": "terminal", "key": "openai", "label": "OpenAI の鍵を入れる（ターミナルが開きます）"}))
        else:
            out.append(_f(f"cover_wait:{sid}", "warn", f"{nm}：ジャケット待ちのまま {_ago(now - since)} たっています", "ジャケットづくりをもう一度試します（1 日 1 回）",
                          "auto", heal="intake"))
    if s == "ready" and not out:
        r_at = _dt(st.get("ready_at"))
        need_listen = False
        try:
            import listen
            need_listen = listen.needed(song)
        except Exception:  # noqa: BLE001  聴いて確かめる部品が無い版
            pass
        if need_listen and r_at and now - r_at > timedelta(days=WAIT_LISTEN_DAYS):
            out.append(_f(f"listen:{sid}", "warn", f"{nm}：仕上げた音を聴いて確かめるのを {_ago(now - r_at)} 待っています", "確かめるまで DistroKid には進みません（決定 169）",
                          "owner", {"url": f"/listen?id={sid}", "label": "聴いて確かめる"}))
        elif not need_listen and r_at and now - r_at > timedelta(days=WAIT_REGISTER_DAYS):
            out.append(_f(f"register:{sid}", "warn", f"{nm}：DistroKid の登録を {_ago(now - r_at)} 待っています", "",
                          "owner", {"action": "fill_distrokid", "id": sid, "label": "DistroKid に入力する（ブラウザが開きます）"}))
        rel = _d(song.get("release_at"))
        if rel and rel < now.date() and not song.get("sprint") and song["kind"] != "debut":
            out.append(_f(f"late:{sid}", "info", f"{nm}：予定の配信日（{rel}）を過ぎています", "登録するときは、選べるいちばん早い日になります（決定 165）"))
    if s == "" and song.get("style") and song.get("brief"):
        try:
            made = datetime.fromtimestamp(Path(song["brief"]).stat().st_mtime)
        except OSError:
            made = now
        start = max(made, datetime.combine(_d(song.get("week")) or made.date(), datetime.min.time()))
        if now - start > timedelta(days=WAIT_SUNO_DAYS) and not _postponed(song):
            out.append(_f(f"suno:{sid}", "warn", f"{nm}：Suno で作曲するのを {_ago(now - start)} 待っています", "",
                          "owner", {"action": "fill_suno", "id": sid, "label": "Suno に入力する（ブラウザが開きます）"}))
    if s == "registered":
        rel = _d(st.get("release_date"))
        if rel is None:
            out.append(_f(f"reldate:{sid}", "warn", f"{nm}：DistroKid 登録済みですが、配信日の記録がありません", "配信の確認が、いつから探せばよいか分かりません。"
                          "ダッシュボードで「← 戻す」→ もう一度「登録した」を押して、配信日を入れてください",
                          "owner", {"action": "back", "id": sid, "to": "ready", "label": "登録の準備に戻す（配信日を入れ直す）"}))
        elif (now.date() - rel).days > NOT_LIVE_DAYS:
            out.append(_f(f"notlive:{sid}", "warn", f"{nm}：配信日（{rel}）から {(now.date() - rel).days} 日たっても、配信中になっていません",
                          "配信の確認をもう一度動かします。それでも見つからなければ、DistroKid の My Music で状態を確かめてください",
                          "auto", {"action": "track", "label": "配信を確かめる"}, heal="release"))
    return out


def _ago(td: timedelta) -> str:
    h = td.total_seconds() / 3600
    return f"{int(h // 24)} 日" if h >= 48 else (f"{int(h)} 時間" if h >= 1 else f"{int(td.total_seconds() // 60)} 分")


def _postponed(song: dict) -> bool:
    """Suno の枠に合わせて後ろへずらした曲（決定 105）は、待っていて正しい"""
    try:
        return bool(read_json(Path(song["brief"])).get("release_postponed"))
    except Exception:  # noqa: BLE001
        return False


def _stale_stage(song: dict, master: Path) -> str:
    """out/distrokid_upload/<曲>/ の写しが、いまの音源・ジャケットと違う中身なら、そのファイル名"""
    import publish
    stage = OUT / "distrokid_upload" / song["key"]
    if not stage.is_dir():
        return ""
    _folder, audio, cover = publish.publish_folder(song)
    a = stage / audio
    if a.exists() and master.exists() and not filecmp.cmp(a, master, shallow=False):
        return audio
    c, src = (stage / cover if cover else None), _songs.cover_source(song)
    if c is not None and c.exists() and src is not None and not filecmp.cmp(c, src, shallow=False):
        return cover
    return ""


# ---------------------------------------------------------------------------
# 点検：全体
def check_duplicates(songs: list[dict]) -> list[dict]:
    """同じ組の同じ曲名が 2 つ以上（まだ配信していない曲）。ファイルをどちらの曲に取り込むか迷うもと（決定 154）"""
    import publish
    seen: dict[tuple, list[dict]] = {}
    for s in songs:
        if s.get("title") and _songs.state(s).get("status") not in publish.DONE:
            seen.setdefault((s["slug"], _songs.norm(s["title"])), []).append(s)
    out = []
    for (slug, _t), v in sorted(seen.items()):
        if len(v) > 1:
            v.sort(key=lambda x: x["week"])
            out.append(_f(f"dup:{slug}:{v[0]['id']}", "warn", f"{v[0]['artist']}「{v[0]['title']}」という曲名の曲が {len(v)} つあります（{'・'.join(x['id'] for x in v)}）",
                          f"あとの週の曲（{v[-1]['id']}）の曲名を別の名前に変えてください。同じままだと、ダウンロードした曲は早い週の方に入ります（決定 154）", "owner"))
    return out


def check_inbox(songs: list[dict], now: datetime) -> list[dict]:
    """受け口（SSD の楽曲情報・ダウンロード/SUNO楽曲・ダウンロードの一番上）に残っているファイル"""
    import publish
    try:
        files = publish.new_files()
    except Exception as e:  # noqa: BLE001
        return [_f("inbox:read", "warn", "受け口のフォルダを読めませんでした", str(e)[:160])]
    waiting = [s for s in songs if s.get("title") and _songs.state(s).get("status") not in publish.DONE]
    known, unknown = [], []
    for f in files:
        song, why = publish.match(f, waiting)
        if song is None:
            if publish.released_song(f) is not None:   # 配信済みの曲の音源：次の取り込みが記録だけして片付ける（決定 180）
                continue
            unknown.append((f, why))
        elif now.timestamp() - f.stat().st_mtime > IMPORT_STUCK_MIN * 60 and not _songs.state(song).get("manual_take"):
            known.append((f, song))
    out = []
    if known:
        out.append(_f("inbox:known", "warn", f"取り込めるファイルが受け口に {len(known)} 本残っています（{'・'.join(f.name for f, _ in known[:3])}）",
                      "取り込みの仕事をもう一度動かします", "auto", heal="intake"))
    for f, why in unknown[:8]:
        out.append(_f(f"inbox:{f.name}", "warn", f"どの曲か見分けられないファイルがあります：{f.name}",
                      f"{why}。どの曲か分かるなら、ターミナルで  python3 scripts/publish.py take \"{f}\" --song <曲 ID>", "owner"))
    return out


def check_orphans(songs: list[dict]) -> list[dict]:
    """曲が無くなったのに残っている進み具合の記録（ブリーフを消した・作り直した）"""
    keys = {s["key"] for s in songs}
    out = []
    if _songs.STATE_DIR.is_dir():
        for p in sorted(_songs.STATE_DIR.glob("*.json")):
            if p.stem not in keys and read_json(p).get("status") not in ("stopped", None, ""):
                out.append(_f(f"orphan:{p.stem}", "info", f"曲が見つからない進み具合の記録があります：{p.stem}", "ブリーフを作り直したか、消した曲の記録です。そのままで害はありません"))
    return out[:10]


def check_jobs() -> list[dict]:
    """定期実行の、最後の実行で失敗している仕事（知らせるのは alert.py。ここは、やり直してよい仕事を 1 日 1 回やり直す）"""
    try:
        import schedule
    except Exception:  # noqa: BLE001
        return []
    out = []
    for job, (desc, cmds) in schedule.JOBS.items():
        if job in ("health", "watch"):
            continue
        try:
            steps, when = schedule.failed_steps(job)
        except Exception:  # noqa: BLE001
            continue
        if not steps:
            continue
        names = "・".join(cmds[i][0] for i in steps if i < len(cmds))
        at = f"（{when:%m/%d %H:%M}）" if when else ""
        if job in RERUN_JOBS:
            out.append(_f(f"job:{job}", "warn", f"定期実行「{RERUN_JOBS[job]}」が失敗しています{at}：{names}", "失敗した手順だけ、もう一度動かします（1 日 1 回）",
                          "auto", {"action": "rerun_job", "job": job, "label": "いま動かし直す"}, heal="rerun", job=job, steps=steps))
        elif job in getattr(schedule, "RETRY_JOBS", {}):
            out.append(_f(f"job:{job}", "warn", f"定期実行「{desc[:30]}」が失敗しています{at}：{names}", "毎日 12:05 に、失敗した手順だけ自動でやり直します（決定 113）"))
        else:
            out.append(_f(f"job:{job}", "warn", f"定期実行「{desc[:30]}」が失敗しています{at}：{names}", f"ログ：out/logs/{job}.log", "owner"))
    return out


def check_launchd() -> list[dict]:
    """定期実行が Mac に登録されているか（Mac だけ）"""
    agents = Path.home() / "Library" / "LaunchAgents"
    if platform.system() != "Darwin" or not agents.is_dir():
        return []
    try:
        import schedule
    except Exception:  # noqa: BLE001
        return []
    prefix = getattr(schedule, "PREFIX", "jp.etherarchi.label")
    if not any(agents.glob(f"{prefix}.*.plist")):
        return [_f("launchd:none", "ng", "定期実行が Mac に 1 つも登録されていません", "ターミナルで  python3 scripts/schedule.py install", "owner")]
    missing = [j for j in [*schedule.JOBS, *schedule.SERVICES] if not (agents / f"{prefix}.{j}.plist").exists()]
    if not missing:
        return []
    return [_f("launchd:missing", "warn", f"Mac に登録されていない定期実行があります：{'・'.join(missing)}", "登録し直します", "auto",
               heal="install", jobs=[j for j in missing if j != "update"])]


def check_keys() -> list[dict]:
    """組で使う鍵が、片方だけ入っている（入れかけ）"""
    env = lambda k: os.environ.get(k, "").strip()  # noqa: E731
    out = []
    for name, keys, svc in (("Notion", ("NOTION_TOKEN", "NOTION_PARENT_PAGE"), "notion"),
                            ("Cloudflare R2", ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"), "r2"),
                            ("Spotify", ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"), "spotify")):
        have = [k for k in keys if env(k)]
        if have and len(have) < len(keys):
            lack = "・".join(k for k in keys if k not in have)
            out.append(_f(f"keys:{svc}", "warn", f"{name} の鍵が入れかけです（足りないもの：{lack}）", f"python3 scripts/setup_keys.py --only {svc}",
                          "owner", {"action": "terminal", "key": svc, "label": f"{name} の鍵を入れる（ターミナルが開きます）"}))
    return out


def check_quota() -> list[dict]:
    try:
        import suno_quota
        q = suno_quota.status()
    except Exception:  # noqa: BLE001
        return []
    if q.get("on") and q.get("left", 1) <= 0 and q.get("waiting", 0) > 0:
        return [_f("quota", "info", f"Suno の今月のダウンロード枠が残っていません（作曲待ち {q['waiting']} 曲）",
                   f"次のリセットは {q.get('reset')}。作曲待ちの曲は、次の月の枠に割り当てます（決定 105）")]
    return []


def check_audio_env() -> list[dict]:
    """メロディー起こし用の別の Python（~/.postdog-audio。決定 160）が、あるのに動かない（時間がかかるので --deep のときだけ）"""
    py = Path.home() / ".postdog-audio" / "bin" / "python3"
    if not py.exists():
        return []
    try:
        r = subprocess.run([str(py), "-c", "import basic_pitch.inference"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [_f("audio_env", "warn", "メロディー起こし用の部品を確かめられませんでした", str(e)[:120])]
    if r.returncode == 0:
        return []
    tail = (r.stderr.strip().splitlines() or [""])[-1][:160]
    return [_f("audio_env", "warn", "メロディー起こし用の部品（~/.postdog-audio）が壊れています", f"{tail}。入れ直し：~/.postdog-audio/bin/python3 -m pip install 'setuptools<81' basic-pitch",
               "owner")]


CHECKS = ["曲ごとの記録とファイル", "同じ曲名", "受け口のファイル", "行き先の無い記録", "定期実行の失敗", "定期実行の登録", "鍵の入れかけ", "Suno の枠"]


def check(deep: bool = False) -> list[dict]:
    """何も変えずに、見つけたことの一覧を返す（重い順ではなく、深刻な順）"""
    load_dotenv(override=True)
    now = datetime.now()
    _songs.freeze()
    try:
        songs = [s for s in _songs.all_songs() if not str(s.get("week", "")).startswith("2000-")]
        found: list[dict] = []
        for s in songs:
            try:
                found += [dict(f, group=CHECKS[0]) for f in check_song(s, now)]
            except Exception as e:  # noqa: BLE001  1 曲の点検の失敗で、ほかの曲の点検を止めない
                found.append(_f(f"check:{s['id']}", "info", f"{_name(s)} を点検できませんでした", f"{type(e).__name__}: {str(e)[:120]}") | {"group": CHECKS[0]})
        for group, fn in ((CHECKS[1], lambda: check_duplicates(songs)), (CHECKS[2], lambda: check_inbox(songs, now)), (CHECKS[3], lambda: check_orphans(songs)),
                          (CHECKS[4], check_jobs), (CHECKS[5], check_launchd), (CHECKS[6], check_keys), (CHECKS[7], check_quota)):
            try:
                found += [dict(f, group=group) for f in fn()]
            except Exception as e:  # noqa: BLE001
                found.append(_f(f"check:{group}", "info", f"「{group}」を点検できませんでした", f"{type(e).__name__}: {str(e)[:120]}") | {"group": group})
        if deep:
            found += [dict(f, group="メロディー起こしの部品") for f in check_audio_env()]
    finally:
        _songs.thaw()
    found.sort(key=lambda f: (SEV_ORDER.get(f["sev"], 9), {"owner": 0, "auto": 1, "none": 2}.get(f["fix"], 3), f["id"]))
    return found


# ---------------------------------------------------------------------------
# 自動の直し（どれも Mac の中だけ・何度やっても同じ・消さない）
def _run(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "PYTHONUNBUFFERED": "1", "PATH": os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"})
    return r.returncode, (r.stdout + r.stderr)[-1500:]


def fix_intake(**_) -> tuple[bool, str]:
    """取り込みの仕事（定期実行 intake と同じ鍵を掛けて動かすので、動いている最中なら飛ばされる）"""
    code, out = _run([PY, str(S / "schedule.py"), "run", "intake"])
    return code == 0, "取り込みの仕事を動かしました" + ("" if code == 0 else f"（終了コード {code}）")


def fix_release(**_) -> tuple[bool, str]:
    code, out = _run([PY, str(S / "schedule.py"), "run", "release"])
    return code == 0, "配信の確認を動かしました" + ("" if code == 0 else f"（終了コード {code}）")


def fix_retake_copy(key: str, **_) -> tuple[bool, str]:
    """選んだテイクを、曲のフォルダの Suno の元データから写し直す"""
    song = _songs.by_key(key)
    st = _songs.state(song) if song else {}
    src = Path(str(st.get("source") or ""))
    take, _sel = _take_file(song) if song else (None, {})
    if not song or take is None or not src.is_file():
        return False, "元データが見つかりません"
    take.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, take)
    return True, f"{src.name} から {take.name} を写し直しました"


def fix_remaster(id: str, **_) -> tuple[bool, str]:  # noqa: A002
    """整えた音源と下書きを「戻したもの」へ移して、取り込み済みに戻す（このあと heal が取り込みの仕事を 1 回動かして、音量からやり直す）"""
    code, out = _run([PY, str(S / "publish.py"), "back", id, "--to", "master"])
    if code != 0:
        return False, f"音量を整え直す準備ができませんでした：{out.strip().splitlines()[-1][:160] if out.strip() else code}"
    return True, "音量を整え直します（取り込みの仕事で、音量からやり直します）"


def fix_draft(id: str, **_) -> tuple[bool, str]:  # noqa: A002
    import publish
    song = _songs.by_id(id)
    if not song:
        return False, "曲が見つかりません"
    publish.write_draft(song, _songs.state(song))
    return (publish.PUB / song["key"] / "DistroKid下書き.md").exists(), "DistroKid の下書きを作り直しました"


def fix_store_set(slug: str, **_) -> tuple[bool, str]:
    code, out = _run([PY, str(S / "archive_sync.py"), "--only", slug])
    return code == 0, "公開セットを作り直しました（保管をやり直しました）" + ("" if code == 0 else f"（保管の終了コード {code}。ほかの組・管理表の書き込みで警告あり）")


def fix_restage(id: str, **_) -> tuple[bool, str]:  # noqa: A002
    """DistroKid に渡す写しを、いまの音源・ジャケットで作り直す（公開セットを新しくしてから写す）"""
    import contextlib
    import io
    song = _songs.by_id(id)
    if not song:
        return False, "曲が見つかりません"
    _run([PY, str(S / "archive_sync.py"), "--only", song["slug"]])
    import browser_fill
    with contextlib.redirect_stdout(io.StringIO()):
        audio, cover, how = browser_fill.upload_files(song)
    master = OUT / "masters" / song["key"] / "master.wav"
    if audio is not None and master.exists() and not filecmp.cmp(audio, master, shallow=False):   # 公開セットが古いまま：音源そのものから写す
        with contextlib.redirect_stdout(io.StringIO()):
            audio = browser_fill._upload_copy(song, master, audio.name)
    src = _songs.cover_source(song)
    if cover is not None and src is not None and not filecmp.cmp(cover, src, shallow=False):
        cover = browser_fill._upload_copy(song, src, cover.name)
    return audio is not None and not _stale_stage(song, master), "DistroKid に渡す写しを作り直しました"


def fix_rerun(job: str, steps: list[int], **_) -> tuple[bool, str]:
    import schedule
    names = "・".join(schedule.JOBS[job][1][i][0] for i in steps if i < len(schedule.JOBS[job][1]))
    code = schedule.run_job(job, only=list(steps), title=f"自動の点検のやり直し（{names}）")
    return code == 0, f"定期実行「{RERUN_JOBS.get(job, job)}」の失敗した手順（{names}）をやり直しました" + ("" if code == 0 else "が、まだ失敗しています")


def fix_install(jobs: list[str], **_) -> tuple[bool, str]:
    if not jobs:
        return True, ""
    code, out = _run([PY, str(S / "schedule.py"), "install", "--only", ",".join(jobs)], timeout=600)
    return code == 0 and "✕" not in out, f"定期実行を登録し直しました：{'・'.join(jobs)}"


FIXERS = {"intake": fix_intake, "release": fix_release, "retake_copy": fix_retake_copy, "remaster": fix_remaster, "draft": fix_draft,
          "store_set": fix_store_set, "restage": fix_restage, "rerun": fix_rerun, "install": fix_install}
ONCE_PER_RUN = {"intake", "release"}   # 何曲ぶんあっても 1 回動かせば足りる直し
NEEDS_INTAKE = {"remaster"}            # 直したあとに、取り込みの仕事を（まだなら）1 回動かす直し


def _log(entry: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **entry}, ensure_ascii=False) + "\n")


def _fixed_today() -> list[dict]:
    if not LOG.exists():
        return []
    today, out = date.today().isoformat(), []
    for ln in LOG.read_text(encoding="utf-8").splitlines()[-400:]:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if e.get("at", "").startswith(today) and e.get("ok") and not e.get("dry"):
            out.append(e)
    return out


def heal(deep: bool = False, dry: bool = False, quiet: bool = False) -> dict:
    """点検 → 自動で直す（同じ直しは 1 日 1 回）→ もう一度点検 → 記録。戻り値は status() と同じ形"""
    say = (lambda *a, **k: None) if quiet else print
    today = date.today().isoformat()
    state = read_json(STATE) if STATE.exists() else {}
    tried = {k: v for k, v in (state.get("tried") or {}).items() if v == today}
    found = check(deep)
    todo = [f for f in found if f["fix"] == "auto" and f.get("heal") in FIXERS and f["id"] not in tried]
    todo.sort(key=lambda f: {"retake_copy": 0, "remaster": 1, "draft": 2, "store_set": 3, "restage": 4}.get(f["heal"], 5))   # ファイルを直してから、仕事を動かす
    done_kinds: dict[str, tuple[bool, str]] = {}
    tries: list[tuple[dict, bool, str]] = []
    for f in todo:
        kind = f["heal"]
        if dry:
            say(f"   （試し）直すこと：{f['what']} → {kind}")
            continue
        if kind in ONCE_PER_RUN and kind in done_kinds:
            ok, msg = done_kinds[kind]
        else:
            say(f"▶ {f['what']}", flush=True)
            try:
                ok, msg = FIXERS[kind](**(f.get("args") or {}))
            except Exception as e:  # noqa: BLE001  1 つの直しの失敗で、ほかを止めない
                ok, msg = False, f"{type(e).__name__}: {str(e)[:160]}"
            done_kinds[kind] = (ok, msg)
            say(f"   {'○' if ok else '✕'} {msg}")
        tried[f["id"]] = today
        tries.append((f, bool(ok), msg))
    if not dry and any(f["heal"] in NEEDS_INTAKE for f in todo) and "intake" not in done_kinds:   # 音量を整え直す曲がある：取り込みの仕事を 1 回
        ok, msg = fix_intake()
        say(f"   {'○' if ok else '✕'} {msg}")
    if not dry:
        DIR.mkdir(parents=True, exist_ok=True)
        write_json(STATE, {**state, "tried": tried})
    after = check(deep) if (todo and not dry) else found
    after_ids = {f["id"] for f in after}
    for f, ok, msg in tries:   # 直ったかどうかは、もう一度点検して決める（命令が警告つきで終わっても、直っていれば「直った」）
        _log({"id": f["id"], "what": f["what"], "did": msg, "ok": f["id"] not in after_ids, "ran_ok": ok})
    before_ids = {f["id"] for f in found}
    fixed_now = [f for f in todo if f["id"] not in after_ids] if not dry else []
    for f in after:   # 今日もう試して直らなかったもの：Gon さんに回す
        if f["fix"] == "auto" and f["id"] in tried and not dry:
            f["fix"], f["why"] = "owner", (f.get("why", "") + "（自動では直りませんでした）").strip()
    summary = _summary(after)
    summary["fixed_now"] = [f["what"] for f in fixed_now]
    if not dry:
        prev = read_json(LAST) if LAST.exists() else {}
        write_json(LAST, summary)
        _tell_new(prev, summary)
    n_owner = len(summary["needs_owner"])
    print(f"【結果】点検しました：自動で直した {len(fixed_now)} 件・あなたの番 {n_owner} 件・知らせるだけ {summary['info']} 件"
          + ("" if before_ids else "（食い違いはありません）"))
    return summary


def _summary(found: list[dict]) -> dict:
    groups = {f.get("group") for f in found}
    pub = lambda f: {k: v for k, v in f.items() if k not in ("heal", "args")}  # noqa: E731
    return {"checked_at": datetime.now().isoformat(timespec="seconds"), "ok": len([g for g in CHECKS if g not in groups]), "checks": len(CHECKS),
            "auto_fixed_today": [{"at": e["at"][11:16], "what": e["what"], "did": e.get("did", "")} for e in _fixed_today()],
            "needs_owner": [pub(f) for f in found if f["fix"] == "owner"],
            "auto_pending": [pub(f) for f in found if f["fix"] == "auto"],
            "notes": [pub(f) for f in found if f["fix"] == "none"],
            "info": len([f for f in found if f["fix"] == "none"]),
            "worst": min((f["sev"] for f in found), key=lambda s: SEV_ORDER.get(s, 9), default="ok")}


def _tell_new(prev: dict, cur: dict) -> None:
    """新しく「あなたの番」になった深刻なもの（ng）を、1 回だけ知らせる（スマホへの知らせがあればそちらにも）"""
    old = {f["id"] for f in prev.get("needs_owner", [])}
    new = [f for f in cur["needs_owner"] if f["id"] not in old and f["sev"] == "ng"]
    if not new:
        return
    title, body = "曲の流れが止まっています", "・".join(f["what"] for f in new[:3]) + (f" ほか {len(new) - 3} 件" if len(new) > 3 else "")
    try:
        import alert
        alert.notify(title, body, "warn")
        return
    except Exception:  # noqa: BLE001  スマホへの知らせが無い版・使えないとき
        pass
    try:
        from _common import notify
        notify(title, body)
    except Exception:  # noqa: BLE001
        pass


def status() -> dict:
    """ダッシュボードに出す（最後の点検の記録を読むだけ）：{"ok": n, "auto_fixed_today": […], "needs_owner": […], …}"""
    if not LAST.exists():
        return {"checked_at": "", "stale": True, "ok": 0, "checks": len(CHECKS), "auto_fixed_today": [], "needs_owner": [], "auto_pending": [],
                "notes": [], "info": 0, "worst": "ok"}
    d = read_json(LAST)
    at = _dt(d.get("checked_at"))
    d["stale"] = at is None or datetime.now() - at > timedelta(hours=STALE_HOURS)
    d["auto_fixed_today"] = [{"at": e["at"][11:16], "what": e["what"], "did": e.get("did", "")} for e in _fixed_today()]
    return d


def report() -> None:
    s = status()
    if not s["checked_at"]:
        print("【結果】まだ点検していません（python3 scripts/health.py heal）")
        return
    print(f"■ 最後の点検：{s['checked_at'].replace('T', ' ')}" + ("（1 日以上前。定期実行 health が動いているか確かめてください）" if s["stale"] else ""))
    print(f"   問題なし {s['ok']}／{s['checks']} 種類・今日自動で直した {len(s['auto_fixed_today'])} 件")
    for e in s["auto_fixed_today"]:
        print(f"   ○ {e['at']} {e['did'] or e['what']}")
    for title, key, mark in (("あなたの番", "needs_owner", "！"), ("次の点検で自動で直すもの", "auto_pending", "…"), ("知らせ", "notes", "・")):
        if s.get(key):
            print(f"■ {title}")
            for f in s[key]:
                btn = f"　→ {f['action']['label']}" if (f.get("action") or {}).get("label") else ""
                print(f"   {mark} {f['what']}" + (f"（{f['why']}）" if f.get("why") else "") + btn)
    print(f"【結果】あなたの番 {len(s['needs_owner'])} 件・自動で直す予定 {len(s['auto_pending'])} 件・知らせ {s['info']} 件")


def main() -> None:
    ap = argparse.ArgumentParser(description="毎日の自動点検と、安全なものだけ自動で直す（決定 173）")
    ap.add_argument("cmd", choices=["check", "heal", "report"])
    ap.add_argument("--quiet", action="store_true", help="途中の様子を出さない（定期実行）")
    ap.add_argument("--deep", action="store_true", help="時間のかかる点検もする（メロディー起こし用の部品）")
    ap.add_argument("--dry-run", action="store_true", help="heal：何を直すかだけ表示")
    ap.add_argument("--json", action="store_true", help="check：JSON で出す")
    a = ap.parse_args()
    if a.cmd == "check":
        found = check(a.deep)
        if a.json:
            print(json.dumps(found, ensure_ascii=False, indent=1, default=str))
            return
        mark = {"ng": "！", "warn": "△", "info": "・"}
        who = {"auto": "自動で直す", "owner": "あなたの番", "none": "知らせ"}
        for f in found:
            print(f"{mark.get(f['sev'], '・')} [{who[f['fix']]}] {f['what']}" + (f"\n     {f['why']}" if f.get("why") else ""))
        print(f"【結果】見つけたこと {len(found)} 件（自動で直せる {sum(f['fix'] == 'auto' for f in found)}・あなたの番 {sum(f['fix'] == 'owner' for f in found)}）"
              if found else "【結果】食い違いはありません")
    elif a.cmd == "heal":
        heal(a.deep, a.dry_run, a.quiet)
    else:
        report()


if __name__ == "__main__":
    main()
