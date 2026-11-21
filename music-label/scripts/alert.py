#!/usr/bin/env python3
"""
スマホへの知らせと、Mac の見守り（決定 174）

  なぜ：この仕組みは Mac 1 台で動いている。Mac が止まる・眠る・仕事が失敗する、に気づけるのが Mac の画面だけだと遅れる。
  どうする：
    1 スマホへの知らせ（ntfy）… 仕事が失敗した・SSD が 1 日つながっていない、などをスマホに通知する。
         ntfy はアカウント無しで使える通知の仕組み。スマホに ntfy アプリを入れて、合言葉の「トピック名」を登録するだけ。
    2 Mac の見守り（healthchecks.io）… Mac が 10 分ごとに「生きています」と合図を送る。
         合図が止まると、healthchecks.io がメール（やスマホ）で知らせる。Mac が丸ごと止まっても気づける。
    3 見回り（watch）… 10 分ごとに、定期実行の失敗・SSD・R2 の予備保管・鍵の金庫・ディスクの空きを見て、
         新しい問題が出たときに 1 回だけ知らせ、直ったら「直りました」と知らせる。

使い方
  python3 scripts/alert.py watch     # 見回り 1 回（定期実行 watch から 10 分ごと）
  python3 scripts/alert.py test      # スマホに試しの知らせを送る
  python3 scripts/alert.py status    # いまの状態
  python3 scripts/alert.py hint      # Mac を眠らせない設定の説明

鍵（.env。setup_keys.py かダッシュボードの「鍵と見守り」で入れる）
  NTFY_TOPIC       … ntfy のトピック名（合言葉のようなもの。推測されにくい長い名前に。例：postdog-<でたらめな 16 文字>）
  NTFY_URL         … 自分で ntfy を立てているときだけ（ふだんは空。https://ntfy.sh を使う）
  NTFY_TOKEN       … ntfy の有料プランなどでトピックに鍵を掛けているときだけ
  HEALTHCHECK_URL  … healthchecks.io の「Ping URL」（https://hc-ping.com/<番号>）

記録：out/alert/（heartbeat.json・state.json・sent.json）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
from _common import OUT, ROOT, load_dotenv, read_json, ssl_context, write_json  # noqa: E402

load_dotenv()
DIR = OUT / "alert"
SENT = DIR / "sent.json"
STATE = DIR / "state.json"
HEART = DIR / "heartbeat.json"
DEDUPE_HOURS = 6             # 同じ知らせは、この時間は送り直さない
STALE_MIN = 30               # 合図がこの分数より古ければ「止まっているかも」
SSD_HOURS = 24               # SSD がこの時間つながっていなければ知らせる
R2_HOURS = 48                # 予備保管がこの時間より古ければ知らせる
DISK_WARN_GB, DISK_CRIT_GB = 5, 1
PRIORITY = {"info": 3, "warn": 4, "error": 5}
TAGS = {"info": ["musical_note"], "warn": ["warning"], "error": ["rotating_light"], "ok": ["white_check_mark"]}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _now() -> datetime:
    return datetime.now()


def _load(p: Path) -> dict:
    try:
        return read_json(p) if p.exists() else {}
    except (ValueError, OSError):
        return {}


def _post(url: str, data: bytes, headers: dict, timeout: int = 10) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context() if url.startswith("https") else None) as r:
            return r.status, r.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)[:200]


# ---------------------------------------------------------------------------
# スマホへの知らせ（ntfy）
def push(title: str, message: str, level: str = "info", url: str | None = None) -> tuple[bool, str]:
    """ntfy に 1 通送る：(送れたか, 説明)。日本語の題名が化けないように JSON で送る"""
    topic = _env("NTFY_TOPIC")
    if not topic:
        return False, "未設定"
    base = (_env("NTFY_URL") or "https://ntfy.sh").rstrip("/")
    body = {"topic": topic, "title": title[:200], "message": message[:3500] or title,
            "priority": PRIORITY.get(level, 3), "tags": TAGS.get(level, [])}
    if url:
        body["click"] = url
    hdr = {"Content-Type": "application/json"}
    if _env("NTFY_TOKEN"):
        hdr["Authorization"] = f"Bearer {_env('NTFY_TOKEN')}"
    code, txt = _post(base + "/", json.dumps(body, ensure_ascii=False).encode("utf-8"), hdr)
    if code == 200:
        return True, "送りました"
    hint = {401: "NTFY_TOKEN が違います", 403: "このトピックに送る権限がありません（トピック名か NTFY_TOKEN を確認）",
            429: "送りすぎです（少し待ってから）"}.get(code, "")
    return False, f"ntfy に送れませんでした（{code or txt}）{hint}"


def notify(title: str, message: str, level: str = "info", url: str | None = None, force: bool = False) -> bool:
    """Mac の通知と、スマホ（ntfy）への知らせ。同じ知らせは 6 時間は送り直さない。スマホに送れたら True"""
    key = hashlib.sha256(f"{title}\n{message}".encode()).hexdigest()[:16]
    sent = _load(SENT)
    now = _now()
    last = sent.get(key)
    if last and not force:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=DEDUPE_HOURS):
                return False
        except ValueError:
            pass
    try:
        _common.notify(title, message)   # これまでどおり Mac の通知センターにも
    except Exception:  # noqa: BLE001
        pass
    ok, _ = push(title, message, level, url)
    cut = (now - timedelta(days=3)).isoformat()
    sent = {k: v for k, v in sent.items() if str(v) >= cut}
    sent[key] = now.isoformat(timespec="seconds")
    DIR.mkdir(parents=True, exist_ok=True)
    write_json(SENT, sent)
    return ok


# ---------------------------------------------------------------------------
# Mac の見守り（healthchecks.io）
def heartbeat(ok: bool = True, body: str = "") -> bool:
    """「生きています」の合図（失敗のときは /fail に、最後のエラーの行を付けて）。送れたら True"""
    url = _env("HEALTHCHECK_URL").rstrip("/")
    sent = False
    if url:
        code, _ = _post(url + ("" if ok else "/fail"), body.encode("utf-8")[-9000:], {"Content-Type": "text/plain; charset=utf-8"})
        if code != 200:   # 1 回だけやり直す
            code, _ = _post(url + ("" if ok else "/fail"), body.encode("utf-8")[-9000:], {"Content-Type": "text/plain; charset=utf-8"})
        sent = code == 200
    DIR.mkdir(parents=True, exist_ok=True)
    write_json(HEART, {"at": _now().isoformat(timespec="seconds"), "ok": bool(ok), "sent": sent})
    return sent


# ---------------------------------------------------------------------------
# 見回り
def _job_problems() -> list[dict]:
    """定期実行の、最後の実行で失敗している仕事（schedule.py の記録から）"""
    out = []
    try:
        import schedule
    except Exception:  # noqa: BLE001
        return out
    for job, (desc, _) in schedule.JOBS.items():
        if job == "watch":   # この見回り自身は数えない
            continue
        try:
            steps, when = schedule.failed_steps(job)
        except Exception:  # noqa: BLE001
            continue
        if steps:
            names = "・".join(schedule.JOBS[job][1][i][0] for i in steps if i < len(schedule.JOBS[job][1]))
            out.append({"key": f"job:{job}", "level": "error", "title": f"自動の仕事が失敗しました：{desc[:40]}",
                        "detail": f"{job}（{names}）" + (f"・{when:%m/%d %H:%M}" if when else "") + "。ダッシュボードの「仕組みの詰まり」を見てください",
                        "tail": _log_tail(job)})
    return out


def _log_tail(job: str, lines: int = 12) -> str:
    f = OUT / "logs" / f"{job}.log"
    try:
        return "\n".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _ssd_problem(st: dict) -> dict | None:
    try:
        import _archive as archive
        base, _ = (archive._root() if hasattr(archive, "_root") else archive.root())
    except Exception:  # noqa: BLE001
        return None
    if base is not None:
        st.pop("ssd_missing_since", None)
        return None
    since = st.setdefault("ssd_missing_since", _now().isoformat(timespec="seconds"))
    try:
        hours = (_now() - datetime.fromisoformat(since)).total_seconds() / 3600
    except ValueError:
        hours = 0
    if hours < SSD_HOURS:
        return None
    return {"key": "ssd", "level": "warn", "title": "SSD が 1 日以上つながっていません",
            "detail": "SSD（DS72）をつなぐと、たまっている曲の保管と管理表の書き込みが自動で進みます"}


def _r2_problem() -> dict | None:
    if not (_env("R2_ACCOUNT_ID") and _env("R2_ACCESS_KEY_ID") and _env("R2_SECRET_ACCESS_KEY")):
        return None
    man = OUT / "backup" / "r2_manifest.json"
    if not man.exists():
        return {"key": "r2", "level": "warn", "title": "R2 への予備保管がまだ 1 回も済んでいません",
                "detail": "ダッシュボードの「保管する」か、python3 scripts/backup_r2.py"}
    hours = (_now().timestamp() - man.stat().st_mtime) / 3600
    if hours < R2_HOURS:
        return None
    return {"key": "r2", "level": "warn", "title": f"R2 への予備保管が {int(hours // 24)} 日止まっています",
            "detail": "毎日 3:30 の保管（backup）が動いているか、ダッシュボードの「仕組みの詰まり」を見てください"}


def _vault_problem() -> dict | None:
    try:
        import vault
        s = vault.status()
    except Exception:  # noqa: BLE001
        return None
    if not s["initialized"]:   # まだ金庫を作っていない：ダッシュボードには出すが、スマホには送らない
        return {"key": "vault_init", "level": "info", "push": False, "title": "鍵の金庫がまだ作られていません",
                "detail": "python3 scripts/vault.py init（Mac が壊れても鍵を戻せるようにする）"}
    if s["env_changed_since"] or not s["uploaded"]:
        return {"key": "vault", "level": "warn", "title": "鍵が変わったのに、金庫に預け直せていません",
                "detail": "ダッシュボードの「鍵と見守り」→「金庫に預ける」（R2 の鍵・ネットのつながりを確認）"}
    if not s["kit_saved"]:
        return {"key": "vault_kit", "level": "info", "push": False, "title": "復旧キットをまだ保存していません",
                "detail": "python3 scripts/vault.py kit → Apple の「パスワード」アプリにメモ"}
    return None


def _disk_problem() -> dict | None:
    try:
        free = shutil.disk_usage(ROOT).free / 1024 ** 3
    except OSError:
        return None
    if free >= DISK_WARN_GB:
        return None
    return {"key": "disk", "level": "error" if free < DISK_CRIT_GB else "warn", "title": f"Mac の空きが残り {free:.1f} GB です",
            "detail": "空きが無くなると、取り込み・音量調整・ジャケットづくりが止まります。いらないファイルを消してください"}


def problems(st: dict | None = None) -> list[dict]:
    st = st if st is not None else {}
    out = _job_problems()
    for p in (_ssd_problem(st), _r2_problem(), _vault_problem(), _disk_problem()):
        if p:
            out.append(p)
    return out


def watch() -> dict:
    """見回り 1 回：新しい問題を 1 回だけ知らせ、直ったら知らせる。合図（heartbeat）も送る"""
    load_dotenv(override=True)
    st = _load(STATE)
    found = problems(st)
    old = st.get("problems") or {}
    now = _now().isoformat(timespec="seconds")
    new = [p for p in found if p["key"] not in old]
    gone = [v for k, v in old.items() if k not in {p["key"] for p in found}]
    pushed = 0
    for p in new:
        if p.get("push", True):
            pushed += bool(notify(p["title"], p["detail"], p["level"]))
    cleared = [g for g in gone if g.get("push", True)]
    if cleared:
        notify("直りました", "・".join(g.get("title", "") for g in cleared), "ok")
    st["problems"] = {p["key"]: {"title": p["title"], "detail": p["detail"], "level": p["level"], "push": p.get("push", True),
                                 "since": (old.get(p["key"]) or {}).get("since", now)} for p in found}
    st["checked_at"] = now
    DIR.mkdir(parents=True, exist_ok=True)
    write_json(STATE, st)
    errors = [p for p in found if p["level"] == "error"]
    body = "\n\n".join(f"{p['title']}\n{p['detail']}\n{p.get('tail', '')}".strip() for p in errors)
    beat = heartbeat(ok=not errors, body=body)
    return {"problems": found, "new": len(new), "cleared": len(gone), "pushed": pushed, "heartbeat": beat}


def job_failed(job: str, desc: str, code: int) -> None:
    """定期実行が失敗した直後に呼ぶ（schedule.py から）。見回りを待たずにすぐ知らせ、見回りでは二重に送らない"""
    try:
        st = _load(STATE)
        probs = st.setdefault("problems", {})
        key = f"job:{job}"
        if key in probs:
            return
        title, detail = f"自動の仕事が失敗しました：{desc[:40]}", f"{job}（終了コード {code}）。ダッシュボードの「仕組みの詰まり」を見てください"
        probs[key] = {"title": title, "detail": detail, "level": "error", "push": True, "since": _now().isoformat(timespec="seconds")}
        DIR.mkdir(parents=True, exist_ok=True)
        write_json(STATE, st)
        notify(title, detail, "error")
    except Exception:  # noqa: BLE001  知らせの失敗で、元の仕事を止めない
        pass


def status() -> dict:
    """ダッシュボードに出す（通信しない）"""
    load_dotenv(override=True)
    hb, st = _load(HEART), _load(STATE)
    stale = True
    if hb.get("at"):
        try:
            stale = (_now() - datetime.fromisoformat(hb["at"])) > timedelta(minutes=STALE_MIN)
        except ValueError:
            pass
    return {"ntfy": bool(_env("NTFY_TOPIC")), "healthcheck": bool(_env("HEALTHCHECK_URL")),
            "last_heartbeat": hb.get("at", ""), "heartbeat_ok": bool(hb.get("ok")), "heartbeat_sent": bool(hb.get("sent")), "stale": stale,
            "checked_at": st.get("checked_at", ""),
            "problems": [{"key": k, **v} for k, v in (st.get("problems") or {}).items()]}


def check(service: str) -> tuple[str, str]:
    """「接続を確かめる」：実際に送ってみる（vault.check から）"""
    if service == "ntfy":
        if not _env("NTFY_TOPIC"):
            return "none", "未設定"
        ok, msg = push("Postdog Records：つながりました", "スマホへの知らせの確認です。これが届いていれば設定は合っています。", "info")
        return ("ok", "試しの知らせを送りました（スマホに届いたか見てください）") if ok else ("ng", msg)
    if service == "healthcheck":
        url = _env("HEALTHCHECK_URL")
        if not url:
            return "none", "未設定"
        if not url.startswith("https://"):
            return "ng", "HEALTHCHECK_URL は https:// で始まる Ping URL を入れてください"
        return ("ok", "合図を送れました") if heartbeat(True) else ("ng", "合図を送れませんでした（Ping URL を確認）")
    return "ng", "そのサービスはありません"


def keep_awake_hint() -> str:
    """Mac を眠らせない・停電のあと自動で立ち上げる設定の説明（ここでは実行しない。管理者のパスワードが要るため）"""
    return "\n".join([
        "Mac が眠ると定期実行が止まります。電源につないでいる間は眠らないようにします（画面だけ消えるのは問題ありません）。",
        "",
        "■ 画面から（おすすめ）",
        "  システム設定 → バッテリー（デスクトップは「エネルギー」）→ オプション：",
        "   ・「ディスプレイがオフのときに自動でスリープさせない」をオン（電源アダプタ接続中）",
        "   ・「停電後に自動的に起動」をオン（ある機種だけ）",
        "   ・「ネットワークアクセスによるスリープ解除」をオン",
        "",
        "■ ターミナルから（同じ設定。管理者のパスワードを聞かれます）",
        "  sudo pmset -c sleep 0          # 電源につないでいる間は眠らない",
        "  sudo pmset -c displaysleep 10  # 画面は 10 分で消す",
        "  sudo pmset -c autorestart 1    # 停電のあと自動で起動",
        "  pmset -g                       # いまの設定を見る",
        "",
        "■ ノート型でふたを閉じて使うとき",
        "  電源・外付けの画面・キーボードをつないでいれば、ふたを閉じても動き続けます（つないでいないと眠ります）。",
    ])


def main() -> None:
    ap = argparse.ArgumentParser(description="スマホへの知らせと Mac の見守り")
    ap.add_argument("command", choices=["watch", "test", "status", "hint"])
    a = ap.parse_args()
    if a.command == "hint":
        print(keep_awake_hint())
        return
    if a.command == "test":
        load_dotenv(override=True)
        ok, msg = push("Postdog Records：試しの知らせ", "ダッシュボードから送った試しの知らせです。届いていれば設定は合っています。", "info")
        hb = heartbeat(True) if _env("HEALTHCHECK_URL") else None
        print(f"   {'○' if ok else '✕'} スマホへの知らせ（ntfy）：{msg}" + ("。python3 scripts/setup_keys.py --only ntfy" if msg == "未設定" else ""))
        if hb is not None:
            print(f"   {'○' if hb else '✕'} Mac の見守り（healthchecks.io）：{'合図を送りました' if hb else '合図を送れませんでした'}")
        print(f"【結果】{'試しの知らせを送りました（スマホを見てください）' if ok else 'スマホへの知らせ：' + msg}")
        sys.exit(0 if ok else 2)
    if a.command == "status":
        s = status()
        print(f"   スマホへの知らせ：{'○' if s['ntfy'] else '－ 未設定'}　Mac の見守り：{'○' if s['healthcheck'] else '－ 未設定'}"
              f"　最後の合図：{s['last_heartbeat'].replace('T', ' ') or 'まだ'}")
        for p in s["problems"]:
            print(f"   ！ {p['title']}（{p.get('since', '')[:16].replace('T', ' ')} から）")
        print(f"【結果】気になること {len(s['problems'])} 件")
        return
    r = watch()
    for p in r["problems"]:
        print(f"   ！ {p['title']}：{p['detail']}")
    print(f"【結果】見回り：気になること {len(r['problems'])} 件（新しく {r['new']}・直った {r['cleared']}）"
          + ("・合図を送りました" if r["heartbeat"] else ""))


if __name__ == "__main__":
    main()
