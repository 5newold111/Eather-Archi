#!/usr/bin/env python3
"""
成長分析の「方針転換の提案」（out/growth/pivots.json）を設定書に反映する（docs/06_growth.md「方針転換」）。

  レベル 1（音の微調整）：BPM の範囲を ±5 ずらす。参考曲の枠の組み合わせを変える・トレンド曲の枠を 3 にするのは
                         次週の割り当てが hints.json を読んで自動で行う
  レベル 2（コンセプト転換）：Claude が「隣の場面」への転換案を作る → 場面・空間・光・色・素材・カメラ・
                         作曲の癖 1 項目・DNA タグ・歌詞のテーマだけを書き換える（名前・声・生まれ育ち・好みは変えない）
                         → concept_version を +1 → アーティスト写真とロゴの撮り直し候補を作る（選ぶのは毎回オーナー）
  2 回目のレベル 2：その組を隔週に落とす（空いた枠は expand_label.py が新しい組で埋める）。3 回目は休止

  ・転換から 8 週は再判定しない（analyze_growth.py が見ている）
  ・同じ週の同じ提案は 1 回だけ適用する（out/growth/applied_pivots.json に記録）

使い方
  python scripts/apply_pivot.py --from-growth                          # 定期実行（木曜、成長分析のあと）
  python scripts/apply_pivot.py --artist light --level 2 --reason "8 週連続で下降"   # 手で
  python scripts/apply_pivot.py --from-growth --dry-run                # 何が変わるかだけ表示
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _claude import available, call_json, dry_run_file  # noqa: E402
from _common import MAIN_LABEL, OUT, ROOT, load_artist, monday_of, read_json, save_artist, step, write_json  # noqa: E402

PIVOT_SYSTEM = """You plan a concept pivot for a virtual music act at EtherArchi (AI-generated music x spatial design).
The act is not growing. Move its SCENE one step sideways to an adjacent scene (e.g. morning highway -> evening drive
home), and re-stage its visual world to match. The person does not change: never touch the name, the voice, the
origin, the likes. Change only the fields in the schema, and change exactly one composition habit. Write descriptive
values in Japanese like the current sheet; dna_tags in short lowercase English. summary_ja explains the pivot to the
owner in 2-3 Japanese sentences. Keep the label's tone: refined but approachable, never theatrical."""

PIVOT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["new_scene", "visual", "composition_habit_change", "dna_tags", "themes", "summary_ja"],
    "properties": {
        "new_scene": {"type": "string"},
        "visual": {"type": "object", "additionalProperties": False,
                   "required": ["space", "light", "materials", "palette", "camera", "cover_series_rule"],
                   "properties": {"space": {"type": "string"}, "light": {"type": "string"},
                                  "materials": {"type": "array", "items": {"type": "string"}},
                                  "palette": {"type": "array", "items": {"type": "string"}},
                                  "camera": {"type": "string"}, "cover_series_rule": {"type": "string"}}},
        "composition_habit_change": {"type": "object", "additionalProperties": False,
                                     "required": ["key", "new_value", "why"],
                                     "properties": {"key": {"type": "string"}, "new_value": {"type": "string"},
                                                    "why": {"type": "string"}}},
        "dna_tags": {"type": "array", "items": {"type": "string"}},
        "themes": {"type": "array", "items": {"type": "string"}},
        "summary_ja": {"type": "string"},
    },
}


def history_of(a: dict) -> dict:
    h = a.get("concept_history")
    if not isinstance(h, dict):
        h = {"concept_version": 1, "history": list(h or [])}
    h.setdefault("concept_version", 1)
    h.setdefault("history", [])
    a["concept_history"] = h
    return h


def level1(a: dict, reason: str, dry: bool) -> dict:
    s = a.setdefault("sound", {})
    h = history_of(a)
    prev = [x for x in h["history"] if x.get("level") == 1]
    shift = -5 if prev and prev[-1].get("bpm_shift", 0) > 0 else 5        # 前回上げたら今回は下げる
    lo, hi = int(s.get("bpm_min", 90)) + shift, int(s.get("bpm_max", 120)) + shift
    lo, hi = max(40, lo), min(220, hi)
    change = {"date": date.today().isoformat(), "level": 1, "reason": reason, "bpm_shift": shift,
              "changes": {"sound.bpm_min": [s.get("bpm_min"), lo], "sound.bpm_max": [s.get("bpm_max"), hi]},
              "note": "枠の組み合わせを大きく変える・トレンド曲の枠を 3 にするのは次週の割り当てで自動"}
    if not dry:
        s["bpm_min"], s["bpm_max"] = lo, hi
        h["history"].append(change)
        save_artist(a)
    return change


