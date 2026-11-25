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
import threading
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
            sp = STATE_DIR / f"{kit_json.parent.name}_{s['slug']}.json"
            if sp.exists() and read_json(sp).get("status") == "stopped":   # 組を外したときに止めたデビュー曲（決定 166）
                continue
            out.append({
                "id": f"PDR-{i:03d}", "kind": "debut", "key": f"{kit_json.parent.name}_{s['slug']}",
                "slug": s["slug"], "label_slug": None, "artist": a.get("name") or s.get("artist") or s["slug"],
                "title": s.get("title", ""), "alts": list(s.get("title_alt") or []), "week": kit_json.parent.name,
                "release_at": None, "style": s.get("style", ""), "lyrics": s.get("lyrics", ""),
                "exclude": s.get("exclude", ""), "genre": list(s.get("genre") or []), "language": s.get("language", ""),
                "instrumental": bool(s.get("instrumental")), "brief": None, "kit": kit_json.parent, "bpm": s.get("bpm"),
                "episode": s.get("episode") or {},   # この曲のエピソード（決定 33）
            })
    return out


def meta_path(song: dict) -> Path:
    """登録データ（写し）の置き場所"""
    return OUT / "distrokid" / str(song["week"]) / f"{song['slug']}.metadata.json"


META_STATUS = {"registered": "uploaded", "live": "live"}   # 曲の記録の状態 → 登録データの状態


def sync_meta(song: dict, st: dict | None = None) -> bool:
    """登録データ（out/distrokid/<週>/<組>.metadata.json）を、正本に合わせて書き直す（決定 177）。
    正本は 2 つだけ：曲名＝ブリーフ（デビュー曲は作業セットの kit.json）、配信の記録（状態・配信日・ISRC・UPC・住所）＝曲の記録。
    登録データは、成績集め・四半期の計画・SNS の準備が読む「写し」で、人も機械もここを直接は書き換えない。変わったら True"""
    if song.get("kind") != "weekly":
        return False
    st = state(song) if st is None else st
    p, status = meta_path(song), st.get("status", "")
    if not p.exists() and status not in META_STATUS:
        return False   # 登録するまでは、登録シートづくり（distrokid_sheet.py）が作る
    old = read_json(p) if p.exists() else {"artist_slug": song["slug"], "label_slug": song.get("label_slug"), "week_start": song["week"],
                                           "brief": str(song.get("brief") or ""), "ai_disclosed": True}
    new = dict(old)
    if song.get("title"):
        new["title"] = song["title"]
    for k in ("isrc", "upc", "url"):
        if st.get(k):
            new[k] = st[k]
    if status in META_STATUS:
        new["status"] = META_STATUS[status]
    elif old.get("status") in ("uploaded", "live"):
        new["status"] = "planned"   # 登録を取り消した
    base = str(old.get("release_at_utc") or song.get("release_at") or "")
    day = str(st.get("released") or st.get("release_date") or "")[:10]
    if day:
        new["release_at_utc"] = day + (base[10:] if len(base) > 10 else "T21:00:00Z")
    elif base and not new.get("release_at_utc"):
        new["release_at_utc"] = base
    if new == old or not new.get("release_at_utc"):
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def migrate_meta() -> int:
    """1 回だけ：これまで登録データにだけあった曲名・ISRC・UPC を、正本（ブリーフ・曲の記録）へ写す（決定 177）。
    前は「登録データの曲名」がブリーフの曲名より優先されていたので、いま見えている曲名が変わらないようにする。写した数を返す"""
    mark = OUT / "publish" / "meta_migrated.json"
    if mark.exists() or not (OUT / "distrokid").is_dir():
        return 0
    n = 0
    for p in sorted((OUT / "briefs").glob("*.json")):
        if p.name.startswith("2000-") or "." in p.stem or "_" not in p.stem:
            continue
        b = read_json(p)
        if "artist_slug" not in b or "week_start" not in b:
            continue
        mp = OUT / "distrokid" / str(b["week_start"]) / f"{b['artist_slug']}.metadata.json"
        if not mp.exists():
            continue
        m = read_json(mp)
        t = m.get("title")
        if t and not str(t).startswith("（") and b.get("title") != t:
            b["title"] = t
            p.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            n += 1
        sp = STATE_DIR / f"{p.stem}.json"
        st = read_json(sp) if sp.exists() else {}
        got = {k: m[k] for k in ("isrc", "upc") if m.get(k) and not st.get(k)}
        if got:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            sp.write_text(json.dumps({**st, **got}, ensure_ascii=False, indent=1), encoding="utf-8")
            n += 1
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(json.dumps({"moved": n}, ensure_ascii=False), encoding="utf-8")
    return n


