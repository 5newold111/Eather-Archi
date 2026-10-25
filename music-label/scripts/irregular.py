#!/usr/bin/env python3
"""
不定期配信：デビュー曲が配信中の組を選んで、毎週の曲とは別に 1 曲ずつ出す（オーナーの指示 2026-10-25。決定 72）

流れ（ダッシュボードの「不定期配信」のタブから）：
  1 組を選ぶ（任意で場面とメモ）→「曲の案を作る」：参考曲の割り当て → Claude が曲名・イメージ（エピソード）・歌詞・Suno のスタイルを書く
  2 ポップアップで案を見る。曲名・イメージ・歌詞・スタイルなどは直して保存できる
  3 好みでなければ「却下して作り直す」（どこが好みでないかを任意で書く。却下した案は残し、次の案は同じ方向をくり返さない）
  4 よければ「この案で作曲へ進む」：毎週の曲と同じ置き場（out/briefs）に移し、Suno に自動で入力する
     → そのあとは毎週の曲と同じ流れ（取り込み → 音量・ジャケット・下書き → DistroKid → 配信）。配信日はいつもいちばん早い日

置き場所：
  案（作曲前）… out/irregular/drafts/<案 ID>.json（毎週の曲の置き場とは別。作曲前の案が楽曲管理シートや Suno の作曲待ちに出ないように）
  採用した曲 … out/briefs/<制作週>_<組>.json。制作週は、その組の曲が無い過去の週を使う（これから作る毎週の枠を使わないため）。
               中の印 irregular（不定期の曲）・sprint（配信日はいちばん早い日。集中配信と同じ扱い）

使い方（ふだんはダッシュボードのボタンから）：
  python3 scripts/irregular.py artists                          # 選べる組（デビュー曲が配信中）
  python3 scripts/irregular.py propose declan_fay --scene live --memo "雨の夜"
  python3 scripts/irregular.py propose declan_fay --redo <案 ID> --reason "もっと明るく"   # 却下して作り直す
  python3 scripts/irregular.py list
  python3 scripts/irregular.py accept <案 ID>                    # この案で作曲へ（Suno への入力はダッシュボードから）
  python3 scripts/irregular.py discard <案 ID>                   # この案をやめる
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, SCENE_TYPES, all_artists, load_artist, load_dotenv, monday_of, read_json  # noqa: E402

load_dotenv()
PY = sys.executable
S = ROOT / "scripts"
BRIEFS = OUT / "briefs"
DRAFTS = OUT / "irregular" / "drafts"
REFS = OUT / "references"
STATUS_JA = {"making": "作っています", "proposed": "確認待ち", "failed": "作れませんでした", "rejected": "却下",
             "accepted": "作曲へ進めた", "discarded": "やめた"}
# ポップアップで直せる欄：（欄の名前・ブリーフの中の場所）
EDITABLE = {"title": "曲名", "theme": "曲のテーマ", "story": "曲のイメージ（エピソード）", "lyrics": "歌詞（Suno に入れる）",
            "style": "Suno のスタイル（英語）", "exclude": "入れない要素（英語）", "cover": "ジャケットのイメージ（英語）"}
LIMITS = {"title": 120, "theme": 300, "story": 3000, "lyrics": 6000, "style": 1000, "exclude": 500, "cover": 1500}


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def _draft_path(draft_id: str) -> Path | None:
    if not re.fullmatch(r"[a-z0-9_]+-\d{14}", draft_id or ""):
        return None
    return DRAFTS / f"{draft_id}.json"


def load_draft(draft_id: str) -> dict | None:
    p = _draft_path(draft_id)
    return read_json(p) if p and p.exists() else None


def eligible() -> list[dict]:
    """選べる組：デビュー曲が配信中の組（オーナーの指示：配信中の組だけ）"""
    import _songs
    live = {s["slug"] for s in _songs.debut_songs() if _songs.state(s).get("status") == "live"}
    return [{"slug": a["slug"], "name": a.get("name", a["slug"])} for a in all_artists() if a.get("slug") in live]


def drafts() -> list[dict]:
    """案の一覧（新しい順）"""
    out = []
    for p in sorted(DRAFTS.glob("*.json")) if DRAFTS.exists() else []:
        try:
            out.append(read_json(p))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda d: (d.get("irregular") or {}).get("created_at", ""), reverse=True)


def _taken_weeks(slug: str) -> set[str]:
    """その組で使っている制作週（ブリーフ・取り込み・仕上げ・ジャケット・作業中の案）"""
    used = {p.name.split("_", 1)[0] for p in BRIEFS.glob(f"*_{slug}.json")} if BRIEFS.exists() else set()
    for base in (OUT / "takes", OUT / "masters"):
        used |= {p.name.split("_", 1)[0] for p in base.glob(f"*_{slug}")} if base.exists() else set()
    cov = OUT / "visuals" / slug / "cover"
    used |= {p.name for p in cov.iterdir()} if cov.exists() else set()
    for d in drafts():
        if d.get("artist_slug") == slug and (d.get("irregular") or {}).get("status") in ("making", "proposed", "accepted"):
            used.add(str(d.get("week_start")))
    return used


def free_week(slug: str) -> date:
    """不定期の曲に使う制作週：その組の曲が無い、いちばん近い過去の月曜（これから作る毎週の枠は使わない）"""
    used = _taken_weeks(slug)
    w = monday_of(date.today()) - timedelta(weeks=1)
    for _ in range(520):
        if w.isoformat() not in used:
            return w
        w -= timedelta(weeks=1)
    raise SystemExit("[停止] 使える制作週が見つかりませんでした")


def _title(d: dict) -> str:
    c = list(d.get("title_candidates") or [])
    return d.get("title") or (c[0] if c else "")


def summary(d: dict) -> dict:
    """ダッシュボードのポップアップに出すもの"""
    ir = d.get("irregular") or {}
    ep = d.get("episode") or {}
    a = load_artist(d["artist_slug"], d.get("label_slug"), missing_ok=True) or {}
    sc = d.get("scene") or {}
    return {"id": ir.get("id"), "slug": d.get("artist_slug"), "artist": a.get("name", d.get("artist_slug")),
            "status": ir.get("status"), "status_ja": STATUS_JA.get(ir.get("status"), ir.get("status")),
            "created_at": ir.get("created_at", ""), "round": ir.get("round", 1), "memo": ir.get("memo", ""),
            "scene": sc.get("name") or "", "scene_choice": ir.get("scene") or "", "error": ir.get("error", ""),
            "song_id": ir.get("song_id", ""), "reason": ir.get("reason", ""), "edited_at": ir.get("edited_at", ""),
            "instrumental": d.get("language") == "none" or d.get("lyrics_mode") == "instrumental",
            "title": _title(d), "candidates": list(d.get("title_candidates") or []),
            "theme": ep.get("theme", ""), "story": ep.get("story_ja", ""), "when_where": ep.get("when_where", ""),
            "realized": ep.get("realized", ""), "lyrics": d.get("suno_lyrics", ""), "style": d.get("suno_style_prompt", ""),
            "exclude": d.get("suno_exclude", "") or "", "cover": d.get("cover_prompt_seed", ""), "notes": d.get("notes_ja", ""),
            "bpm": d.get("bpm"), "history": ir.get("history", [])}


def _refs_count() -> int:
    return sum(1 for x in REFS.glob("*.json") if not x.name.endswith(".measure.json")) if REFS.exists() else 0


def _progress(k: int, n: int, text: str) -> None:
    print(f"【工程】{k}/{n}：{text}", flush=True)


def propose(slug: str, scene: str = "", memo: str = "", redo: str = "", reason: str = "") -> int:
    """曲の案を 1 つ作る（redo を渡すと、その案を却下して作り直す）"""
    import os
    if slug not in {a["slug"] for a in eligible()}:
        print(f"【結果】{slug} は選べません（デビュー曲が配信中の組だけ）")
        return 1
    a = load_artist(slug)
    history: list[dict] = []
    if redo:   # 前の案を却下にして、その案と理由を次の案に渡す（同じ方向をくり返さない）
        old = load_draft(redo)
        if not old or old.get("artist_slug") != slug:
            print("【結果】作り直す前の案が見つかりませんでした")
            return 1
        oi = old["irregular"]
        history = list(oi.get("history") or [])
        history.append({"title": _title(old), "theme": (old.get("episode") or {}).get("theme", ""),
                        "story": (old.get("episode") or {}).get("story_ja", "")[:400], "style": old.get("suno_style_prompt", "")[:400],
                        "reason": reason.strip(), "rejected_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
        scene = scene or oi.get("scene", "")
        memo = memo or oi.get("memo", "")
        if oi.get("status") in ("proposed", "failed"):
            oi.update({"status": "rejected", "reason": reason.strip(), "rejected_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
            _write(_draft_path(redo), old)
    if scene and scene not in SCENE_TYPES:
        scene = ""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    draft_id = f"{slug}-{stamp}"
    dp = _draft_path(draft_id)
    week = free_week(slug)
    meta = {"id": draft_id, "status": "making", "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "scene": scene,
            "memo": memo.strip(), "round": len(history) + 1, "history": history}
    _write(dp, {"artist_slug": slug, "label_slug": a.get("label_slug"), "week_start": week.isoformat(), "irregular": meta})
    print(f"【進み具合】{a.get('name', slug)} の曲の案（{meta['round']} 案目）")

    def fail(msg: str, code: int = 2) -> int:
        d = read_json(dp)
        d["irregular"].update({"status": "failed", "error": msg})
        _write(dp, d)
        print(f"【結果】{msg}")
        return code

    N = 4
    _progress(1, N, "SSD の参考データベースを読んでいます（SSD が無ければ前回の取り込みを使う）")
    if (S / "reference_db.py").exists():
        subprocess.run([PY, str(S / "reference_db.py"), "--quiet", "--audio-limit", "5"])   # 既存楽曲の新しい曲は 5 曲まで（全部はダッシュボードのボタンで）
    if _refs_count() < 11:
        return fail(f"参考曲の解析シートが {_refs_count()} 件しかありません（11 件以上必要）。SSD をつないでから、もう一度作ってください")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return fail("Claude の鍵（.env の ANTHROPIC_API_KEY）がありません。鍵を入れてから、もう一度作ってください")

    _progress(2, N, "参考曲を枠ごとに割り当てています" + (f"（場面：{SCENE_TYPES[scene]['name']}）" if scene else "（場面はおまかせ）"))
    out_p = BRIEFS / f"{week}_{slug}.json"   # 割り当ては毎週の置き場に出るので、すぐ案の置き場へ移す
    args = [PY, str(S / "select_references.py"), "--artist", slug, "--week", week.isoformat(), "--force",
            "--references", str(REFS), "--seed", stamp[-9:]]
    if scene:
        args += ["--scene", scene]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0 or not out_p.exists():
        print((r.stdout + r.stderr)[-800:])
        return fail("参考曲の割り当てができませんでした（下の記録を見てください）", 1)
    skel = read_json(out_p)
    out_p.unlink()
    skel["irregular"] = meta
    # オーナーの希望と、却下した案（Claude に渡す。write_brief.py の決まり 14）
    skel["owner_request"] = {"kind": "one-off release (irregular)", "memo_ja": meta["memo"],
                             "scene_choice": scene or "auto",
                             "rejected_ideas": [{k: h.get(k, "") for k in ("title", "theme", "story", "style", "reason")} for h in history]}
    _write(dp, skel)

    _progress(3, N, "Claude が曲名・イメージ・歌詞・Suno のスタイルを書いています（1〜2 分）")
    inst = (a.get("lyrics") or {}).get("mode") == "instrumental"
    r = subprocess.run([PY, str(S / "write_brief.py"), str(dp), "--references", str(REFS), *([] if inst else ["--lyrics-mode", "full"])],
                       capture_output=True, text=True)
    print("\n".join(ln for ln in r.stdout.splitlines() if ln.strip().startswith(("歌詞モード", "Claude", "タイトル候補", "[要確認]", "応答"))))
    d = read_json(dp)
    if r.returncode != 0 or not d.get("suno_style_prompt"):
        print((r.stdout + r.stderr)[-800:])
        return fail("Claude が案を書けませんでした（下の記録を見てください）。もう一度「作り直す」を押してください", 1)

    _progress(4, N, "案ができました。ポップアップで確かめてください")
    d["irregular"].update({"status": "proposed", "proposed_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    d["title"] = _title(d)
    _write(dp, d)
    print(f"【結果】{a.get('name', slug)} の曲の案ができました：「{d['title']}」（{meta['round']} 案目）。押すと中身を見て直せます")
    return 0


def _banned(d: dict, text: str) -> list[str]:
    """実在のアーティスト名など（Suno が断る・まねの心配）が入っていないか"""
    try:
        import write_brief
        a = load_artist(d["artist_slug"], d.get("label_slug"))
        low = text.lower()
        return sorted({t for t in write_brief.banned_terms(a) if len(t) >= 3 and t.lower() in low})
    except Exception:  # noqa: BLE001
        return []


def save(draft_id: str, fields: dict) -> tuple[bool, str]:
    """ポップアップで直した欄を保存する"""
    p = _draft_path(draft_id)
    if not p or not p.exists():
        return False, "その案が見つかりません"
    d = read_json(p)
    if (d.get("irregular") or {}).get("status") != "proposed":
        return False, "この案はもう直せません（作曲へ進めたか、却下した案です）"
    clean = {}
    for k, v in fields.items():
        if k in EDITABLE and isinstance(v, str):
            v = v.replace("\r\n", "\n").strip()
            if len(v) > LIMITS[k]:
                return False, f"{EDITABLE[k]}が長すぎます（{LIMITS[k]} 文字まで）"
            clean[k] = v
    if "title" in clean and not clean["title"]:
        return False, "曲名を入れてください"
    if "style" in clean and not clean["style"]:
        return False, "Suno のスタイルを入れてください"
    bad = _banned(d, " ".join(clean.get(k, "") for k in ("title", "lyrics", "style", "exclude", "cover")))
    if bad:
        return False, "実在のアーティスト名などが入っています（Suno が断るため消してください）：" + "・".join(bad)
    ep = d.setdefault("episode", {})
    put = {"title": lambda v: d.__setitem__("title", v), "theme": lambda v: ep.__setitem__("theme", v),
           "story": lambda v: ep.__setitem__("story_ja", v), "lyrics": lambda v: d.__setitem__("suno_lyrics", v),
           "style": lambda v: d.__setitem__("suno_style_prompt", v), "exclude": lambda v: d.__setitem__("suno_exclude", v),
           "cover": lambda v: d.__setitem__("cover_prompt_seed", v)}
    for k, v in clean.items():
        put[k](v)
    d["irregular"]["edited_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _write(p, d)
    return True, "保存しました"


def accept(draft_id: str) -> tuple[str | None, str]:
    """この案で作曲へ進む：毎週の曲と同じ置き場に移す。（曲 ID・説明）"""
    import sprint
    p = _draft_path(draft_id)
    if not p or not p.exists():
        return None, "その案が見つかりません"
    d = read_json(p)
    ir = d.get("irregular") or {}
    if ir.get("status") == "accepted" and ir.get("song_id"):
        return ir["song_id"], "もう作曲へ進めてあります"
    if ir.get("status") != "proposed":
        return None, "この案は作曲へ進められません（作っている途中か、却下した案です）"
    title = _title(d).strip()
    if not title or not d.get("suno_style_prompt"):
        return None, "曲名と Suno のスタイルを入れてから進めてください"
    slug = d["artist_slug"]
    week = d.get("week_start")
    if not week or (BRIEFS / f"{week}_{slug}.json").exists():   # 案を作ったあとに、その週がふさがった
        week = free_week(slug).isoformat()
    bp = BRIEFS / f"{week}_{slug}.json"
    today = date.today()
    b = json.loads(json.dumps(d))
    b["week_start"] = week
    b["title"] = title
    b["title_candidates"] = [title] + [c for c in (b.get("title_candidates") or []) if c != title]
    b["sprint"] = {"id": "irregular", "release": "asap", "marked_at": today.isoformat()}   # 配信日はいつもいちばん早い日（決定 53 と同じ扱い）
    b["release_at"] = sprint.release_at_asap(today + timedelta(days=5))
    b["irregular"] = {**ir, "status": "accepted", "accepted_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "draft": draft_id}
    BRIEFS.mkdir(parents=True, exist_ok=True)
    _write(bp, b)
    pm = p.with_suffix(".prompt.md")
    if pm.exists():
        shutil.copy2(pm, bp.with_suffix(".prompt.md"))
    ir.update({"status": "accepted", "accepted_at": b["irregular"]["accepted_at"], "song_id": bp.stem, "week_start": week})
    d["irregular"] = ir
    _write(p, d)
    return bp.stem, f"「{title}」を作曲へ進めました（曲 ID {bp.stem}）"


def discard(draft_id: str) -> tuple[bool, str]:
    p = _draft_path(draft_id)
    if not p or not p.exists():
        return False, "その案が見つかりません"
    d = read_json(p)
    if (d.get("irregular") or {}).get("status") not in ("proposed", "failed", "rejected"):
        return False, "この案はやめられません"
    d["irregular"].update({"status": "discarded", "discarded_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    _write(p, d)
    return True, "この案をやめました（記録は残します）"


def main() -> None:
    ap = argparse.ArgumentParser(description="不定期配信の曲の案を作る・直す・作曲へ進める")
    ap.add_argument("cmd", choices=["artists", "propose", "list", "accept", "discard"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--scene", default="", help="場面（live / party / personal / drive。空ならおまかせ）")
    ap.add_argument("--memo", default="", help="作りたい曲のメモ（任意）")
    ap.add_argument("--redo", default="", help="却下して作り直す前の案 ID")
    ap.add_argument("--reason", default="", help="好みでなかったところ（任意）")
    a = ap.parse_args()
    if a.cmd == "artists":
        for x in eligible():
            print(f"{x['slug']}\t{x['name']}")
        return
    if a.cmd == "list":
        for d in drafts():
            s = summary(d)
            print(f"{s['id']}\t{s['status_ja']}\t{s['artist']}\t「{s['title']}」\t{s['created_at']}")
        return
    if a.cmd == "propose":
        sys.exit(propose(a.arg, a.scene, a.memo[:1000], a.redo, a.reason[:1000]))
    fn = accept if a.cmd == "accept" else discard
    ok, msg = fn(a.arg)
    print(f"【結果】{msg}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
