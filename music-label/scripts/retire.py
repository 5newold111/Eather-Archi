#!/usr/bin/env python3
"""
所属アーティストを外す・元に戻す（オーナーの指示 2026-11-10。決定 166）

決めた流れ：
  ① ダッシュボードの「アーティスト追加」タブの「組を外す」→ 外したときに起きることを先に見せる（impact）
  ② 作っている途中の曲は、曲ごとに「出し切る」か「止める」を選ぶ
  ③ 確定（retire）：
     ・これからの制作を止める：毎週の枠（production_status）・不定期・毎日の持ち回り・SNS・コラボの相手・新人の比べ先から外す
       （設定書に retire の印を付けると、all_artists() から外れる）
     ・「止める」曲は、作業ファイルを「戻したもの」（out/publish/undo/）へ移す（消さない）
     ・管理表（アーティスト情報.xlsx）の『アーティスト』タブの状態を「活動終了」にする（行は消さない。pull で設定書が作り直されない）
     ・配信中の曲はそのまま残す（取り下げはしない。DistroKid の画面で Gon さんが決める）
     ・「出し切る」曲が無ければすぐ、あれば全部 DistroKid に登録し終えたときに、設定書を templates/archive/retired/ へ移す（tick）
     ・空いた枠に、新人の案を管理表に「提案」で入れる（expand_label.py propose。採用はオーナー）
  ④ 外してから 30 日は「復帰」で元に戻せる（restore）。止めた曲は「戻したもの」に残る（自動では戻さない）
  SSD の 02_アーティスト/<組>/ はそのまま残す

使い方（ふだんはダッシュボードから）：
  python3 scripts/retire.py impact <slug>                      # 外したときに起きること
  python3 scripts/retire.py retire <slug> --keep <曲 ID,…> [--reason …]   # 外す（--keep に無い途中の曲は止める）
  python3 scripts/retire.py restore <slug>                     # 元に戻す（外してから 30 日まで）
  python3 scripts/retire.py list                               # 外す途中・外した組
  python3 scripts/retire.py tick                               # 出し切る曲が全部登録できた組の手続きを終える（毎日・登録のあと自動）
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_artist, save_artist  # noqa: E402

PY = sys.executable
S = ROOT / "scripts"
ARTISTS = ROOT / "templates" / "artists"
RETIRED = ROOT / "templates" / "archive" / "retired"
UNDO = OUT / "publish" / "undo"
RESTORE_DAYS = 30
DONE = ("registered", "live")
BOOK_STATUS = "活動終了"


def _songs_of(slug: str) -> list[dict]:
    import _songs
    return [s for s in _songs.all_songs() if s.get("slug") == slug]


def _status_ja(st: str) -> str:
    import publish
    return publish.STATUS_JA.get(st or "", st or "")


def impact(slug: str) -> dict:
    """外したときに起きること（確認の画面に出す）"""
    import _songs
    a = load_artist(slug, missing_ok=True)
    if not a:
        return {"error": "その組が見つかりません"}
    released, doing = [], []
    for s in _songs_of(slug):
        st = _songs.state(s).get("status", "")
        row = {"id": s["id"], "title": s.get("title") or s.get("week"), "kind": s.get("kind"), "status": st, "status_ja": _status_ja(st)}
        (released if st in DONE else doing).append(row)
    drafts = []
    try:
        import irregular
        drafts = [d for d in irregular.drafts() if d.get("artist_slug") == slug and (d.get("irregular") or {}).get("status") in ("proposed", "making")]
    except Exception:  # noqa: BLE001
        pass
    daily = False
    try:
        import daily as _daily
        daily = slug in (_daily.settings().get("order") or [])
    except Exception:  # noqa: BLE001
        pass
    return {"slug": slug, "name": a.get("name", slug), "label": a.get("label_slug"), "retire": a.get("retire"),
            "released": released, "doing": doing, "drafts": len(drafts), "daily": daily,
            "stops": ["毎週の制作の枠", "不定期配信の候補", "毎日配信の持ち回り" + ("（いま入っています）" if daily else ""),
                      "SNS の投稿", "コラボの相手・参考の比べ先"]}


def _stash(paths: list[Path], key: str) -> Path | None:
    dest = UNDO / f"{key}_{datetime.now():%Y%m%d-%H%M%S}_活動終了"
    moved = False
    for p in paths:
        if p.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest / p.name))
            moved = True
    return dest if moved else None


def stop_song(song: dict) -> str:
    """作っている途中の曲を止める：作業ファイルを「戻したもの」へ移す（消さない）。デビュー曲は作業セットの中なので、止めた印だけ付ける"""
    import _songs
    import publish
    key = song["key"]
    paths = [OUT / "takes" / key, OUT / "masters" / key, publish.PUB / key]
    if song.get("kind") != "debut":
        paths += sorted((OUT / "briefs").glob(f"{key}.*"))
        paths.append(OUT / "visuals" / song["slug"] / "cover" / str(song.get("week")))
    dest = _stash([p for p in paths if p.exists()], key)
    st_p = _songs.STATE_DIR / f"{key}.json"
    if song.get("kind") == "debut":
        st = _songs.state(song)
        st.update({"status": "stopped", "stopped_at": date.today().isoformat(), "by": "活動終了"})
        _songs.save_state(song, st)
    elif st_p.exists():   # 進み具合の記録も「戻したもの」へ（ブリーフと同じ名前なので state_ を付ける）
        dest = dest or (UNDO / f"{key}_{datetime.now():%Y%m%d-%H%M%S}_活動終了")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(st_p), str(dest / f"state_{st_p.name}"))
    return f"{song.get('title') or song.get('week')}：止めました" + (f"（{dest.name} に移しました）" if dest else "")


def _book_status(slug: str, to: str) -> str:
    """管理表の『アーティスト』タブの状態を変える。SSD が無いなどで書けなければ、その理由"""
    try:
        import act_review
        n = act_review.set_status(slug, "acts", to, ("採用", "", "提案", "保留", BOOK_STATUS))
        return "" if n else "管理表にその組の行が見つかりませんでした"
    except SystemExit as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:120]}"


def retire(slug: str, keep: list[str], reason: str = "", propose: bool = True) -> int:
    a = load_artist(slug, missing_ok=True)
    if not a or not (ARTISTS / f"{slug}.json").exists():
        print("【結果】その組は所属していません（もう外したか、名前が違います）")
        return 1
    if a.get("retire"):
        print("【結果】その組は、もう外す手続きに入っています")
        return 1
    imp = impact(slug)
    keep = [k for k in keep if any(d["id"] == k for d in imp["doing"])]
    print(f"▶ {a.get('name', slug)} を外します" + (f"（理由：{reason}）" if reason else ""), flush=True)
    import _songs
    stopped = []
    for d in imp["doing"]:
        if d["id"] in keep:
            print(f"   ・{d['title']}：出し切ります（いま：{d['status_ja']}）")
            continue
        s = _songs.by_id(d["id"])
        if s:
            print(f"   ・{stop_song(s)}")
            stopped.append(d["id"])
    try:   # 不定期配信の、作曲へ進める前の案はやめる
        import irregular
        for dft in irregular.drafts():
            if dft.get("artist_slug") == slug and (dft.get("irregular") or {}).get("status") in ("proposed", "failed", "rejected"):
                irregular.discard(dft["irregular"]["id"])
    except Exception:  # noqa: BLE001
        pass
    try:   # 毎日配信の持ち回りから外す
        import daily as _daily
        st = _daily.settings()
        if slug in (st.get("order") or []):
            st["order"] = [x for x in st["order"] if x != slug]
            _daily.SETTINGS.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            print("   ・毎日配信の持ち回りから外しました")
    except Exception:  # noqa: BLE001
        pass
    today = date.today()
    a["retire"] = {"status": "retiring" if keep else "retired", "at": today.isoformat(), "reason": reason, "keep": keep,
                   "stopped": stopped, "restore_until": (today + timedelta(days=RESTORE_DAYS)).isoformat(),
                   "label_slug": a.get("label_slug")}
    why = _book_status(slug, BOOK_STATUS)
    if why:
        a["retire"]["book_pending"] = True
        print(f"   ⚠ 管理表を「{BOOK_STATUS}」にできませんでした（{why}）。SSD をつなぐと、次の毎日の点検で書き込みます")
    else:
        print(f"   ・管理表の状態を「{BOOK_STATUS}」にしました（行は残します）")
    save_artist(a)
    if not keep:
        _finalize(slug)
    else:
        print(f"   ・出し切る曲が {len(keep)} 曲あります。全部 DistroKid に登録し終えたら、手続きを終えます")
    if propose:
        label = a.get("label_slug") or "drive"
        print("▶ 空いた枠に、新人の案を管理表に入れます（採用するかは Gon さんが決めます。数分）", flush=True)
        r = subprocess.run([PY, str(S / "expand_label.py"), "propose", "--label", label], capture_output=True, text=True)
        tail = [x for x in (r.stdout + r.stderr).strip().splitlines() if x.strip()]
        print("   " + (tail[-1][:200] if tail else ("新人の案を入れました" if r.returncode == 0 else "新人の案を作れませんでした")))
    print(f"【結果】{a.get('name', slug)} を外しました。配信中の曲（{len(imp['released'])} 曲）はそのまま残ります。"
          f"{RESTORE_DAYS} 日間（{a['retire']['restore_until']} まで）は「復帰」で元に戻せます")
    return 0


def _finalize(slug: str) -> None:
    """設定書を templates/archive/retired/ へ移して、手続きを終える"""
    src = ARTISTS / f"{slug}.json"
    if not src.exists():
        return
    a = json.loads(src.read_text(encoding="utf-8"))
    a.setdefault("retire", {})["status"] = "retired"
    a["retire"]["finalized_at"] = date.today().isoformat()
    RETIRED.mkdir(parents=True, exist_ok=True)
    (RETIRED / f"{slug}.json").write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    src.unlink()
    print(f"   ・設定書を保管庫（templates/archive/retired/{slug}.json）へ移しました。SSD の 02_アーティスト/{a.get('name', slug)}/ はそのまま残します")


def tick() -> int:
    """出し切る曲が全部 DistroKid に登録できた組の手続きを終える。管理表に書けていなかった組は書き直す（毎日・登録のあと）"""
    import _songs
    for p in sorted(ARTISTS.glob("*.json")):
        a = json.loads(p.read_text(encoding="utf-8"))
        if a.get("book_pending_status") and not _book_status(a["slug"], a["book_pending_status"]):   # 元に戻したときに書けなかった管理表
            a.pop("book_pending_status", None)
            save_artist(a)
        r = a.get("retire") or {}
        if not r:
            continue
        if r.get("book_pending") and not _book_status(a["slug"], BOOK_STATUS):
            r.pop("book_pending", None)
            save_artist(a)
        left = [k for k in r.get("keep", []) if (s := _songs.by_id(k)) and _songs.state(s).get("status") not in DONE]
        if not left:
            print(f"▶ {a.get('name')} の出し切る曲が全部登録できたので、外す手続きを終えます")
            _finalize(a["slug"])
    for p in sorted(RETIRED.glob("*.json")) if RETIRED.exists() else []:
        a = json.loads(p.read_text(encoding="utf-8"))
        if (a.get("retire") or {}).get("book_pending") and not _book_status(a["slug"], BOOK_STATUS):
            a["retire"].pop("book_pending", None)
            p.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def restore(slug: str) -> int:
    src = RETIRED / f"{slug}.json"
    live = ARTISTS / f"{slug}.json"
    if src.exists():
        a = json.loads(src.read_text(encoding="utf-8"))
    elif live.exists():
        a = json.loads(live.read_text(encoding="utf-8"))
    else:
        print("【結果】外した組の中に見つかりません")
        return 1
    r = a.get("retire") or {}
    if not r:
        print("【結果】その組は外していません")
        return 1
    if r.get("restore_until", "") < date.today().isoformat():
        print(f"【結果】元に戻せる期間（{r.get('restore_until')} まで）を過ぎています。記録とファイルは残っています")
        return 2
    a.setdefault("retire_history", []).append({**r, "restored_at": date.today().isoformat()})
    a.pop("retire", None)
    why = _book_status(slug, "採用")
    if why:   # 管理表に書けなかった：毎日の点検で書き直す
        a["book_pending_status"] = "採用"
    ARTISTS.mkdir(parents=True, exist_ok=True)
    live.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if src.exists():
        src.unlink()
    print(f"▶ {a.get('name', slug)} を所属に戻しました（設定書を戻し、これからの制作を再開します）")
    print("   ・管理表の状態を「採用」に戻しました" if not why else f"   ⚠ 管理表を「採用」に戻せませんでした（{why}）。SSD をつなぐと、次の毎日の点検で書き込みます")
    if r.get("stopped"):
        print(f"   ・止めた曲（{len(r['stopped'])} 曲）は「戻したもの」（out/publish/undo/）に残っています（自動では戻しません）")
    print(f"【結果】{a.get('name', slug)} を元に戻しました。毎日配信の持ち回りに入れるときは、毎日配信の設定から入れてください")
    return 0


def listing() -> list[dict]:
    """外す途中の組・外した組（ダッシュボードに出す）"""
    today = date.today().isoformat()
    out = []
    for folder, where in ((ARTISTS, "retiring"), (RETIRED, "retired")):
        for p in sorted(folder.glob("*.json")) if folder.exists() else []:
            if p.stem.startswith("_"):
                continue
            a = json.loads(p.read_text(encoding="utf-8"))
            r = a.get("retire") or {}
            if not r:
                continue
            out.append({"slug": a["slug"], "name": a.get("name", a["slug"]), "status": r.get("status", where), "at": r.get("at", ""),
                        "reason": r.get("reason", ""), "keep": r.get("keep", []), "restore_until": r.get("restore_until", ""),
                        "can_restore": r.get("restore_until", "") >= today, "book_pending": bool(r.get("book_pending"))})
    return out


def active() -> list[dict]:
    """外せる組（いま所属している組）"""
    return [{"slug": a["slug"], "name": a.get("name", a["slug"])} for a in all_artists()
            if a.get("slug") and (ARTISTS / f"{a['slug']}.json").exists()]   # いまは Postdog Records の組だけ（子レーベルの組はレーベルの設定書の中）


def main() -> None:
    ap = argparse.ArgumentParser(description="所属アーティストを外す・元に戻す（決定 166）")
    ap.add_argument("cmd", choices=["impact", "retire", "restore", "list", "tick"])
    ap.add_argument("slug", nargs="?", default="")
    ap.add_argument("--keep", default="", help="出し切る途中の曲の ID（カンマ区切り）。書かなかった途中の曲は止める")
    ap.add_argument("--reason", default="")
    ap.add_argument("--no-propose", action="store_true", help="空いた枠に新人の案を入れない")
    a = ap.parse_args()
    if a.cmd == "impact":
        print(json.dumps(impact(a.slug), ensure_ascii=False, indent=1))
    elif a.cmd == "retire":
        sys.exit(retire(a.slug, [x.strip() for x in a.keep.split(",") if x.strip()], a.reason[:300], not a.no_propose))
    elif a.cmd == "restore":
        sys.exit(restore(a.slug))
    elif a.cmd == "list":
        print(json.dumps(listing(), ensure_ascii=False, indent=1))
    else:
        sys.exit(tick())


if __name__ == "__main__":
    main()