def weekly_songs() -> list[dict]:
    try:
        migrate_meta()
    except OSError:
        pass
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
        cands = list(b.get("title_candidates") or [])
        title = b.get("title") or (cands[0] if cands else "")   # 曲名の正本はブリーフ（登録データは写し。決定 177）
        lyr = p.with_suffix(".lyrics_final.txt")
        out.append({
            "id": p.stem, "kind": "weekly", "key": p.stem, "slug": slug, "label_slug": b.get("label_slug"),
            "artist": a.get("name") or slug, "title": title or "", "alts": [c for c in cands if c != title],
            "week": week, "release_at": b.get("release_at"), "style": b.get("suno_style_prompt", ""),
            "lyrics": lyr.read_text(encoding="utf-8") if lyr.exists() else str(b.get("suno_lyrics") or ""),
            "exclude": b.get("suno_exclude", "") or "", "genre": list((a.get("sound") or {}).get("genres") or [])[:2],
            "language": b.get("language") or (a.get("lyrics") or {}).get("language", ""),
            "instrumental": b.get("language") == "none", "brief": p, "kit": None, "bpm": b.get("bpm"),
            "suno_options": b.get("suno_settings") or {}, "scene": b.get("scene"), "scene_base": b.get("scene_base"),
            "episode": b.get("episode") or {},
            "sprint": b.get("sprint"),   # 集中配信の曲（決定 53）：配信日はいちばん早い日
            "irregular": b.get("irregular"),   # 不定期配信の曲（決定 72）：ダッシュボードの「不定期配信」のタブに並べる
            "daily": b.get("daily"),   # 毎日配信の曲（決定 94）：配信日は daily.date に固定。「不定期・毎日配信」のタブに並べる
        })
    return out


_FROZEN = threading.local()   # ダッシュボードの 1 回の表示の間だけ、曲の一覧を覚えておく（何百回も読み直して遅くなるのを防ぐ）


def freeze() -> None:
    """この先 thaw() までは、曲の一覧を読み直さない（同じスレッドの中だけ）"""
    _FROZEN.songs = debut_songs() + weekly_songs()


def thaw() -> None:
    _FROZEN.songs = None


def all_songs() -> list[dict]:
    fz = getattr(_FROZEN, "songs", None)
    if fz is not None:
        return fz
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


def generated_on(song: dict) -> str:
    """曲を Suno で作った日（取り込んだファイルの作成日。publish.py が記録）。記録が無ければ配信用の音源ができた日"""
    st = state(song)
    if st.get("generated_on"):
        return st["generated_on"]
    m = OUT / "masters" / song["key"] / "master.wav"
    if m.exists():
        from datetime import date
        return date.fromtimestamp(m.stat().st_mtime).isoformat()
    return song["week"]


def audio_stem(song: dict, day: str | None = None) -> str:
    """楽曲データ（音声ファイル）の名前：<曲名>_<アーティスト名>_<生成日>（オーナーの指定 2026-10-21）"""
    return safe(f"{song['title'] or song['week']}_{song['artist']}_{day or generated_on(song)}")


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
    try:   # 登録データ（写し）も合わせる（決定 177）
        sync_meta(song, st)
    except OSError:
        pass


def debut_registered(slug: str) -> bool | None:
    """その組のデビュー曲を DistroKid に登録したか（デビュー曲が無い組は None）"""
    ds = [s for s in debut_songs() if s["slug"] == slug]
    if not ds:
        return None
    return any(state(s).get("status") in ("registered", "live") for s in ds)


# ---------------------------------------------------------------------------
# Suno の作曲の軸のうち、組ごとに決めておくもの（templates/suno_options.json。Gon さんが書き換えてよい）
SUNO_OPTIONS = ROOT / "templates" / "suno_options.json"
# 「あなたの好み」を組ごとにオンにするかの知識（再生数が伸びた曲から、あとで作る）：{"<組>": {"on": true, "why": "…"}}
YOUR_TASTE = OUT / "knowledge" / "your_taste.json"
GENDER_JA = {"male": "男", "female": "女", None: "選ばない"}


def suno_options(song: dict) -> dict:
    """{'vocal_gender', 'variety'(0〜4), 'style_influence'(%), 'weirdness'(%), 'length_sec'(秒 or None＝自動), 'max_mode', 'your_taste', …}
    共通（default）→ 組ごと → 知識（あなたの好み）→ 曲ごとの指定（song['suno_options']）の順に上書き"""
    d = read_json(SUNO_OPTIONS) if SUNO_OPTIONS.exists() else {}
    opt = {k: v for k, v in (d.get("default") or {}).items() if not k.startswith("_")}
    opt.update({k: v for k, v in (d.get(song.get("slug", ""), {}) or {}).items() if not k.startswith("_")})
    taste = read_json(YOUR_TASTE) if YOUR_TASTE.exists() else {}
    t = taste.get(song.get("slug", ""))
    if t is not None:
        opt["your_taste"] = bool(t.get("on") if isinstance(t, dict) else t)
    opt.update(song.get("suno_options") or {})
    return opt


def fmt_len(sec) -> str:
    return "自動" if not sec else f"カスタム {int(sec) // 60}:{int(sec) % 60:02d}"


def suno_options_text(song: dict) -> str:
    o = suno_options(song)
    if not o:
        return ""
    onoff = lambda v: "オン" if v else "オフ"  # noqa: E731
    parts = []
    if "vocal_gender" in o:
        parts.append(f"声の性別：{GENDER_JA.get(o['vocal_gender'], o['vocal_gender'])}")
    if "length_sec" in o:
        parts.append(f"間隔：{fmt_len(o['length_sec'])}")
    if "max_mode" in o:
        parts.append(f"Max mode：{onoff(o['max_mode'])}")
    if o.get("weirdness") is not None:
        parts.append(f"Weirdness：{o['weirdness']}%")
    if o.get("style_influence") is not None:
        parts.append(f"スタイルの影響度：{o['style_influence']}%")
    if o.get("variety") is not None:
        parts.append(f"バラエティ：{o['variety']}")
    if "your_taste" in o:
        parts.append(f"あなたの好み：{onoff(o['your_taste'])}")
    return "・".join(parts)