def level2(a: dict, reason: str, dry: bool) -> dict | None:
    h = history_of(a)
    past_l2 = [x for x in h["history"] if x.get("level") == 2]
    if past_l2:   # 1 回目の転換で戻らなかった
        new = "biweekly" if a.get("cadence", "weekly") == "weekly" else "paused"
        change = {"date": date.today().isoformat(), "level": 2, "reason": reason,
                  "changes": {"cadence": [a.get("cadence", "weekly"), new]},
                  "note": "転換しても戻らなかったので、制作の頻度を落とす（空いた枠は新しい組へ）"}
        if not dry:
            a["cadence"] = new
            h["history"].append(change)
            save_artist(a)
        return change

    persona = a.get("persona") or {}
    scene_key = "drive_scene" if "drive_scene" in persona else "scene"
    current = {"scene": persona.get(scene_key), "visual": {k: (a.get("visual") or {}).get(k) for k in PIVOT_SCHEMA["properties"]["visual"]["required"]},
               "composition_habits": a.get("composition_habits"), "dna_tags": (a.get("sound") or {}).get("dna_tags"),
               "themes": (a.get("lyrics") or {}).get("themes"), "genres": (a.get("sound") or {}).get("genres"),
               "persona_story": persona.get("story")}
    user = json.dumps({"artist_name": a["name"], "reason": reason, "current": current}, ensure_ascii=False, indent=1)
    if not available():
        f = OUT / "growth" / f"pivot_{a['slug']}_{date.today()}.prompt.md"
        dry_run_file(f, PIVOT_SYSTEM, user, PIVOT_SCHEMA)
        print(f"   ANTHROPIC_API_KEY が無いので、転換案の依頼文を {f.relative_to(ROOT)} に書き出しました（未適用）")
        return None
    step(f"{a['name']}：Claude が隣の場面への転換案を作っています")
    plan, msg = call_json(PIVOT_SYSTEM, user, PIVOT_SCHEMA)
    if not plan:
        print(f"   ✕ 転換案を作れませんでした：{msg}")
        return None
    hk = plan["composition_habit_change"]["key"]
    if hk not in (a.get("composition_habits") or {}):
        print(f"   ✕ 作曲の癖の項目名 {hk} が設定書に無いので、癖の変更は見送ります")
        hk = None
    v = a.setdefault("visual", {})
    changes = {f"persona.{scene_key}": [persona.get(scene_key), plan["new_scene"]]}
    changes.update({f"visual.{k}": [v.get(k), plan["visual"][k]] for k in plan["visual"]})
    if hk:
        changes[f"composition_habits.{hk}"] = [a["composition_habits"][hk], plan["composition_habit_change"]["new_value"]]
    changes["sound.dna_tags"] = [(a.get("sound") or {}).get("dna_tags"), plan["dna_tags"]]
    changes["lyrics.themes"] = [(a.get("lyrics") or {}).get("themes"), plan["themes"]]
    version = int(h["concept_version"]) + 1
    change = {"version": version, "date": date.today().isoformat(), "level": 2, "reason": reason,
              "summary_ja": plan["summary_ja"], "changes": changes, "photos_reshot": True}
    if dry:
        return change
    persona[scene_key] = plan["new_scene"]
    a["persona"] = persona
    v.update(plan["visual"])
    if hk:
        a["composition_habits"][hk] = plan["composition_habit_change"]["new_value"]
    a.setdefault("sound", {})["dna_tags"] = plan["dna_tags"]
    a.setdefault("lyrics", {})["themes"] = plan["themes"]
    h["concept_version"] = version
    h["history"].append(change)
    save_artist(a)
    step(f"{a['name']}：アーティスト写真とロゴの撮り直し候補を作っています（選ぶのはオーナー）")
    cmd = [sys.executable, str(ROOT / "scripts" / "generate_visuals.py"), "--artist", a["slug"], "--kind", "reshoot",
           "--pivot-reason", f"{reason}。{plan['summary_ja']}"]
    if a["label_slug"] != MAIN_LABEL:
        cmd += ["--label", a["label_slug"]]
    subprocess.run(cmd, check=False)
    return change


def main() -> None:
    ap = argparse.ArgumentParser(description="方針転換を設定書に反映する")
    ap.add_argument("--from-growth", action="store_true", help="out/growth/pivots.json の提案を適用")
    ap.add_argument("--artist")
    ap.add_argument("--label")
    ap.add_argument("--level", type=int, choices=[1, 2])
    ap.add_argument("--reason", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    todo: list[tuple[str, int, str]] = []
    applied_path = OUT / "growth" / "applied_pivots.json"
    applied = read_json(applied_path) if applied_path.exists() else {}
    week = monday_of(date.today()).isoformat()
    if a.from_growth:
        p = OUT / "growth" / "pivots.json"
        if not p.exists():
            step("方針転換の提案はありません（成長分析がまだ）")
            return
        for slug, v in read_json(p).items():
            if applied.get(f"{week}|{slug}"):
                continue
            todo.append((slug, int(v["level"]), v.get("reason", "")))
    elif a.artist and a.level:
        todo.append((a.artist, a.level, a.reason or "オーナーの判断"))
    else:
        sys.exit("[エラー] --from-growth か、--artist と --level を指定してください")
    if not todo:
        step("今週適用する方針転換はありません")
        return

    log = []
    for slug, level, reason in todo:
        art = load_artist(slug, a.label)
        step(f"{art['name']}：方針転換 レベル {level}（{reason}）" + ("　※ドライラン" if a.dry_run else ""))
        change = level1(art, reason, a.dry_run) if level == 1 else level2(art, reason, a.dry_run)
        if change:
            for k, (old, new) in change.get("changes", {}).items():
                print(f"     {k}: {json.dumps(old, ensure_ascii=False)[:60]} → {json.dumps(new, ensure_ascii=False)[:60]}")
            if not a.dry_run:
                applied[f"{week}|{slug}"] = change["date"]
            log.append({"artist": slug, **change})
    if not a.dry_run:
        write_json(applied_path, applied)
        write_json(OUT / "growth" / f"pivots_applied_{week}.json", log)


if __name__ == "__main__":
    main()
