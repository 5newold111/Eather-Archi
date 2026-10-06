#!/usr/bin/env python3
"""
手元のファイル（設定書・解析シート・ブリーフ・選んだテイク・登録シート）を Supabase に登録する。

鍵が無いとき（既定）：SQL ファイルを out/supabase/ に書き出すだけ。Supabase の SQL Editor に貼れば登録できる。
鍵があるとき（--apply）：API で直接登録し、音源とジャケットを非公開の保管庫（バケット）へ上げる。

  seed        レーベル（子レーベル）とアーティスト全組を登録 / 更新
  references  解析シート（out/references/*.json など）を参考曲と解析に登録
  week        その週のブリーフ・テイク・リリース・ジャケットを登録（--week 必須）

使い方
  python scripts/supabase_sync.py seed                                   # → out/supabase/seed.sql
  python scripts/supabase_sync.py references --dir out/references        # → out/supabase/references.sql
  python scripts/supabase_sync.py week --week 2026-10-05                 # → out/supabase/week_2026-10-05.sql
  python scripts/supabase_sync.py week --week 2026-10-05 --apply         # 鍵を入れた後：直接登録＋音源アップロード

鍵（.env）：SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY（service_role は管理者用。画面やチャットに貼らない）
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import MAIN_LABEL, OUT, ROOT, all_artists, load_dotenv, read_json, step  # noqa: E402
from _supabase import Client, Ref, upsert_sql  # noqa: E402

load_dotenv()
WEEKDAY = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
AXES = {"shape", "quality", "light", "time", "self"}
SLOTS = {"lyrics", "worldview", "instruments", "performance", "structure", "harmony", "groove", "phrase",
         "vocal_main", "vocal_sub1", "vocal_sub2"}


def as_date(v) -> str | None:
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        return None


def artist_ref(slug: str) -> Ref:
    return Ref.of("artists", slug=slug)


# ---------------------------------------------------------------------------
# 行を組み立てる（SQL でも API でも同じ行を使う）
# ---------------------------------------------------------------------------
def seed_rows() -> list[tuple[str, dict, list[str]]]:
    rows = []
    for p in sorted((ROOT / "templates" / "labels").glob("*.json")):
        if p.stem.startswith("_"):
            continue
        lab = read_json(p)
        acc = lab.get("accounts") or {}
        rows.append(("labels", {
            "slug": lab["slug"], "name": lab["name"], "scene": lab.get("scene", ""),
            "sound_center": lab.get("sound_center", []), "expansion_path": lab.get("expansion_path", []),
            "automation_tier": lab.get("automation_tier", "B"),
            "max_weekly_releases": lab.get("max_weekly_releases"),
            "release_weekday": WEEKDAY.get(str(lab.get("release_weekday", "wednesday")).lower(), 3),
            "release_hour_et": int(lab.get("release_hour_et", 17)),
            "distrokid_account": acc.get("distrokid_email") or None,   # 識別名だけ（メールアドレスは入れない運用）
            "spotify_team": acc.get("spotify_for_artists_team") or None,
            "sheet": {k: v for k, v in lab.items() if k != "artists"},
        }, ["slug"]))
    for a in all_artists():
        v, s, lyr = a.get("vocal") or {}, a.get("sound") or {}, a.get("lyrics") or {}
        dist = a.get("distribution") or {}
        main = a["label_slug"] == MAIN_LABEL
        sheet = {k: val for k, val in a.items() if k != "label_slug"}
        rows.append(("artists", {
            "slug": a["slug"], "name": a["name"],
            "label_id": None if main else Ref.of("labels", slug=a["label_slug"]),
            "axis": a.get("axis") if main and a.get("axis") in AXES else None,
            "formation": a.get("formation", "solo"),
            "persona_id": v.get("suno_persona_id"),
            "vocal_sex": v.get("sex", "none"), "vocal_range": v.get("range"),
            "fixed_timbre": bool(v.get("fixed_timbre", True)),
            "bpm_min": int(s.get("bpm_min", 90)), "bpm_max": int(s.get("bpm_max", 120)),
            "dna_tags": s.get("dna_tags", []),
            "lyric_language": lyr.get("primary_language", "en"),
            "trend_language_ok": bool(lyr.get("trend_language_ok", False)),
            "cadence": a.get("cadence", "weekly"),
            "debut_week": as_date(a.get("debut_week")),
            "composition_habits": a.get("composition_habits", {}),
            "profile": a.get("profile", {}),
            "concept_history": a.get("concept_history", []),
            "sheet": sheet,
            "spotify_uri": dist.get("spotify_uri"), "apple_artist_id": dist.get("apple_artist_id"),
            "distrokid_artist_id": dist.get("distrokid_artist_id"),
        }, ["slug"]))
    return rows


def reference_rows(folder: Path) -> list[tuple[str, dict, list[str]]]:
    rows = []
    for p in sorted(folder.glob("*.json")):
        if p.name.endswith(".measure.json"):
            continue
        sh = read_json(p)
        r = sh.get("reference") or {}
        if not r.get("title") or not r.get("artist_name"):
            print(f"   － {p.name}：reference.title / artist_name が無いので飛ばします")
            continue
        v = sh.get("vocal") or {}
        ref = Ref.of("reference_tracks", title=r["title"], artist_name=r["artist_name"])
        rows.append(("reference_tracks", {
            "title": r["title"], "artist_name": r["artist_name"], "isrc": r.get("isrc"),
            "release_year": r.get("release_year"), "language": r.get("language"),
            "source": r.get("source", "manual") if r.get("source") in ("own", "chart", "manual", "trend") else "manual",
            "acquired_from": r.get("acquired_from"), "tags": r.get("tags", []),
            "vocal_sex": v.get("sex") if v.get("sex") in ("female", "male", "mixed", "none") else None,
            "vocal_range": v.get("range") if v.get("range") in ("low", "mid", "high") else None,
        }, ["title", "artist_name"]))
        rows.append(("track_analyses", {
            "reference_track_id": ref, "version": int(sh.get("version", 1)),
            **{k: sh.get(k, [] if k == "phrases" else {}) for k in
               ("lyrics", "worldview", "instruments", "performance", "harmony", "groove", "structure",
                "phrases", "vocal", "visual_web")},
            "signature": sh.get("signature"), "analyzed_by": sh.get("analyzed_by"),
        }, ["reference_track_id", "version"]))
    return rows


def release_week_of(release_at_utc: str) -> str:
    et = datetime.fromisoformat(release_at_utc.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    return (et.date() - timedelta(days=(et.weekday()))).isoformat()


def week_rows(week: str, with_sources: bool) -> tuple[list, list]:
    """戻り値：(行のリスト, アップロードするファイルのリスト)"""
    rows, uploads = [], []
    briefs = sorted(p for p in (OUT / "briefs").glob(f"{week}_*.json") if "." not in p.stem)
    if not briefs:
        sys.exit(f"[エラー] out/briefs/{week}_*.json がありません")
    for bp in briefs:
        b = read_json(bp)
        slug = b["artist_slug"]
        a_ref = artist_ref(slug)
        brief_ref = Ref.of("briefs", week_start=b["week_start"], artist_id=a_ref)
        final_lyrics = bp.with_suffix(".lyrics_final.txt")
        step(f"{bp.name}：ブリーフ・テイク・リリースの行を組み立てています")
        rows.append(("briefs", {
            "week_start": b["week_start"], "artist_id": a_ref,
            "featured_artist_id": artist_ref(b["featured_artist_slug"]) if b.get("featured_artist_slug") else None,
            "title_candidates": b.get("title_candidates", []),
            "vocal_blend": b.get("vocal_blend", {}), "phrase_transform": b.get("phrase_transform", {}),
            "suno_style_prompt": b.get("suno_style_prompt") or None,
            "suno_lyrics": final_lyrics.read_text(encoding="utf-8") if final_lyrics.exists() else (b.get("suno_lyrics") or None),
            "trend_language": b.get("trend_language"), "generated_by": b.get("generated_by"),
        }, ["week_start", "artist_id"]))
        if with_sources:
            for slot, s in (b.get("slots") or {}).items():
                if slot in SLOTS and s and s.get("title"):
                    rows.append(("brief_sources", {
                        "brief_id": brief_ref, "slot": slot,
                        "reference_track_id": Ref.of("reference_tracks", title=s["title"], artist_name=s["artist_name"]),
                        "weight": round(min(1.0, float(s.get("weight", 1.0))), 2),
                    }, ["brief_id", "slot"]))

        sel_path = OUT / "takes" / bp.stem / "selection.json"
        sel = read_json(sel_path) if sel_path.exists() else {}
        chosen = (sel.get("decision") or {}).get("selected")
        gen_ref = None
        takes = sorted(sel.get("takes", []), key=lambda t: t["take"] == chosen)  # 選んだテイクを最後に（1 曲 1 選択の制約）
        for t in takes:
            is_sel = t["take"] == chosen
            ext = Path(t["take"]).suffix
            path = f"{b['week_start']}/{slug}/take_{t['take_no']:02d}{ext}"
            fails = "、".join(t.get("hard_fail") or [])
            note = (sel["decision"].get("note") if is_sel else None) or (f"機械判定で除外：{fails}" if fails else f"点 {t.get('score')}")
            rows.append(("generations", {
                "brief_id": brief_ref, "take_no": int(t["take_no"]),
                "storage_path": f"generations/{path}" if is_sel else None,
                "duration_sec": int(round(t["measure"]["duration_sec"])), "selected": is_sel,
                "listening_note": note,
            }, ["brief_id", "take_no"]))
            if is_sel:
                gen_ref = Ref.of("generations", brief_id=brief_ref, take_no=int(t["take_no"]))
                uploads.append(("generations", path, Path(t["path"]), "audio/mpeg" if ext == ".mp3" else "audio/wav"))

        meta_path = OUT / "distrokid" / week / f"{slug}.metadata.json"
        if meta_path.exists():
            m = read_json(meta_path)
            master = ROOT / m["audio"] if m.get("audio") else None
            cover = ROOT / m["cover"] if m.get("cover") else None
            rel_week = release_week_of(m["release_at_utc"])
            mpath = f"{slug}/{rel_week}/master.wav"
            cpath = f"{slug}/{rel_week}/cover_final.jpg"
            status = m.get("status", "planned")
            if status == "planned" and master and master.exists():
                status = "mastered"
            title = m.get("title") or "（未定）"
            if re.search(r"\b(feat|ft)\.?\s", title, re.I):
                sys.exit(f"[停止] {slug} のタイトルに feat. が入っています（データベースでも拒否されます）")
            rows.append(("releases", {
                "artist_id": a_ref, "brief_id": brief_ref, "generation_id": gen_ref,
                "featured_artist_id": artist_ref(m["featured_artist_slug"]) if m.get("featured_artist_slug") else None,
                "title": title, "release_week": rel_week, "release_at": m["release_at_utc"],
                "lyrics_language": m.get("lyrics_language") or "none", "ai_disclosed": True,
                "isrc": m.get("isrc"), "upc": m.get("upc"), "distrokid_release_id": m.get("distrokid_release_id"),
                "master_wav_path": f"masters/{mpath}" if master and master.exists() else None,
                "cover_path": f"covers/{cpath}" if cover and cover.exists() else None,
                "metadata_path": f"masters/{slug}/{rel_week}/metadata.json",
                "status": status,
            }, ["brief_id"]))
            if master and master.exists():
                uploads.append(("masters", mpath, master, "audio/wav"))
            if cover and cover.exists():
                uploads.append(("covers", cpath, cover, "image/jpeg"))
            uploads.append(("masters", f"{slug}/{rel_week}/metadata.json", meta_path, "application/json"))
        else:
            print(f"   － {slug}：登録シート（distrokid_sheet.py）がまだ無いので、リリースは登録しません")
    return rows, uploads


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="手元のファイルを Supabase に登録する（鍵が無ければ SQL を書き出す）")
    ap.add_argument("command", choices=["seed", "references", "week"])
    ap.add_argument("--week", help="制作週の月曜（week のとき）")
    ap.add_argument("--dir", default=str(OUT / "references"), help="解析シートのフォルダ（references のとき）")
    ap.add_argument("--with-sources", action="store_true", help="ブリーフの枠と参考曲の対応（brief_sources）も登録")
    ap.add_argument("--apply", action="store_true", help="API で直接登録する（鍵が必要）")
    args = ap.parse_args()

    uploads: list = []
    if args.command == "seed":
        step("設定書（レーベル・アーティスト）を読み込んでいます")
        rows = seed_rows()
        name = "seed"
    elif args.command == "references":
        folder = Path(args.dir)
        if not folder.exists():
            sys.exit(f"[エラー] フォルダがありません: {folder}")
        step(f"解析シートを読み込んでいます: {folder}")
        rows = reference_rows(folder)
        name = "references"
    else:
        if not args.week:
            sys.exit("[エラー] --week を指定してください（例：--week 2026-10-05）")
        rows, uploads = week_rows(args.week, args.with_sources)
        name = f"week_{args.week}"

    sql_path = OUT / "supabase" / f"{name}.sql"
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    body = ["-- supabase_sync.py が書き出した SQL。Supabase の SQL Editor に貼って実行できます", "begin;"]
    body += [upsert_sql(t, r, c) for t, r, c in rows]
    body += ["commit;", ""]
    sql_path.write_text("\n".join(body), encoding="utf-8")
    counts: dict[str, int] = {}
    for t, _, _ in rows:
        counts[t] = counts.get(t, 0) + 1
    step(f"SQL を書き出しました: {sql_path.relative_to(ROOT)}（" + "、".join(f"{k} {v} 行" for k, v in counts.items()) + "）")
    if uploads:
        print("   アップロード予定のファイル:")
        for bucket, path, f, _ in uploads:
            print(f"     {bucket}/{path}  ← {f.relative_to(ROOT) if f.resolve().is_relative_to(ROOT) else f}")

    if not args.apply:
        print("   （--apply を付けると、鍵を使って直接登録・アップロードします）")
        return
    cl = Client()
    if not cl.ready:
        sys.exit("[停止] SUPABASE_URL と SUPABASE_SERVICE_ROLE_KEY が .env にありません。"
                 "鍵の一括設定のときに  python scripts/set_key.py SUPABASE_URL  などで入れてください。"
                 "それまでは上の SQL を SQL Editor に貼れば同じ内容を登録できます")
    for i, (t, r, c) in enumerate(rows, 1):
        step(f"[{i}/{len(rows)}] {t} に登録しています")
        cl.upsert(t, r, c)
    for bucket, path, f, ctype in uploads:
        step(f"保管庫へアップロードしています: {bucket}/{path}")
        cl.upload(bucket, path, f, ctype)
    step("登録が終わりました")


if __name__ == "__main__":
    main()
