#!/usr/bin/env python3
"""
レーベルの拡大を自動で回す（docs/06_growth.md・docs/07_sublabels.md のルール）。

  check            各レーベルの週の曲数と、月 1 組の追加の時期を確かめる
  check --apply    ・週の曲数が上限（既定 8）を超えたら、デビューが古い組から隔週（biweekly）に切り替える
                   ・追加の時期が来たレーベルには、Claude が新しい組を丸ごと考えて、アーティスト台帳に「提案」の行で入れる
                     （自動で決めるのは提案まで。採用するかはオーナー。採用 → artist_book.py pull のあと、
                     次の check --apply で 4 週間以上あとの「1 日のある週」にデビューを予約する）
                   ・台帳で返事待ちの提案があるレーベルには、新しい提案を足さない
                   ・全部のレーベルが上限に達していたら、新しい子レーベルの候補をレポートに書く（アカウント作成は人）
  propose          今すぐ 1 組を台帳に提案する（--label を指定。--legacy で旧方式＝設定書を直接下書きして予約）
  cancel           予約した新人を取り消す（--artist を指定）

  追加の条件
    本体（drive）     : 毎月（1 日のある週ごとに 1 組。予約済みの新人がいる間は増やさない）
    子レーベル        : 立ち上げから 3 か月たち、1,000 再生に届いた曲の割合が 50% を超えていること

使い方
  python scripts/expand_label.py check                 # 見るだけ
  python scripts/expand_label.py check --apply         # 定期実行（毎月 1 日）
  python scripts/expand_label.py propose --label focus
  python scripts/expand_label.py cancel --artist focus_new_slug
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _claude import available, call_json_free, dry_run_file  # noqa: E402
from _common import (MAIN_LABEL, OUT, ROOT, all_artists, as_date, load_artist, load_label, monday_of,  # noqa: E402
                     next_debut_week, notify, read_json, save_artist, step, write_json)

DEFAULT_CAP = 8
SUBLABEL_MIN_DAYS = 90
SUBLABEL_REACH = 0.5
DEBUT_LEAD_WEEKS = 4      # 下書きからデビューまで（名前確認・ロゴと写真・オーナーの見直しの時間）


def labels() -> list[str]:
    return [MAIN_LABEL] + [p.stem for p in sorted((ROOT / "templates" / "labels").glob("*.json"))
                           if not p.stem.startswith("_") and read_json(p).get("artists")]


def members(label: str) -> list[dict]:
    return [a for a in all_artists() if a["label_slug"] == label]


def load_of(arts: list[dict]) -> float:
    w = {"weekly": 1.0, "biweekly": 0.5, "paused": 0.0}
    return sum(w.get(a.get("cadence", "weekly"), 1.0) for a in arts if as_date(a.get("debut_week")))


def reach_rate(label_arts: list[dict]) -> tuple[float | None, int]:
    """1,000 再生に届いた曲の割合（配信サービスの数字だけ）"""
    p = OUT / "metrics" / "daily.csv"
    if not p.exists():
        return None, 0
    slugs = {a["slug"] for a in label_arts}
    total: dict[str, int] = {}
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["artist_slug"] in slugs and r.get("kind", "dsp") != "sns":
                total[r["release_id"]] = total.get(r["release_id"], 0) + int(float(r["streams"] or 0))
    if not total:
        return None, 0
    return sum(1 for v in total.values() if v >= 1000) / len(total), len(total)


def growth_trends() -> dict[str, str]:
    p = OUT / "growth" / "hints.json"
    return {k: v.get("artist_trend", "new") for k, v in read_json(p).items()} if p.exists() else {}


def status(label: str, today: date) -> dict:
    arts = members(label)
    lab = load_label(label)
    cap = int(lab.get("max_weekly_releases") or DEFAULT_CAP)
    debuted = [a for a in arts if as_date(a.get("debut_week")) and as_date(a["debut_week"]) <= today]
    scheduled = [a for a in arts if as_date(a.get("debut_week")) and as_date(a["debut_week"]) > today]
    first = min((as_date(a["debut_week"]) for a in debuted), default=None)
    load = load_of(arts)
    due, why = False, ""
    if not debuted:
        why = "まだ立ち上げ前"
    elif scheduled:
        why = f"デビュー予約あり（{', '.join(a['name'] for a in scheduled)}）"
    elif load + 1 > cap and not any(a.get("cadence") == "weekly" for a in debuted):
        why = "週の曲数が上限で、隔週にできる組も無い"
    elif label != MAIN_LABEL:
        rate, n = reach_rate(debuted)
        if (today - first).days < SUBLABEL_MIN_DAYS:
            why = f"立ち上げから {(today - first).days} 日（子レーベルは 3 か月たってから追加）"
        elif rate is None or rate <= SUBLABEL_REACH:
            why = f"1,000 再生到達率 {('—' if rate is None else f'{rate:.0%}')}（{n} 曲。50% を超えたら追加）"
        else:
            due, why = True, f"1,000 再生到達率 {rate:.0%}。月 1 組を追加"
    else:
        due, why = True, "本体は毎月 1 組を追加"
    return {"label": label, "name": lab.get("name"), "cap": cap, "load": load, "debuted": len(debuted),
            "scheduled": len(scheduled), "due": due, "why": why}


def switch_to_biweekly(label: str, cap: int) -> list[str]:
    """週の曲数が上限を超えたら、デビューが古い組から隔週に"""
    arts = sorted([a for a in members(label) if as_date(a.get("debut_week"))], key=lambda a: a["debut_week"])
    changed = []
    while load_of(arts) > cap:
        target = next((a for a in arts if a.get("cadence", "weekly") == "weekly"), None)
        if not target:
            break
        target["cadence"] = "biweekly"
        target.setdefault("cadence_history", []).append({"date": date.today().isoformat(), "to": "biweekly",
                                                         "reason": f"週の曲数が上限 {cap} を超えたため（古い組から隔週）"})
        save_artist(target)
        changed.append(target["name"])
    return changed


# ---------------------------------------------------------------------------
# 新しい組の下書き
# ---------------------------------------------------------------------------
DRAFT_SYSTEM = """You design virtual music artists for EtherArchi, a label that pairs AI-generated music with spatial
design. Every artist is defined by a scene of daily life (driving, sleep, morning coffee, focus, walking/running) and
by space: shape, quality, light, time and the self. The tone is refined but approachable - never theatrical.

