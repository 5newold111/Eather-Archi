#!/usr/bin/env python3
"""
アーティスト管理表（スプレッドシート）を中心にした管理。

  管理表がアーティスト情報の「正本」。オーナーが書き足し、機械は読んで曲づくりに使う。
  機械が作るものは、すべて「状態＝提案」の行として管理表に足すだけ。採用するかはオーナーが決める。

  init            管理表を新しく作る（music-label/artists.xlsx。Google スプレッドシートに取り込んでもよい）
  check           提案の JSON を管理表に入れる前に確かめる（項目名・型・実名の混入）
  import-json     提案の JSON（proposals/ の中）を「提案」の行として管理表に入れる
  propose         Claude が新しいアーティストを丸ごと提案する（管理表に「提案」の行で入る）
  propose-update  Claude が組ごとに来月の近況と歌の種を提案する（その時々の想いを歌にするため）
  adopt           1 組ぶんの「提案」の行をまとめて「採用」にする（却下・保留にした行はそのまま）
  pull            管理表の「採用」の行から、制作で使う設定書（templates/）を作り直す
  status          提案の数・空欄の多い項目・参考曲の入手状況
  render          管理表の内容を、読みやすい資料（proposals/<組>.md）に書き出す
  restyle         管理表の中身をそのまま、デザインを整えた Excel に書き出す（Google の表へは「インポート → 置換」で入れる）
  apply-changes   「変更の提案」タブで採用された変更を、該当する欄に書き込む

使い方
  python3 scripts/artist_book.py init
  python3 scripts/artist_book.py import-json proposals/2026-10-08
  python3 scripts/artist_book.py propose --label 本体 --note "日本とアメリカのハーフ、朝の歌"
  python3 scripts/artist_book.py restyle                # 今の中身のまま、見やすいデザインの Excel に書き出し直す
  python3 scripts/artist_book.py pull
  python3 scripts/artist_book.py status

状態の列：提案（機械の案）／採用（使う）／保留／却下。空欄は採用として扱う（オーナーが自分で書いた行）。
管理表の場所：既定は music-label/artists.xlsx。.env に ARTIST_BOOK_GSHEET_ID と GOOGLE_SERVICE_ACCOUNT_FILE を入れると
Google スプレッドシートを読む（docs/13_artist_book.md）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _artist_book import PENDING, TAB_ORDER, TABS, ReadOnlyBook, XlsxBook, adopted, append_rows, open_book  # noqa: E402
from _common import MAIN_LABEL, OUT, ROOT, all_artists, load_dotenv, load_label, read_json, save_artist, step, write_json  # noqa: E402

load_dotenv()
FORMATION = {"ソロ": "solo", "デュオ": "duo", "バンド": "band", "ボーカルグループ": "vocal_group", "プロデューサー": "producer"}
SEX = {"女性": "female", "男性": "male", "男女": "mixed", "なし": "none"}
RANGE = {"高": "high", "中": "mid", "低": "low"}
LYRIC_MODE = {"核を固定": "core_fixed", "全部書く": "full", "お題だけ": "topic_only", "歌なし": "instrumental"}
LANG = {"英語": "en", "日本語": "ja", "スペイン語": "es", "韓国語": "ko", "ポルトガル語": "pt", "なし": "none"}
LABEL_JA = {"本体": MAIN_LABEL}
PROPOSALS = ROOT / "proposals"



def book_or_die(if_exists: bool = False):
    b = open_book()
    if hasattr(b, "path") and not b.path.exists():
        if if_exists:   # 定期実行から呼ばれたとき：管理表がまだ無ければ何もしない
            print(f"   管理表がまだ無いので飛ばします（{b.path.name}）")
            sys.exit(0)
        sys.exit(f"[案内] 管理表がまだありません。先に  python3 scripts/artist_book.py init  で作ってください（{b.path}）")
    return b


# ---------------------------------------------------------------------------
# 提案を管理表に入れる
# ---------------------------------------------------------------------------
def proposal_rows(p: dict, status: str = "提案") -> dict[str, list[dict]]:
    """提案の JSON（1 組ぶん）→ タブごとの行"""
    act = dict(p["act"])
    aid = act["id"]
    act.setdefault("updated", date.today().isoformat())
    out: dict[str, list[dict]] = {"acts": [{**act, "status": status}]}
    for tab in TAB_ORDER:
        if tab == "acts":
            continue
        v = p.get(tab)
        rows = [v] if isinstance(v, dict) else (v or [])
        out[tab] = [{**r, "act_id": aid, "status": status} for r in rows]
    return out


def append_proposal(book, p: dict) -> None:
    for tab, rows in proposal_rows(p).items():
        append_rows(book, tab, rows)


def cmd_import_json(a) -> None:
    book = book_or_die()
    src = Path(a.path)
    files = sorted(src.glob("*.json")) if src.is_dir() else [src]
    existing = {r["id"] for r in book.read()["acts"]}
    for f in files:
        p = read_json(f)
        if p["act"]["id"] in existing and not a.force:
            print(f"   － {p['act']['name']}（{p['act']['id']}）は管理表に既にあるので入れません（入れ直すなら --force）")
            continue
        step(f"{p['act']['name']}（{p['act']['id']}）を『提案』として管理表に入れています")
        append_proposal(book, p)
    step(f"入れ終わりました：{book.describe()}")


# ---------------------------------------------------------------------------
# 管理表 → 設定書
# ---------------------------------------------------------------------------
def group(data: dict, aid: str, tab: str, only_adopted: bool = True) -> list[dict]:
    rows = [r for r in data.get(tab, []) if r.get("act_id") == aid]
    return adopted(rows) if only_adopted else rows


def strip_row(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("_pos", "_row", "status", "act_id") and v not in ("", None, [])}


def build_sheet(act: dict, data: dict, old: dict | None) -> tuple[dict, list[str]]:
    aid = act["id"]
    warn = []
    label = LABEL_JA.get(act.get("label", "本体"), act.get("label") or MAIN_LABEL)
    members = group(data, aid, "members")
    music = (group(data, aid, "music") or [{}])[0]
    vis = (group(data, aid, "visual") or [{}])[0]
    infl = group(data, aid, "influences")
    old = old or {}
    for k, msg in (("name", "名前"), ("formation", "構成")):
        if not act.get(k):
            warn.append(f"{msg} が空欄")
    if not members:
        warn.append("メンバーが 1 人もいない（採用の行がない）")
    if not music:
        warn.append("『声と曲づくり』の行がない")
    same_name = old.get("name") == act.get("name")
    music_infl = [i for i in infl if str(i.get("kind", "")).startswith("音楽")]
    main = members[0] if members else {}

    def tic(n):
        t = music.get(f"tic{n}")
        return {"technique": t, "where": music.get(f"tic{n}_where", ""), "frequency": "every_song"} if t else None

    sheet = {
        "slug": aid,
        "name": act.get("name", ""),
        "name_status": old.get("name_status", "draft") if same_name else "draft",
        "name_check": old.get("name_check", {}) if same_name else {},
        "formation": FORMATION.get(act.get("formation", ""), act.get("formation") or "solo"),
        "cadence": old.get("cadence", "weekly"),
        "debut_week": old.get("debut_week"),
        "persona": {
            "members": [{"role": m.get("role", ""), "description": f"{m.get('gender', '')}・{m.get('age', '')} 歳。{m.get('personality', '')}"}
                        for m in members],
            "age_feel": "、".join(f"{m.get('name', '')} {m.get('age', '')} 歳" for m in members),
            "story": act.get("current_chapter") or act.get("values", ""),
            ("drive_scene" if label == MAIN_LABEL else "scene"): act.get("scene", ""),
            "origin": {"birthplace": main.get("birthplace", ""), "upbringing": act.get("origin", ""),
                       "landscape_words": act.get("landscape_words", [])},
            "influences": [{"name": i.get("author") or i.get("title", ""), "era": "", "genre": "",
                            "what_was_taken": i.get("taken", "")} for i in music_infl],
            "formation_story": {"started": (group(data, aid, "debut") or [{}])[0].get("when", ""),
                                "how": act.get("debut_summary", ""), "inspired_by_pattern": "",
                                "first_release": "", "name_meaning": act.get("name_meaning", "")},
        },
        "vocal": {
            "sex": SEX.get(music.get("v_sex", ""), music.get("v_sex") or "none"),
            "range": RANGE.get(music.get("v_range", ""), music.get("v_range") or "mid"),
            "fixed_timbre": bool(music.get("fixed_timbre", True)),
            "suno_persona_id": (old.get("vocal") or {}).get("suno_persona_id"),
            "voice_spec": {"comfortable_low": music.get("low", ""), "comfortable_high": music.get("high", ""),
                           "chest_voice_top": music.get("chest_top", ""), "falsetto_switch_point": music.get("switch", ""),
                           "falsetto_quality": music.get("falsetto", ""), "strength": music.get("v_strength", ""),
                           "weakness": music.get("v_weakness", "")},
            "signature_techniques": {"primary": music.get("primary_tech", ""), "secondary": music.get("secondary_tech", []),
                                     "avoid": music.get("avoid_tech", []), "wordless_style": music.get("wordless", ""),
                                     "tics": [t for t in (tic(1), tic(2), tic(3)) if t]},
            "melodic_tendency": {"motion": music.get("melodic", ""), "chorus_peak": music.get("chorus_peak", ""),
                                 "verse_register": music.get("verse_register", "")},
            "timbre_blend": (old.get("vocal") or {}).get("timbre_blend", {}),
            "guest_description": music.get("guest", ""),
        },
        "composition_habits": {"modulation": music.get("modulation", ""), "intro": music.get("intro", ""),
                               "outro": music.get("outro", ""), "phrase_endings": music.get("phrase_endings", ""), "structure": music.get("structure", ""),
                               "instruments_rule": music.get("inst_rule", ""), "rhythm": music.get("rhythm", ""),
                               "lyrics_tic": music.get("lyrics_tic", ""), "always": music.get("always", []),
                               "never": music.get("never", [])},
        "sound": {"genres": music.get("genres", []), "palette": music.get("palette", []),
                  "bpm_min": music.get("bpm_min") or 90, "bpm_max": music.get("bpm_max") or 120,
                  "energy_curve_type": (old.get("sound") or {}).get("energy_curve_type", ""),
                  "dna_tags": music.get("dna", []), "forbidden": music.get("never", [])},
        "lyrics": {"themes": music.get("themes", []), "point_of_view": music.get("pov", ""),
                   "primary_language": LANG.get(music.get("lang", "英語"), music.get("lang") or "en"),
                   "trend_language_ok": (old.get("lyrics") or {}).get("trend_language_ok", False),
                   "trend_language_max_ratio": (old.get("lyrics") or {}).get("trend_language_max_ratio", 0.0),
                   "tone": music.get("tone", ""), "forbidden_topics": music.get("avoid_topics", []),
                   "mode": LYRIC_MODE.get(music.get("lyric_mode", ""), "core_fixed"),
                   "suno_share": 0.45, "topic_only_ratio": 0.2},
        "visual": {"space": vis.get("space", ""), "light": vis.get("light", ""), "materials": vis.get("materials", []),
                   "palette": vis.get("palette", []), "camera": vis.get("camera", ""),
                   "cover_series_rule": vis.get("cover_rule", ""),
                   "blend_file": (old.get("visual") or {}).get("blend_file"),
                   "face_concealment": {"primary": vis.get("conceal", ""), "secondary": vis.get("conceal2", []),
                                        "never": vis.get("never", [])},
                   "logo": {"concept": vis.get("logo_concept", ""), "mark_type": vis.get("logo_mark", ""),
                            "color_rule": vis.get("logo_color", ""), "typography": vis.get("logo_font", "")},
                   "photo_direction": {k: vis.get(k2, "") for k, k2 in (
                       ("taste", "taste"), ("composition", "composition"), ("location_type", "location"),
                       ("brightness", "brightness"), ("contrast", "contrast"), ("color_temperature", "temperature"),
                       ("background", "background"), ("positioning", "position"), ("gesture", "gesture"))}},
        "distribution": {**{k: v for k, v in (old.get("distribution") or {}).items() if k != "bio_en"},
                         "bio_en": act.get("bio_en", "")},
        "collab": {"preferred_partners": music.get("partners", []), "collab_style": music.get("collab_style", "")},
        "profile": {"hometown": main.get("birthplace", ""), "height_cm": [m.get("height") for m in members],
                    "weight_kg": [m.get("weight") for m in members],
                    "likes": sum((m.get("likes", []) for m in members), []),
                    "favorite_food": "、".join(sum((m.get("like_foods", []) for m in members), [])),
                    "dislikes": sum((m.get("dislikes", []) for m in members), []),
                    "favorite_artists_real": [i.get("author") or i.get("title") for i in music_infl]},
        "concept_history": old.get("concept_history") or {"concept_version": 1, "history": []},
        "life": {
            "act": strip_row(act),
            "members": [strip_row(m) for m in members],
            "timeline": [strip_row(r) for r in group(data, aid, "timeline")],
            "debut_steps": [strip_row(r) for r in group(data, aid, "debut")],
            "marks": [strip_row(r) for r in group(data, aid, "marks")],
            "influences": [strip_row(r) for r in infl],
            "seeds": [strip_row(r) for r in group(data, aid, "seeds")],
            "lyric_sketches": [strip_row(r) for r in group(data, aid, "lyrics")],
            "updates": [strip_row(r) for r in group(data, aid, "updates")],
            "song_choices": [strip_row(r) for r in group(data, aid, "choices")],
        },
        "source": {"pulled_at": datetime.now().isoformat(timespec="seconds")},
    }
    if label == MAIN_LABEL:
        sheet["sound"]["drive_spec"] = (old.get("sound") or {}).get("drive_spec") or {
            "hook_within_sec": 30, "length_sec_min": 150, "length_sec_max": 210, "no_sudden_silence": True}
    for k in ("expansion",):
        if old.get(k):
            sheet[k] = old[k]
    # 場面や見た目が変わったら、コンセプトの世代を上げて写真の撮り直しを促す
    if old:
        before = (old.get("persona", {}).get("drive_scene") or old.get("persona", {}).get("scene"),
                  {k: (old.get("visual") or {}).get(k) for k in ("space", "light", "palette", "materials")})
        after = (act.get("scene", ""), {k: sheet["visual"][k] for k in ("space", "light", "palette", "materials")})
        if before != after and before[0]:
            h = sheet["concept_history"]
            h["concept_version"] = int(h.get("concept_version", 1)) + 1
            h.setdefault("history", []).append({"version": h["concept_version"], "date": date.today().isoformat(),
                                                "level": "owner", "reason": "管理表で場面・見た目を変更", "photos_reshot": False})
            warn.append(f"場面か見た目が変わったので、コンセプトの世代を {h['concept_version']} にしました"
                        f"（写真の撮り直し：generate_visuals.py --artist {aid} --kind reshoot）")
    sheet["label_slug"] = label
    return sheet, warn


def cmd_pull(a) -> None:
    book = book_or_die(a.if_exists)
    step(f"管理表を読んでいます：{book.describe()}")
    data = book.read()
    snap = OUT / "artist_book" / "snapshots" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    write_json(snap, data)
    olds = {x["slug"]: x for x in all_artists()}
    acts = adopted(data["acts"])
    if not acts:
        print("   採用の組がまだありません（『アーティスト』タブの状態を『採用』にすると、設定書が作られます）")
    for act in acts:
        sheet, warn = build_sheet(act, data, olds.get(act["id"]))
        if a.dry_run:
            print(f"   [確認だけ] {sheet['name']}（{sheet['slug']}）")
        else:
            save_artist(sheet)
            print(f"   ○ {sheet['name']}（{sheet['slug']}／{sheet['label_slug']}）の設定書を作り直しました")
        for w in warn:
            print(f"      ⚠ {w}")
    gone = sorted(set(olds) - {x["id"] for x in acts})
    if gone:
        print(f"   － 管理表で採用されていない組の設定書はそのまま残しています：{', '.join(gone)}")
    step(f"読み込みの記録：{snap.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Claude の提案
# ---------------------------------------------------------------------------
PROPOSE_SYSTEM = """You design virtual music acts for EtherArchi, a label that pairs AI-generated music with spatial
design (the five elements: shape, quality, light, time, and the self). Each act lives a full, specific human life; their
songs come from the feelings of particular moments in that life. Tone: refined but approachable, never theatrical.

