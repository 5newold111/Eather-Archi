"""
曲の一覧（デビュー曲と毎週の曲）をまとめて扱う部品

1 曲ごとに次の情報を持つ（dict）：
  id        楽曲管理シートの曲 ID（デビュー曲は PDR-001〜、毎週の曲は <週>_<組>）
  kind      "debut" / "weekly"
  key       out/takes/<key>/・out/masters/<key>/ のフォルダ名（<週か作業セットの日付>_<組>）
  slug・label_slug・artist（アーティスト名）・title（曲名。未定なら ""）・alts（別案）
  week      制作週（デビュー曲は作業セットの日付）
  release_at 配信日時（毎週の曲。デビュー曲は「準備ができたらすぐ」なので None）
  style・lyrics・exclude・genre（DistroKid のジャンル）・language・instrumental
  brief / kit 元のファイル

公開の流れ（publish.py）・保管（archive_sync.py）・ジャケット（kit_images.py・finalize_cover.py）・
楽曲管理シート（song_book.py）が、同じ曲名・同じフォルダ名を使うためにここを通す。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from _common import OUT, ROOT, load_artist, read_json

KIT_ROOT = ROOT / "debut_kit"
STATE_DIR = OUT / "publish" / "state"   # publish.py が書く曲ごとの進み具合（取り込み・登録・配信）


def norm(s: object) -> str:
    """見比べるための形（全角半角・大文字小文字・記号の揺れをそろえる）"""
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    s = re.sub(r"[’'`\"“”]", "", s)
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def safe(name: str) -> str:
    """ファイル名・フォルダ名に使えない文字を置き換える（Mac の Finder は「:」「/」を使えない）"""
    return re.sub(r"\s+", " ", str(name).replace("/", "／").replace(":", "：")).strip() or "（名前なし）"


def debut_songs() -> list[dict]:
    out = []
    for kit_json in sorted(KIT_ROOT.glob("*/kit.json")):
        kit = read_json(kit_json)
        for i, s in enumerate(kit.get("songs", []), 1):
            a = load_artist(s["slug"], None, missing_ok=True) or {}
            out.append({
                "id": f"PDR-{i:03d}", "kind": "debut", "key": f"{kit_json.parent.name}_{s['slug']}",
                "slug": s["slug"], "label_slug": None, "artist": a.get("name") or s.get("artist") or s["slug"],
                "title": s.get("title", ""), "alts": list(s.get("title_alt") or []), "week": kit_json.parent.name,
                "release_at": None, "style": s.get("style", ""), "lyrics": s.get("lyrics", ""),
                "exclude": s.get("exclude", ""), "genre": list(s.get("genre") or []), "language": s.get("language", ""),
                "instrumental": bool(s.get("instrumental")), "brief": None, "kit": kit_json.parent,
            })
    return out


def weekly_songs() -> list[dict]:
    out = []
    for p in sorted((OUT / "briefs").glob("*.json")):
        if p.name.startswith("2000-") or "." in p.stem or "_" not in p.stem:
            continue
        b = read_json(p)
        if "artist_slug" not in b or "week_start" not in b:
            continue
        a = load_artist(b["artist_slug"], b.get("label_slug"), missing_ok=True)
        if a is None:
            continue
        week, slug = b["week_start"], b["artist_slug"]
        meta_p = OUT / "distrokid" / week / f"{slug}.metadata.json"
        meta = read_json(meta_p) if meta_p.exists() else {}
        cands = list(b.get("title_candidates") or [])
        title = meta.get("title") if meta.get("title") not in (None, "", "（未定）") else (b.get("title") or (cands[0] if cands else ""))
        lyr = p.with_suffix(".lyrics_final.txt")
        out.append({
            "id": p.stem, "kind": "weekly", "key": p.stem, "slug": slug, "label_slug": b.get("label_slug"),
            "artist": a.get("name") or slug, "title": title or "", "alts": [c for c in cands if c != title],
            "week": week, "release_at": b.get("release_at"), "style": b.get("suno_style_prompt", ""),
            "lyrics": lyr.read_text(encoding="utf-8") if lyr.exists() else str(b.get("suno_lyrics") or ""),
            "exclude": b.get("suno_exclude", "") or "", "genre": list((a.get("sound") or {}).get("genres") or [])[:2],
            "language": b.get("language") or (a.get("lyrics") or {}).get("language", ""),
            "instrumental": b.get("language") == "none", "brief": p, "kit": None,
        })
    return out


def all_songs() -> list[dict]:
    return debut_songs() + weekly_songs()


def by_key(key: str) -> dict | None:
    return next((s for s in all_songs() if s["key"] == key), None)


def by_id(song_id: str) -> dict | None:
    return next((s for s in all_songs() if s["id"] == song_id or s["key"] == song_id), None)


def folder_name(song: dict) -> str:
    """公開セットのフォルダ名（04_楽曲/<曲名>）。曲名が未定なら制作週"""
    return safe(song["title"]) if song.get("title") else song["week"]


def cover_stem(song: dict) -> str:
    """ジャケットの名前（番号の前まで）：<アーティスト名>_<曲名>"""
    return safe(f"{song['artist']}_{song['title'] or song['week']}")


def cover_source(song: dict) -> Path | None:
    """その曲の仕上がったジャケット（3000×3000）"""
    if song["kind"] == "debut":
        p = song["kit"] / "images" / f"{song['slug']}_cover.jpg"   # kit_images.py が作ったもの（covers/ は下書きなので使わない）
        return p if p.exists() else None
    p = OUT / "visuals" / song["slug"] / "cover" / song["week"] / "cover_final.jpg"
    return p if p.exists() else None


def state(song: dict) -> dict:
    p = STATE_DIR / f"{song['key']}.json"
    return read_json(p) if p.exists() else {}


def save_state(song: dict, st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{song['key']}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def debut_registered(slug: str) -> bool | None:
    """その組のデビュー曲を DistroKid に登録したか（デビュー曲が無い組は None）"""
    ds = [s for s in debut_songs() if s["slug"] == slug]
    if not ds:
        return None
    return any(state(s).get("status") in ("registered", "live") for s in ds)