Write a complete artist sheet as ONE JSON object with exactly the same keys and nesting as the template you are given
(keep keys that start with "_" out of your answer). Rules:
- The new act must not overlap the existing acts listed: different vocal range/sex or formation, a different corner of
  the scene, different dna_tags emphasis. Move one step along the label's expansion_path.
- Japanese for descriptive text (like the existing sheets); English for bio_en, genres and dna_tags.
- name: an original, easy-to-say English act name; name_candidates: 10 options including the chosen one.
- persona.formation_story: modelled on a real-world PATTERN of how acts form (describe the pattern, never name the
  real act). Real artist names may appear ONLY in profile.favorite_artists_real.
- vocal.signature_techniques.tics: 2-3 recurring habits that can be written as Suno tags.
- collab.preferred_partners: 1-2 slugs from the existing acts (prefer ones listed as "up").
- slug: lowercase ascii with underscores, prefixed with the label slug for sub-labels (e.g. focus_tapeloop).
- name_status "draft", debut_week null, cadence "weekly", distribution ids null.
Return only the JSON object."""


def existing_summary(label: str) -> list[dict]:
    out = []
    for a in members(label):
        v, s = a.get("vocal") or {}, a.get("sound") or {}
        out.append({"slug": a["slug"], "name": a["name"], "formation": a.get("formation"), "vocal_sex": v.get("sex"),
                    "vocal_range": v.get("range"), "genres": s.get("genres"), "dna_tags": s.get("dna_tags"),
                    "scene": (a.get("persona") or {}).get("drive_scene") or (a.get("persona") or {}).get("scene")})
    return out


def clean(d):
    if isinstance(d, dict):
        return {k: clean(v) for k, v in d.items() if not str(k).startswith("_")}
    if isinstance(d, list):
        return [clean(x) for x in d]
    return d


def validate_draft(d: dict, template: dict, label: str) -> list[str]:
    probs = []
    for k, v in template.items():
        if k.startswith("_"):
            continue
        if k not in d:
            probs.append(f"項目 {k} が無い")
        elif isinstance(v, dict):
            probs += [f"項目 {k}.{kk} が無い" for kk in v if not kk.startswith("_") and kk not in (d.get(k) or {})]
    slugs = {a["slug"] for a in all_artists()}
    names = {a["name"].lower() for a in all_artists()}
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", str(d.get("slug", ""))):
        probs.append(f"slug の形が不正（{d.get('slug')}）")
    if d.get("slug") in slugs:
        probs.append(f"slug {d.get('slug')} は既にある")
    if str(d.get("name", "")).lower() in names:
        probs.append(f"名前 {d.get('name')} は既にある")
    real = [n for n in (d.get("profile") or {}).get("favorite_artists_real", []) if n]
    body = json.dumps({k: v for k, v in d.items() if k != "profile"}, ensure_ascii=False).lower()
    probs += [f"実在アーティスト名（{n}）が profile 以外に入っている" for n in real if n.lower() in body]
    return probs


def draft_artist(label: str, direction: str | None) -> dict | None:
    lab = load_label(label)
    tmpl_path = ROOT / "templates" / "artists" / "_template.json"
    template = read_json(tmpl_path)
    path = lab.get("expansion_path") or []
    n_extra = max(0, len(members(label)) - (5 if label == MAIN_LABEL else 3))
    direction = direction or (path[n_extra % len(path)] if path else "")
    trend = growth_trends()
    user = json.dumps({
        "label": {k: lab.get(k) for k in ("slug", "name", "scene", "sound_center", "expansion_path", "scene_spec")},
        "next_direction": direction,
        "existing_acts": [{**e, "trend": trend.get(e["slug"], "new")} for e in existing_summary(label)],
        "template": clean(template),
    }, ensure_ascii=False, indent=1)
    if not available():
        f = OUT / "expansion" / f"{date.today()}_{label}.prompt.md"
        dry_run_file(f, DRAFT_SYSTEM, user)
        print(f"   ANTHROPIC_API_KEY が無いので、依頼文を {f.relative_to(ROOT)} に書き出しました")
        return None
    step(f"{lab.get('name')}：新しい組の設定書を Claude が下書きしています（方向：{direction}）")
    d, msg = call_json_free(DRAFT_SYSTEM, user)
    if not d:
        print(f"   ✕ 下書きに失敗：{msg}")
        return None
    probs = validate_draft(d, template, label)
    if probs:
        bad = OUT / "expansion" / f"{date.today()}_{label}_rejected.json"
        write_json(bad, {"problems": probs, "draft": d})
        print(f"   ✕ 下書きに問題があるので使いません：{' / '.join(probs[:4])}（{bad.relative_to(ROOT)}）")
        return None
    if label != MAIN_LABEL:
        d.pop("axis", None)
    else:
        d["axis"] = None   # 本体の 5 軸は 1 組ずつ。追加の組は軸なし
    d.update(name_status="draft", debut_week=None, cadence="weekly", label_slug=label,
             expansion={"date": date.today().isoformat(), "direction": direction, "by": "expand_label.py"})
    d.setdefault("concept_history", {"concept_version": 1, "history": []})
    return d


def book_pending(label: str) -> list[str]:
    """台帳で返事待ち（状態＝提案）の組の名前"""
    try:
        from _artist_book import open_book
        b = open_book()
        if hasattr(b, "path") and not b.path.exists():
            return []
        data = b.read()
    except Exception as e:   # 台帳が読めなくても拡大の確認は止めない
        print(f"   （台帳を読めませんでした：{e}）")
        return []
    ja = "本体" if label == MAIN_LABEL else label
    return [r.get("name", r.get("id")) for r in data["acts"]
            if str(r.get("status") or "").strip() == "提案" and (r.get("label") or "本体") in (ja, label)]


def propose_to_book(label: str, direction: str | None) -> str | None:
    """Claude が新しい組を丸ごと考えて台帳に『提案』として入れる（artist_book.py propose）"""
    lab = load_label(label)
    path = lab.get("expansion_path") or []
    n_extra = max(0, len(members(label)) - (5 if label == MAIN_LABEL else 3))
    direction = direction or (path[n_extra % len(path)] if path else "")
    cmd = [sys.executable, str(ROOT / "scripts" / "artist_book.py"), "propose",
           "--label", "本体" if label == MAIN_LABEL else label, "--note", f"広げる方向：{direction}" if direction else ""]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.rstrip())
    if r.returncode != 0:
        print(r.stderr[-600:])
        return None
    m = re.search(r"『(.+?)』を提案として台帳に入れました", r.stdout)
    return m.group(1) if m else ("（依頼文だけ書き出し）" if "依頼文" in r.stdout else None)


def adopted_unbooked(label: str) -> list[dict]:
    """台帳で採用されて設定書になったが、まだデビュー週が決まっていない組（取り消し・休止は除く）"""
    return [a for a in members(label) if a.get("source") and not as_date(a.get("debut_week"))
            and a.get("cadence", "weekly") != "paused"]


def schedule_debut(artist: dict) -> date:
    # デビューは毎月 1 日のある週。下書きから 4 週間（名前確認・ロゴと写真・取り消しの猶予）以上あとの最初のデビュー週
    debut = next_debut_week(monday_of(date.today()) + timedelta(weeks=DEBUT_LEAD_WEEKS))
    save_artist(artist)
    cmd = [sys.executable, str(ROOT / "scripts" / "debut.py"), "--artist", artist["slug"], "--launch-week", debut.isoformat()]
    if artist["label_slug"] != MAIN_LABEL:
        cmd += ["--label", artist["label_slug"]]
    subprocess.run(cmd, check=False)
    return debut


def main() -> None:
    ap = argparse.ArgumentParser(description="レーベルの拡大（月 1 組・隔週への切り替え）")
    ap.add_argument("command", choices=["check", "propose", "cancel"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--label")
    ap.add_argument("--artist")
    ap.add_argument("--direction", help="propose：広げる方向を手で指定")
    ap.add_argument("--legacy", action="store_true", help="propose：台帳を通さず設定書を直接下書きして予約する（旧方式）")
    a = ap.parse_args()
    today = date.today()

    if a.command == "cancel":
        if not a.artist:
            sys.exit("[エラー] --artist を指定してください")
        art = load_artist(a.artist, a.label)
        art.update(debut_week=None, cadence="paused")
        save_artist(art)
        step(f"{art['name']} のデビュー予約を取り消しました（設定書は残しています。消すときは手で削除）")
        return

    if a.command == "propose":
        if not a.label:
            sys.exit("[エラー] --label を指定してください（drive / sleep / morning / focus / move）")
        if not a.legacy:
            name = propose_to_book(a.label, a.direction)
            if name:
                step(f"新しい組『{name}』を台帳に提案しました。採用するなら artist_book.py adopt <ID> → pull")
            return
        d = draft_artist(a.label, a.direction)
        if d:
            debut = schedule_debut(d)
            step(f"新しい組 {d['name']}（{d['slug']}）を下書きし、{debut} の週にデビューを予約しました")
        return

    rows = [status(l, today) for l in labels()]
    lines = [f"# レーベル拡大の確認（{today}）", "", "| レーベル | 週の曲数 / 上限 | デビュー済み | 予約 | 追加 | 理由 |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['name']}（{r['label']}） | {r['load']:g} / {r['cap']} | {r['debuted']} | {r['scheduled']} | "
                     f"{'○' if r['due'] else '—'} | {r['why']} |")
        print(f"  {r['name']:<12} 週 {r['load']:g}/{r['cap']}  {'追加の時期' if r['due'] else '—'}：{r['why']}")
    actions = []
    if a.apply:
        for r in rows:
            changed = switch_to_biweekly(r["label"], r["cap"])
            if changed:
                actions.append(f"{r['name']}：{', '.join(changed)} を隔週に切り替え")
            # 台帳で採用された新しい組は、ここでデビューを予約する（採用＝オーナーの決定）
            for art in adopted_unbooked(r["label"]):
                debut = schedule_debut(art)
                notify("EtherArchi：採用した組のデビューを予約しました", f"{art['name']}（{r['name']}）を {debut} の週に予約")
                actions.append(f"{r['name']}：台帳で採用された {art['name']}（{art['slug']}）を {debut} の週にデビュー予約"
                               f"（取り消し：expand_label.py cancel --artist {art['slug']}）")
            if r["due"]:
                pending = book_pending(r["label"])
                if pending:
                    actions.append(f"{r['name']}：台帳に返事待ちの提案があるので、新しい提案は足しません（{', '.join(pending)}）")
                    continue
                name = propose_to_book(r["label"], None)
                if name:
                    notify("EtherArchi：新しい組の提案があります",
                           f"{name}（{r['name']}）を台帳に提案しました。採用するかを決めてください")
                    actions.append(f"{r['name']}：新しい組『{name}』を台帳に提案（採用するなら artist_book.py adopt <ID> → pull。"
                                   "次の check --apply でデビューを予約）")
    launched = [r for r in rows if r["debuted"]]
    if launched and all(r["load"] >= r["cap"] for r in launched):
        trend = growth_trends()
        best = max(launched, key=lambda r: sum(1 for x in members(r["label"]) if trend.get(x["slug"]) == "up"))
        actions.append(f"全レーベルが上限です。伸びている組が多い「{best['name']}」の場面の隣に、新しい子レーベルを作る時期です"
                       "（templates/labels/_template.json をコピー。配信アカウントは人が作る）")
    lines += ["", "## 実行したこと・提案", ""] + ([f"- {x}" for x in actions] or ["- なし"])
    out = OUT / "expansion" / f"{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for x in actions:
        step(x)
    step(f"レポート: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