Return ONE JSON object with exactly these keys: act, members, timeline, debut, marks, influences, seeds, lyrics,
updates, choices, music, visual. The value shapes and field meanings are given in the user's template (Japanese).
Rules:
- Write descriptive values in Japanese; act.bio_en, lyrics.chorus and lyrics.verse in English.
- Origins: anywhere in the world, but believable for an act that can grow in English-speaking markets.
- The life must be concrete: places, ages, schools, jobs, people, small details; timeline covers birth to now and the
  future plan, at least 6 rows for the lead person. debut rows describe how they shared music outside streaming
  (street, small venues, livestream covers, contests, school festival clips going viral, etc.) and how fans grew, with
  rough numbers.
- influences: real artists, books, paintings, films, and words that genuinely fit; these are the ONLY places where
  real names may appear (plus choices). choices: 6-8 real, existing songs this act would pick as references, with the
  year and which elements (slots: lyrics, worldview, instruments, performance, structure, harmony, groove, phrase,
  vocal) to borrow. Never invent a song; if unsure, leave it out.
- seeds: 6-10 song ideas tied to timeline moments. lyrics: 2 original sketches (4-line chorus, 2-4 line verse opening)
  written in the act's own voice; never quote or imitate a real lyric.
- music: voice spec in note names, 2-3 recurring tics writable as Suno tags, genres, BPM range, etc.
- visual: the face is never shown (pick a concealment method); describe space, light, palette, logo idea.
- Do not overlap the existing acts listed (voice range/sex, formation, origin, scene)."""


def template_for_prompt() -> dict:
    t = {}
    for tab in TAB_ORDER:
        if tab == "changes":
            continue
        cols = {k: h for k, h, _ in TABS[tab][1] if k not in ("status", "act_id")}
        t[tab] = cols if tab in ("act", "music", "visual") else cols
    t["act"] = t.pop("acts")
    t["_shape"] = "act/music/visual はオブジェクト、ほかは配列（1 行 1 要素）。メンバーには id を付け、ほかの行の member_id で参照"
    return t


def existing_summary() -> list[dict]:
    out = []
    for x in all_artists():
        v = x.get("vocal") or {}
        out.append({"id": x["slug"], "name": x["name"], "label": x["label_slug"], "formation": x.get("formation"),
                    "voice": f"{v.get('sex')}/{v.get('range')}", "genres": (x.get("sound") or {}).get("genres"),
                    "origin": (x.get("persona") or {}).get("origin", {}).get("birthplace")})
    return out


def check_shape(p: dict) -> list[str]:
    """項目の名前と型（文字・数・一覧・○）、メンバーと歌の種のつながりを確かめる"""
    probs = []
    keys = {"act": "acts", **{t: t for t in TAB_ORDER if t not in ("acts", "changes")}}
    for k in p:
        if k not in keys and not k.startswith("_"):
            probs.append(f"知らない項目 {k}")
    for k, tab in keys.items():
        v = p.get(k)
        if v is None:
            continue
        rows = [v] if isinstance(v, dict) else v
        if not isinstance(rows, list):
            probs.append(f"{k} の形が違う（オブジェクトか配列）")
            continue
        cols = {c: kind for c, _, kind in TABS[tab][1]}
        for i, r in enumerate(rows):
            for c, val in r.items():
                kind = cols.get(c)
                where = f"{k}[{i}].{c}"
                if kind is None:
                    probs.append(f"知らない欄 {where}")
                elif kind == "list":
                    if not isinstance(val, list):
                        probs.append(f"{where} は配列で書く")
                    else:
                        probs += [f"{where} の要素に「、」がある（管理表で分かれてしまう）：{x}" for x in val if "、" in str(x)]
                elif kind == "int" and val is not None and not isinstance(val, int):
                    probs.append(f"{where} は整数で書く")
                elif kind == "bool" and not isinstance(val, bool):
                    probs.append(f"{where} は true / false で書く")
    mids = {m.get("id") for m in p.get("members", [])}
    for tab in ("timeline", "marks", "influences"):
        for r in p.get(tab, []):
            if r.get("member_id") and r["member_id"] not in mids:
                probs.append(f"{tab} の member_id {r['member_id']} がメンバーにいない")
    sids = {s.get("id") for s in p.get("seeds", [])}
    for r in p.get("lyrics", []):
        if r.get("seed_id") and r["seed_id"] not in sids:
            probs.append(f"歌詞の試作の seed_id {r['seed_id']} が歌の種にない")
    return probs


def validate_proposal(p: dict) -> list[str]:
    probs = check_shape(p)
    for k in ("act", "members", "timeline", "music", "visual", "seeds", "choices"):
        if not p.get(k):
            probs.append(f"{k} が空")
    act = p.get("act") or {}
    if not act.get("id") or not str(act["id"]).replace("_", "").isalnum() or not str(act["id"]).islower():
        probs.append(f"ID の形が不正（{act.get('id')}）")
    names = {x["name"].lower() for x in all_artists() if x["slug"] != act.get("id")}
    if str(act.get("name", "")).lower() in names:
        probs.append(f"名前 {act.get('name')} は既にある")
    real = {str(i.get("author") or i.get("title") or "").lower() for i in p.get("influences", []) if str(i.get("kind", "")).startswith("音楽")}
    real |= {str(c.get("artist", "")).lower() for c in p.get("choices", [])}
    real |= {str(n).lower() for m in p.get("members", []) for n in (m.get("fav_artists") or [])}
    real.discard("")
    rest = {k: v for k, v in p.items() if k not in ("influences", "choices")}
    rest["members"] = [{k: v for k, v in m.items() if k not in ("fav_artists", "instruments", "instruments_sub", "gear")}
                       for m in p.get("members", [])]   # 実名・機種名を書いてよい欄
    body = json.dumps(rest, ensure_ascii=False).lower()
    probs += [f"実在アーティスト名（{n}）が影響・参考曲以外に入っている" for n in real if len(n) > 3 and n in body]
    return probs


def cmd_propose(a) -> None:
    from _claude import available, call_json_free, dry_run_file
    book = book_or_die()
    label = LABEL_JA.get(a.label, a.label)
    lab = load_label(label)
    user = json.dumps({"label": {k: lab.get(k) for k in ("slug", "name", "scene", "sound_center", "expansion_path", "scene_spec")},
                       "owner_note": a.note, "existing_acts": existing_summary(),
                       "template": template_for_prompt()}, ensure_ascii=False, indent=1)
    if not available():
        f = OUT / "artist_book" / f"propose_{label}_{date.today()}.prompt.md"
        dry_run_file(f, PROPOSE_SYSTEM, user)
        print(f"   ANTHROPIC_API_KEY が無いので、依頼文を {f.relative_to(ROOT)} に書き出しました")
        return
    for n in range(a.count):
        step(f"Claude が新しいアーティストを考えています（{n + 1}/{a.count}）")
        p, msg = call_json_free(PROPOSE_SYSTEM, user)
        if not p:
            print(f"   ✕ 提案を作れませんでした：{msg}")
            continue
        p.setdefault("act", {}).update(label="本体" if label == MAIN_LABEL else label)
        p["act"].pop("axis", None)
        probs = validate_proposal(p)
        if probs:
            bad = OUT / "artist_book" / f"rejected_{date.today()}_{n}.json"
            write_json(bad, {"problems": probs, "proposal": p})
            print(f"   ✕ 提案に問題があるので管理表に入れません：{' / '.join(probs[:3])}（{bad.relative_to(ROOT)}）")
            continue
        PROPOSALS.mkdir(parents=True, exist_ok=True)
        write_json(PROPOSALS / f"{date.today()}_{p['act']['id']}.json", p)
        append_proposal(book, p)
        step(f"『{p['act']['name']}』を提案として管理表に入れました（状態を『採用』にすると使われます）")


UPDATE_SYSTEM = """You continue the life of a virtual music act from EtherArchi. Given the act's profile and timeline,
propose what happens in their life during the coming month: 2-4 small, believable events (not melodrama) with the feeling
each one leaves, and 1-2 new song ideas that come from them. Stay consistent with everything already true about them.
Return JSON {"updates": [{"date": "YYYY-MM-DD", "event": "...", "feeling": "...", "to_song": true|false}],
"seeds": [{"id": "...", "period": "...", "feeling": "...", "scene": "...", "pov": "...", "idea": "...", "words": [..]}]}.
Japanese for descriptive text. No real people's names."""


def cmd_propose_update(a) -> None:
    from _claude import available, call_json_free, dry_run_file
    book = book_or_die(a.if_exists)
    targets = [x for x in all_artists() if (a.all or x["slug"] == a.artist) and x.get("life")]
    if not targets:
        if a.if_exists:
            print("   管理表から読み込んだ組がまだ無いので飛ばします")
            return
        sys.exit("[案内] 管理表から読み込んだ組がありません（pull の後に使います）")
    pending = {r.get("act_id") for r in book.read()["updates"] if str(r.get("status") or "").strip() == "提案"}
    for x in targets:
        if x["slug"] in pending and not a.force:
            print(f"   － {x['name']}：返事待ちの近況の提案があるので、新しい提案は足しません（足すなら --force）")
            continue
        life = {k: v for k, v in x["life"].items() if k not in ("influences", "song_choices")}
        user = json.dumps({"act": x["name"], "today": date.today().isoformat(), "life": life}, ensure_ascii=False, indent=1)
        if not available():
            f = OUT / "artist_book" / f"update_{x['slug']}_{date.today()}.prompt.md"
            dry_run_file(f, UPDATE_SYSTEM, user)
            print(f"   ANTHROPIC_API_KEY が無いので、依頼文を {f.relative_to(ROOT)} に書き出しました")
            continue
        step(f"{x['name']}：来月の近況と歌の種を考えています")
        res, msg = call_json_free(UPDATE_SYSTEM, user, effort="medium")
        if not res:
            print(f"   ✕ {msg}")
            continue
        for tab in ("updates", "seeds"):
            rows = [{**r, "act_id": x["slug"], "status": "提案"} for r in res.get(tab, [])]
            append_rows(book, tab, rows)
        print(f"   ○ {x['name']}：近況 {len(res.get('updates', []))} 件・歌の種 {len(res.get('seeds', []))} 件を提案しました")


# ---------------------------------------------------------------------------
def cmd_status(a) -> None:
    book = book_or_die()
    data = book.read()
    names = {r["id"]: r.get("name", r["id"]) for r in data["acts"]}
    print(f"=== 管理表の状態（{book.describe()}）===")
    for r in data["acts"]:
        st = r.get("status") or "採用"
        counts = {TABS[t][0]: sum(1 for x in data[t] if x.get("act_id") == r["id"] and x.get("status") == "提案")
                  for t in TAB_ORDER if t != "acts"}
        props = "、".join(f"{k} {v}" for k, v in counts.items() if v)
        print(f"  {names[r['id']]:<20} {st:<4}  提案の行：{props or 'なし'}")
    ch = [c for c in data["choices"] if (c.get("status") or "採用") in ("採用", "提案")]
    if ch:
        print(f"  参考曲の候補：{len(ch)} 曲（入手 {sum(1 for c in ch if c.get('acquired'))}・解析 {sum(1 for c in ch if c.get('analyzed'))}）")
    pend = [c for c in data["changes"] if c.get("status") == "提案"]
    if pend:
        print(f"  変更の提案：{len(pend)} 件（状態を『採用』にして apply-changes で反映）")


def cmd_apply_changes(a) -> None:
    book = book_or_die()
    data = book.read()
    done = 0
    for c in data["changes"]:
        if c.get("status") != "採用":
            continue
        tab = next((t for t in TAB_ORDER if TABS[t][0] == c.get("tab")), None)
        if not tab:
            print(f"   ✕ タブ『{c.get('tab')}』が見つかりません（{c.get('field')}）")
            continue
        field = next((k for k, h, _ in TABS[tab][1] if h == c.get("field") or k == c.get("field")), None)
        idkey = "id" if tab == "acts" else "act_id"
        target = next((r for r in data[tab] if r.get(idkey) == c.get("act_id") and (r.get("status") or "採用") == "採用"), None)
        if not field or not target:
            print(f"   ✕ 書き込み先が見つかりません：{c.get('act_id')} / {c.get('tab')} / {c.get('field')}")
            continue
        try:
            book.update(tab, target["_pos"], field, c.get("proposed"))
            book.update("changes", c["_pos"], "status", "反映済み")
        except ReadOnlyBook:
            sys.exit("[案内] Google の表を閲覧のみで読んでいるので書き込めません。表の該当欄を直接書き換え、"
                     "変更の提案の状態を『反映済み』にしてください（自動で書くにはサービスアカウント：docs/13）")
        print(f"   ○ {c.get('act_id')}：{c.get('tab')} の『{c.get('field')}』を書き換えました")
        done += 1
    step(f"{done} 件を反映しました。設定書に反映するには  artist_book.py pull")


def cmd_check(a) -> None:
    """提案の JSON を管理表に入れる前に確かめる（項目名・型・実名の混入・つながり）"""
    src = Path(a.path)
    files = sorted(src.glob("*.json")) if src.is_dir() else [src]
    bad = 0
    for f in files:
        p = read_json(f)
        probs = validate_proposal(p)
        lead = [r for r in p.get("timeline", []) if r.get("member_id") == (p.get("members") or [{}])[0].get("id")]
        if len(lead) < 6:
            probs.append(f"中心の人の人生年表が {len(lead)} 行（6 行以上）")
        if not 6 <= len(p.get("choices", [])) <= 10:
            probs.append(f"参考曲の候補が {len(p.get('choices', []))} 曲（6〜10 曲）")
        if len(p.get("seeds", [])) < 6:
            probs.append(f"歌の種が {len(p.get('seeds', []))} 件（6 件以上）")
        music = p.get("music") or {}
        for k in ("bpm_min", "bpm_max"):
            if not isinstance(music.get(k), int):
                probs.append(f"声と曲づくりの {k} が数字でない")
        name = (p.get("act") or {}).get("name", f.stem)
        if probs:
            bad += 1
            print(f"   ✕ {name}（{f.name}）")
            for x in probs:
                print(f"      - {x}")
        else:
            print(f"   ○ {name}（{f.name}）：問題なし")
    if bad:
        sys.exit(1)


def cmd_adopt(a) -> None:
    """1 組ぶんの『提案』の行をまとめて『採用』にする（却下・保留にした行はそのまま）"""
    book = book_or_die()
    data = book.read()
    only = {t for t in TAB_ORDER if TABS[t][0] in (a.tabs or "").split(",") or t in (a.tabs or "").split(",")} if a.tabs else set(TAB_ORDER)
    edits = []
    for tab in TAB_ORDER:
        if tab not in only:
            continue
        idkey = "id" if tab == "acts" else "act_id"
        for r in data[tab]:
            if r.get(idkey) == a.id and str(r.get("status") or "").strip() == "提案":
                edits.append((tab, r["_pos"], "status", "採用"))
    if not edits:
        sys.exit(f"[案内] {a.id} に『提案』の行はありません")
    try:
        book.update_many(edits)
    except ReadOnlyBook:
        sys.exit("[案内] Google の表を閲覧のみで読んでいるので書き込めません。表の『状態』を直接『採用』に変えてください"
                 "（自動で書くにはサービスアカウント：docs/13）")
    by_tab: dict[str, int] = {}
    for tab, *_ in edits:
        by_tab[TABS[tab][0]] = by_tab.get(TABS[tab][0], 0) + 1
    step(f"{a.id}：{len(edits)} 行を『採用』にしました（{'、'.join(f'{k} {v}' for k, v in by_tab.items())}）")
    print("   設定書に反映するには  python3 scripts/artist_book.py pull")


def cmd_init(a) -> None:
    b = open_book()
    if not hasattr(b, "create"):
        sys.exit("[案内] Google スプレッドシートを使う設定です。空の表がほしいときは  restyle --out <ファイル>  で作り、"
                 "Google ドライブで開いてください（docs/13）")
    if b.path.exists() and not a.force:
        sys.exit(f"[案内] 管理表は既にあります：{b.path}（作り直すなら --force。中身は消えます）")
    b.create()
    step(f"管理表を作りました：{b.describe()}")


def cmd_restyle(a) -> None:
    """今の中身をそのまま、デザインを整えた Excel に書き出す（古い横型の表からの移し替えにも使う）"""
    import shutil
    book = book_or_die()
    step(f"管理表を読んでいます：{book.describe()}")
    data = book.read()
    if a.with_pending and PENDING.exists():   # 閲覧のみの設定で貯まった機械の提案も一緒に入れる
        pend = XlsxBook(PENDING).read()
        for tab in TAB_ORDER:
            data[tab] = data.get(tab, []) + pend.get(tab, [])
        step(f"{PENDING.name} に貯まった提案も入れます")
    local = getattr(book, "path", None)
    out = Path(a.out) if a.out else (local or OUT / "artist_book" / "アーティスト管理表.xlsx")
    if local and out.resolve() == local.resolve():
        backup = OUT / "artist_book" / f"{local.stem}_{datetime.now():%Y%m%d-%H%M%S}{local.suffix}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, backup)
        print(f"   （書き出す前の表を {backup.relative_to(ROOT)} に残しました）")
    XlsxBook(out).create(data)
    n = sum(len(v) for v in data.values())
    step(f"デザインを整えた表を書き出しました：{out}（{len(data['acts'])} 組・{n} 件）")
    if not local:
        print("   Google の表に入れるには：Google スプレッドシートで『ファイル → インポート → アップロード』でこのファイルを選び、"
              "『スプレッドシートを置換する』を選ぶ（URL と共有設定はそのまま）")


def cmd_render(a) -> None:
    """管理表の組ごとに、読みやすい資料を書き出す（提案の行も含めて全部）"""
    from render_profile import render_act
    book = book_or_die()
    data = book.read()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for act in data["acts"]:
        md = render_act(act, data)
        (out / f"{act['id']}.md").write_text(md, encoding="utf-8")
        print(f"   ○ {act.get('name')} → {(out / (act['id'] + '.md')).relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="アーティスト管理表（スプレッドシート）の管理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("import-json"); s.add_argument("path"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("check"); s.add_argument("path")
    s = sub.add_parser("adopt"); s.add_argument("id"); s.add_argument("--tabs", help="タブを絞る（例：近況,歌の種）")
    s = sub.add_parser("propose"); s.add_argument("--label", default="本体")
    s.add_argument("--note", default=""); s.add_argument("--count", type=int, default=1)
    s = sub.add_parser("propose-update"); s.add_argument("--artist"); s.add_argument("--all", action="store_true")
    s.add_argument("--if-exists", action="store_true", help="管理表が無ければ何もしない（定期実行用）")
    s.add_argument("--force", action="store_true", help="返事待ちの提案があっても足す")
    s = sub.add_parser("pull"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--if-exists", action="store_true", help="管理表が無ければ何もしない（定期実行用）")
    sub.add_parser("status")
    s = sub.add_parser("render"); s.add_argument("--out", default=str(PROPOSALS))
    sub.add_parser("apply-changes")
    s = sub.add_parser("restyle"); s.add_argument("--out", help="書き出し先（省略：Excel の管理表ならその場所、Google なら out/artist_book/）")
    s.add_argument("--with-pending", action="store_true", help="閲覧のみの設定で貯まった提案（out/artist_book/pending.xlsx）も入れる")
    a = ap.parse_args()
    {"init": cmd_init, "import-json": cmd_import_json, "propose": cmd_propose, "propose-update": cmd_propose_update,
     "pull": cmd_pull, "status": cmd_status, "render": cmd_render, "apply-changes": cmd_apply_changes,
     "check": cmd_check, "adopt": cmd_adopt, "restyle": cmd_restyle}[a.cmd](a)


if __name__ == "__main__":
    main()
