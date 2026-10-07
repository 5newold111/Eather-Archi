#!/usr/bin/env python3
"""
ビジュアル自動生成：ロゴ・顔を出さないアーティスト写真・ジャケットの候補を作り、採点し、選ぶ

流れ：
  1. 設定書（visual / persona / profile）とブリーフから、画像の指示文（プロンプト）を組み立てる
     - 実在アーティスト名（favorite_artists_real、influences.name）は絶対に渡さない
     - 顔を出さない方法（face_concealment）を必ず指示文に含める
  2. 画像 API で候補を生成（OPENAI_API_KEY があれば ChatGPT 側の gpt-image-1。無ければ指示文だけ書き出す＝ドライラン）
  3. Claude が画像を見て採点（5 観点 1〜5 点）し、顔・他社ロゴ・余計な文字などの禁止ルールに当たる候補を外す
     （ANTHROPIC_API_KEY が無いときは仮の点）
  4. 重み付きの合計点（templates/visual_criteria.json の重み）。5 回の判断がそろったら、Claude が重みと好みを学び直す
  5. 最初の 5 回はオーナーに聞く（review.md を書き出し、--choose で判断を記録 → 基準に反映）
     6 回目以降は自動選択。1 位と 2 位の差が小さいときだけ聞く

使い方：
  python3 scripts/generate_visuals.py --artist light --kind debut              # ロゴ 6 ＋ 写真 8（ドライラン可）
  python3 scripts/generate_visuals.py --artist light --kind cover --brief out/briefs/2026-10-05_light.json
  python3 scripts/generate_visuals.py --label sleep --artist sleep_drone --kind debut
  python3 scripts/generate_visuals.py --artist light --kind debut --choose logo:3 --reason "余白が多く、線が 1 本なのが良い"
  python3 scripts/generate_visuals.py --artist light --kind reshoot --pivot-reason "2 か月連続 down。朝の高速から『夕方の帰路』へ転換"

標準ライブラリだけで動く（画像 API 呼び出しは urllib）。
"""
from __future__ import annotations
import argparse, base64, json, os, sys, urllib.error, urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRITERIA_PATH = ROOT / "templates" / "visual_criteria.json"

def load_dotenv(path: Path) -> None:
    """music-label/.env があれば読み込んで環境変数にする（既に設定済みのものは上書きしない）"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")


def ssl_context():
    """
    HTTPS の証明書検証に使う設定。
    Mac の python.org 版 Python は証明書を持っていないことがあり、CERTIFICATE_VERIFY_FAILED になる。
    certifi（pip install certifi）が入っていればそれを使い、無ければ標準の設定を使う。
    """
    import ssl
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_HINT = ("証明書の問題です（鍵は関係ありません）。次のどちらかで直ります：\n"
            "       A) pip3 install certifi   を実行してから再実行\n"
            "       B) Finder の「アプリケーション → Python 3.x → Install Certificates.command」をダブルクリック")


# 候補の枚数
N_LOGO, N_PHOTO, N_COVER = 6, 8, 6

# 画像 API に渡してはいけない語（実名・商標に寄るのを防ぐ）
BANNED_IN_PROMPT = ["feat.", "Spotify", "Apple Music", "TikTok", "Instagram"]


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------
def load_artist(slug: str, label: str | None) -> dict:
    if label:
        lab = json.loads((ROOT / "templates" / "labels" / f"{label}.json").read_text(encoding="utf-8"))
        for a in lab["artists"]:
            if a["slug"] == slug:
                a["label_slug"] = label; a["label_visual"] = lab.get("visual", {}); return a
        sys.exit(f"[エラー] レーベル {label} に {slug} がありません")
    p = ROOT / "templates" / "artists" / f"{slug}.json"
    if not p.exists():
        sys.exit(f"[エラー] 設定書がありません: {p}")
    a = json.loads(p.read_text(encoding="utf-8")); a["label_slug"] = "drive"; return a


def load_criteria() -> dict:
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


def save_criteria(c: dict) -> None:
    CRITERIA_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 指示文（プロンプト）
# ---------------------------------------------------------------------------
def scrub(text: str, artist: dict) -> str:
    """実名・禁止語を落とす。favorite_artists_real / influences.name は最初から使わないが、念のため検査する"""
    names = list(artist.get("profile", {}).get("favorite_artists_real", []))
    names += [i.get("name", "") for i in artist.get("persona", {}).get("influences", [])]
    for n in names + BANNED_IN_PROMPT:
        if n and n not in ("（bio のみ）",) and n in text:
            raise RuntimeError(f"指示文に渡してはいけない語が含まれています: {n}")
    return text


def visual_base(artist: dict) -> str:
    v = artist["visual"]
    pd = v.get("photo_direction", {})
    return (
        f"Setting: {v.get('space','')}. Light: {v.get('light','')}. Materials: {', '.join(v.get('materials', []))}. "
        f"Palette: {', '.join(v.get('palette', []))}. Camera: {v.get('camera','')}. "
        f"Taste: {pd.get('taste','')}. Composition: {pd.get('composition','')}. Brightness: {pd.get('brightness','')}, "
        f"contrast: {pd.get('contrast','')}, color temperature: {pd.get('color_temperature','')}. Background: {pd.get('background','')}."
    )


def concealment_clause(method: str) -> str:
    return (f"The person's face is never visible: {method}. No recognizable facial features, no eyes, no identifiable likeness. "
            "No text, no logos, no brand marks, no watermarks. Not a real person.")


def prompts_debut(artist: dict) -> list[dict]:
    v, lg = artist["visual"], artist["visual"].get("logo", {})
    fc = v.get("face_concealment", {})
    out = []
    # ロゴ：文字から起こす。既存ロゴは参照しない
    for i in range(N_LOGO):
        variant = ["minimal wordmark", "wordmark with a single geometric mark", "mark-only emblem, wordmark small beneath",
                   "wordmark with generous letter-spacing", "stacked wordmark", "wordmark integrated with the mark"][i]
        out.append({"kind": "logo", "no": i + 1, "prompt": scrub(
            f"Logo design for a music artist named '{artist['name']}'. {variant}. Concept: {lg.get('concept','')}. "
            f"Mark type: {lg.get('mark_type','')}. Color rule: {lg.get('color_rule','')}. Typography: {lg.get('typography','')}. "
            "Flat vector style, high contrast, legible at small size, centered on a plain background, no mockups, no extra text, "
            "original lettering (do not imitate any existing logo).", artist)})
    # 写真：主の方法 4 枚、副の方法 2 枚ずつ
    methods = [fc.get("primary", "seen from behind")] * 4 + [m for m in fc.get("secondary", [])[:2] for _ in range(2)]
    pd = v.get("photo_direction", {})
    for i, m in enumerate(methods[:N_PHOTO]):
        out.append({"kind": "photo", "no": i + 1, "method": m, "prompt": scrub(
            f"Artist photograph for a music act ({artist['formation']}; {len(artist['persona'].get('members', [])) or 1} member(s)). "
            f"{visual_base(artist)} Positioning: {pd.get('positioning','')}. Gesture: {pd.get('gesture','')}. "
            f"Location type: {pd.get('location_type','')}. {concealment_clause(m)} Photorealistic, editorial quality, square 1:1.", artist)})
    return out


def prompts_cover(artist: dict, brief: dict) -> list[dict]:
    v = artist["visual"]
    rule = v.get("cover_series_rule", "")
    theme = ", ".join(artist.get("lyrics", {}).get("themes", [])[:3])
    seed = brief.get("cover_prompt_seed") or ""
    title = (brief.get("title_candidates") or [""])[0]
    out = []
    variants = ["no text at all", "no text at all", "title only, small, matching the typography rule", "no text at all",
                "no text at all", "title only, small, matching the typography rule"]
    for i in range(N_COVER):
        out.append({"kind": "cover", "no": i + 1, "prompt": scrub(
            f"Album cover artwork, square 1:1, 3000x3000. {visual_base(artist)} Series rule: {rule}. "
            f"Song theme: {theme}. {seed} {variants[i]}"
            + (f" (title: '{title}')" if 'title only' in variants[i] and title else "") +
            ". No people's faces, no URLs, no social handles, no prices, no third-party logos or characters, no real buildings or signs, "
            "not explicit. Original artwork, not a copy of any existing cover.", artist)})
    return out


def prompts_reshoot(artist: dict, pivot_reason: str) -> list[dict]:
    """方針転換後の撮り直し。設定書はすでに新しい visual に書き換えられている前提"""
    ps = prompts_debut(artist)
    for p in ps:
        p["pivot_reason"] = pivot_reason
    return [p for p in ps if p["kind"] == "photo"] + [p for p in ps if p["kind"] == "logo"][:2]   # 写真 8 ＋ ロゴ微調整 2


# ---------------------------------------------------------------------------
# 画像生成（ChatGPT 側の画像 API）
# ---------------------------------------------------------------------------
def generate_image(prompt: str, out_path: Path, size: str = "1024x1024") -> bool:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return False
    body = json.dumps({"model": "gpt-image-1", "prompt": prompt, "size": size, "n": 1}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180, context=ssl_context()) as r:
        data = json.loads(r.read())
    b64 = data["data"][0]["b64_json"]
    out_path.write_bytes(base64.b64decode(b64))
    return True


# ---------------------------------------------------------------------------
# チェックと採点
# ---------------------------------------------------------------------------
def compliance_checklist(kind: str) -> list[str]:
    common = ["顔が判別できない", "第三者のロゴ・商標・キャラクターなし", "実在の建物・看板が特定できない", "露骨な性的・残虐表現なし"]
    if kind == "cover":
        return common + ["3000×3000 に拡大しても粗くない", "URL・SNS 名・価格・宣伝文なし", "文字があれば曲名 / アーティスト名と一致"]
    if kind == "logo":
        return ["既存ロゴに似ていない（文字から起こしている）", "小さくしても読める", "第三者の商標に似ていない"]
    return common + ["Instagram / TikTok で危険行為に見えない"]


SCORE_SYSTEM = """You are the art director of EtherArchi, a music label where AI-generated music meets spatial design.
Score each candidate image for the given artist on five criteria from 1 (poor) to 5 (excellent):
consistency (fits the artist's visual sheet), symbolism (memorable, says something about the act),
scene_fit (fits the listening scene / the song), safety (platform rules and the face-hiding rule), craft (finish).
Then check every hard rule and the compliance list; list each violation you can SEE (a recognizable face, a third-party
logo or character, a URL / handle / price, text that is not exactly the song title or artist name, an identifiable real
building or sign, sexual or violent content). compliance_ok is false if there is any violation.
Apply the owner's learned preferences when scoring. comment_ja is one short Japanese sentence for the owner."""

SCORE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["candidates"],
    "properties": {"candidates": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["no", "scores", "violations", "compliance_ok", "comment_ja"],
        "properties": {
            "no": {"type": "integer"},
            "scores": {"type": "object", "additionalProperties": False,
                       "required": ["consistency", "symbolism", "scene_fit", "safety", "craft"],
                       "properties": {k: {"type": "integer", "minimum": 1, "maximum": 5}
                                      for k in ["consistency", "symbolism", "scene_fit", "safety", "craft"]}},
            "violations": {"type": "array", "items": {"type": "string"}},
            "compliance_ok": {"type": "boolean"},
            "comment_ja": {"type": "string"}}}}},
}


def thumbnail(src: Path) -> Path:
    """採点用に 768px の JPEG に縮める（送る量を減らす）。ffmpeg が無ければ元の画像"""
    import shutil, subprocess
    if not shutil.which("ffmpeg"):
        return src
    dst = src.with_name(src.stem + ".thumb.jpg")
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=768:-2", "-q:v", "4", str(dst)], check=False)
    return dst if dst.exists() else src


def claude_score(cands: list[dict], outdir: Path, artist: dict, criteria: dict, brief: dict | None) -> int:
    """生成した画像を Claude が見て 5 観点で採点し、禁止ルールに当たる候補を外す。戻り値：採点した枚数"""
    import _claude  # 同じフォルダ。ANTHROPIC_API_KEY が無ければ何もしない
    if not _claude.available():
        return 0
    done = 0
    for kind in sorted({c["kind"] for c in cands}):
        group = [c for c in cands if c["kind"] == kind and c.get("file") and (outdir / c["file"]).exists()]
        if not group:
            continue
        safe_artist = {k: artist.get(k) for k in ("name", "formation", "visual")}
        safe_artist["scene"] = (artist.get("persona") or {}).get("drive_scene") or (artist.get("persona") or {}).get("scene")
        content: list = [{"type": "text", "text": json.dumps({
            "kind": kind, "artist": safe_artist,
            "song": {k: (brief or {}).get(k) for k in ("title", "cover_prompt_seed")} if kind == "cover" else None,
            "hard_rules": criteria.get("hard_rules", []), "compliance": compliance_checklist(kind),
            "owner_preferences": criteria.get("preferences", [])}, ensure_ascii=False)}]
        for c in group:
            content += [{"type": "text", "text": f"Candidate {c['no']}:"}, _claude.image_block(thumbnail(outdir / c["file"]))]
        print(f"  Claude が {kind} の候補 {len(group)} 枚を見て採点しています…")
        res, msg = _claude.call_json(SCORE_SYSTEM, content, SCORE_SCHEMA, effort="medium")
        if not res:
            print(f"    採点できませんでした（{msg}）。仮の点のままにします")
            continue
        by_no = {r["no"]: r for r in res["candidates"]}
        for c in group:
            r = by_no.get(c["no"])
            if not r:
                continue
            c["scores"] = {k: float(v) for k, v in r["scores"].items()}
            c["violations"], c["compliance_ok"], c["claude_note"] = r["violations"], r["compliance_ok"], r["comment_ja"]
            c.pop("scores_note", None)
            done += 1
    return done


LEARN_SYSTEM = """You maintain the scoring criteria for an art director's taste. From the owner's past choices
(what they picked, the scores the machine gave every candidate at the time, and the reason they wrote), adjust the five
weights so that the machine would have ranked the owner's pick first more often, and distill short Japanese preference
notes (each one concrete and checkable, e.g. 余白が多い構図を優先). Weights must be between 0.05 and 0.5 and sum to 1.0."""

LEARN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["weights", "preferences", "note_ja"],
    "properties": {
        "weights": {"type": "object", "additionalProperties": False,
                    "required": ["consistency", "symbolism", "scene_fit", "safety", "craft"],
                    "properties": {k: {"type": "number"} for k in ["consistency", "symbolism", "scene_fit", "safety", "craft"]}},
        "preferences": {"type": "array", "items": {"type": "string"}},
        "note_ja": {"type": "string"}},
}


def learn_criteria(criteria: dict) -> None:
    """オーナーの判断（最初の 5 回）から、採点の重みと好みのメモを Claude が更新する"""
    import _claude
    user = json.dumps({"current_weights": criteria["weights"], "decisions": criteria["decisions"],
                       "current_preferences": criteria.get("preferences", [])}, ensure_ascii=False, indent=1)
    if not _claude.available():
        f = ROOT / "out" / "visuals" / "learn_criteria.prompt.md"
        _claude.dry_run_file(f, LEARN_SYSTEM, user, LEARN_SCHEMA)
        print(f"  ANTHROPIC_API_KEY が無いので、基準の更新は依頼文だけ書き出しました: {f.relative_to(ROOT)}")
        return
    print("  オーナーの 5 回の判断から、採点の重みと好みを更新しています…")
    res, msg = _claude.call_json(LEARN_SYSTEM, user, LEARN_SCHEMA)
    if not res:
        print(f"  基準を更新できませんでした（{msg}）")
        return
    w = {k: min(0.5, max(0.05, float(v))) for k, v in res["weights"].items()}
    total = sum(w.values())
    old = dict(criteria["weights"])
    criteria["weights"] = {k: round(v / total, 3) for k, v in w.items()}
    criteria["preferences"] = list(dict.fromkeys(res["preferences"]))[:12]
    criteria["version"] = int(criteria.get("version", 1)) + 1
    criteria.setdefault("learning_log", []).append({"date": date.today().isoformat(), "from": old,
                                                    "to": criteria["weights"], "note": res["note_ja"]})
    save_criteria(criteria)
    print(f"  基準を更新しました（版 {criteria['version']}）：{res['note_ja']}")


def score_candidate(c: dict, criteria: dict) -> float:
    """
    採点。本番では Claude が画像を見て 5 観点に 1〜5 点をつける（c['scores'] に入れる）。
    ここでは scores が無ければ指示文の充実度から仮の点を入れる（ドライラン用）。
    """
    if "scores" not in c:
        base = 3.0 + min(1.5, len(c["prompt"]) / 600)
        c["scores"] = {k: round(base, 1) for k in criteria["weights"]}
        c["scores_note"] = "仮の点（画像未生成）。Claude が画像を見て付け直す"
    total = sum(criteria["weights"][k] * c["scores"].get(k, 0) for k in criteria["weights"])
    if c.get("compliance_ok") is False:
        total = 0.0      # 禁止ルールに当たる候補は除外（0 点）
    c["total"] = round(total, 3)
    return c["total"]


# ---------------------------------------------------------------------------
# 選択（最初の 5 回は聞く → 基準を学ぶ → 自動）
# ---------------------------------------------------------------------------
def decide(cands: list[dict], criteria: dict, ask_forced: bool = False) -> tuple[dict | None, str]:
    ranked = sorted([c for c in cands if c.get("compliance_ok") is not False], key=lambda c: -c["total"])
    if not ranked:
        return None, "全候補が禁止ルールに当たったので作り直しが必要"
    if len(ranked) < 2:
        return (ranked[0] if ranked else None), "候補が 1 つ"
    margin = ranked[0]["total"] - ranked[1]["total"]
    if not criteria["locked"] or ask_forced:
        return None, f"オーナーに聞く（{criteria['decisions_asked']}/{criteria['decisions_required_before_lock']} 回目）"
    if margin < criteria["low_confidence_margin"]:
        return None, f"確信度が低い（1 位と 2 位の差 {margin:.2f} < {criteria['low_confidence_margin']}）ので聞く"
    return ranked[0], f"自動選択（差 {margin:.2f}）"


def record_choice(criteria: dict, artist: str, kind: str, chosen_no: int, reason: str, cands: list[dict]) -> None:
    criteria["decisions"].append({"date": date.today().isoformat(), "artist": artist, "kind": kind, "chosen": chosen_no,
                                  "reason": reason, "scores_at_time": {c["no"]: c["total"] for c in cands if c["kind"] == kind}})
    if reason:
        criteria["preferences"].append(reason)
    criteria["decisions_asked"] += 1
    just_locked = False
    if criteria["decisions_asked"] >= criteria["decisions_required_before_lock"] and not criteria["locked"]:
        criteria["locked"] = True
        criteria["_locked_note"] = f"{date.today()} に 5 回の判断がそろったので自動選択に切り替え"
        just_locked = True
    save_criteria(criteria)
    if just_locked:
        learn_criteria(criteria)   # 5 回の判断から重みと好みを学ぶ


def write_review(path: Path, artist: dict, cands: list[dict], note: str) -> None:
    lines = [f"# ビジュアル候補レビュー：{artist['name']}（{artist['slug']}）", "", f"判断：{note}", ""]
    for kind in sorted({c["kind"] for c in cands}):
        lines += [f"## {kind}", "", "| # | 点 | 方法 / 変種 | ファイル | Claude の所見 | 規約チェック |", "|---|---|---|---|---|---|"]
        for c in sorted([c for c in cands if c["kind"] == kind], key=lambda c: -c["total"]):
            check = ("✕ " + "、".join(c.get("violations", []))) if c.get("compliance_ok") is False else (
                "○" if c.get("compliance_ok") else "／".join(compliance_checklist(kind)))
            lines.append(f"| {c['no']} | {c['total']} | {c.get('method', '')} | {c.get('file', '（未生成：指示文のみ）')} | "
                         f"{c.get('claude_note', c.get('scores_note', ''))} | {check} |")
        lines.append("")
    lines += ["## 選ぶとき", "", "`--choose <kind>:<番号> --reason \"一言\"` で記録してください。理由が基準に反映されます。", ""]
    lines += ["## 指示文（確認用）", ""] + [f"- **{c['kind']} {c['no']}**: {c['prompt']}" for c in cands]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="ロゴ・顔を出さないアーティスト写真・ジャケットの候補を生成して選ぶ")
    ap.add_argument("--artist", required=True)
    ap.add_argument("--label", help="子レーベルの slug（子レーベルの組のとき）")
    ap.add_argument("--kind", choices=["debut", "cover", "reshoot"], required=True)
    ap.add_argument("--brief", type=Path, help="cover のときのブリーフ JSON")
    ap.add_argument("--pivot-reason", default="", help="reshoot のときの転換理由")
    ap.add_argument("--choose", help="オーナーの選択。例 logo:3 / photo:5 / cover:2")
    ap.add_argument("--reason", default="", help="選んだ理由（一言。基準に反映される）")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "visuals")
    ap.add_argument("--skip-name-check", action="store_true", help="名前の重複確認を飛ばす（テスト用。本番では使わない）")
    args = ap.parse_args()

    artist = load_artist(args.artist, args.label)
    # デビュー処理の最初に名前の重複確認。checked でなければロゴ・写真を作らない（名前が変わると全部作り直しになる）
    if args.kind == "debut" and not args.choose and not args.skip_name_check and artist.get("name_status") != "checked":
        sys.exit(f"[停止] {artist['name']} の名前はまだ重複確認が済んでいません（name_status={artist.get('name_status')}）。\n"
                 f"       先に python3 scripts/check_names.py --artist {artist['slug']}" + (f" --label {args.label}" if args.label else "") +
                 " --apply を実行し、report.md の手動確認（商標・SNS）も済ませてください。")
    criteria = load_criteria()
    outdir = args.out / artist["slug"] / args.kind
    if args.kind == "cover" and args.brief:
        # ジャケットは週ごとに分ける（前の週の候補を上書きしない）
        outdir = outdir / json.loads(args.brief.read_text(encoding="utf-8")).get("week_start", "undated")
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"=== ビジュアル生成：{artist['name']}（{args.kind}）===")
    print(f"  顔を見せない方法：主「{artist['visual'].get('face_concealment',{}).get('primary','')}」")

    # 既存の候補があれば読み（選択の記録用）、無ければ指示文を作る
    cand_path = outdir / "candidates.json"
    if args.choose and cand_path.exists():
        cands = json.loads(cand_path.read_text(encoding="utf-8"))
        kind, no = args.choose.split(":"); no = int(no)
        chosen = next((c for c in cands if c["kind"] == kind and c["no"] == no), None)
        if not chosen:
            sys.exit(f"[エラー] 候補 {args.choose} がありません")
        record_choice(criteria, artist["slug"], kind, no, args.reason, cands)
        (outdir / f"selection_{kind}.json").write_text(json.dumps({"kind": kind, "chosen": no, "reason": args.reason, "by": "owner",
                                                            "date": date.today().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  選択を記録しました：{kind} #{no}（{args.reason}）")
        print(f"  基準の学習：{criteria['decisions_asked']}/{criteria['decisions_required_before_lock']} 回。"
              f"{'自動選択に切り替わりました' if criteria['locked'] else 'まだオーナーに聞きます'}")
        return

    if args.kind == "debut":
        cands = prompts_debut(artist)
    elif args.kind == "cover":
        if not args.brief:
            ap.error("--kind cover には --brief が必要です")
        cands = prompts_cover(artist, json.loads(args.brief.read_text(encoding="utf-8")))
    else:
        cands = prompts_reshoot(artist, args.pivot_reason)
    print(f"  指示文を {len(cands)} 本作りました（実名・商標は含めていません）")

    generated, errors = 0, []
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    if not has_key:
        print("  OPENAI_API_KEY が見つかりません（music-label/.env か環境変数）。画像は生成せず、指示文だけ書き出します（ドライラン）")
    else:
        print(f"  画像を生成しています（{len(cands)} 枚。1 枚 10〜30 秒）…")
    for i, c in enumerate(cands, 1):
        f = outdir / f"{c['kind']}_{c['no']:02d}.png"
        try:
            if generate_image(c["prompt"], f):
                c["file"] = f.name; generated += 1
                print(f"    [{i}/{len(cands)}] {c['kind']} {c['no']:02d} 完了")
        except urllib.error.HTTPError as e:   # API からのエラー応答（鍵・残高・内容の拒否など）
            body = e.read().decode("utf-8", "replace")[:300]
            msg = f"HTTP {e.code}: {body}"
            c["error"] = msg; errors.append(msg)
            print(f"    [{i}/{len(cands)}] {c['kind']} {c['no']:02d} 失敗 → {msg}")
        except Exception as e:   # noqa: BLE001
            c["error"] = str(e); errors.append(str(e))
            print(f"    [{i}/{len(cands)}] {c['kind']} {c['no']:02d} 失敗 → {e}")
        if errors and len(errors) >= 3 and generated == 0:
            print("  3 枚続けて失敗したので中断します（同じ原因の可能性が高い）")
            break
    if has_key:
        print(f"  画像を {generated} 枚生成しました" + (f"（失敗 {len(errors)} 枚）" if errors else ""))
    if errors:
        first = errors[0]
        hint = (SSL_HINT if "CERTIFICATE_VERIFY_FAILED" in first or "SSL" in first else
                "鍵が無効です。.env の OPENAI_API_KEY を確認（前後の空白・改行、sk- で始まるか。`python3 scripts/set_key.py --check`）" if "401" in first else
                "OpenAI 側の残高不足か回数制限です。platform.openai.com の Billing を確認" if "429" in first else
                "画像モデルの利用が許可されていません。OpenAI の組織設定で gpt-image-1 の利用（本人確認）を確認" if "403" in first or "verif" in first.lower() else
                "指示文が内容ポリシーで拒否されました。該当候補だけ除外して続行できます" if "safety" in first.lower() or "content_policy" in first.lower() else
                "ネットワークか API の一時的な問題の可能性。少し待って再実行")
        print(f"  → 対処のヒント: {hint}")

    n_scored = claude_score(cands, outdir, artist, criteria,
                            json.loads(args.brief.read_text(encoding="utf-8")) if args.brief else None) if generated else 0
    if n_scored:
        print(f"  Claude が {n_scored} 枚を採点しました")
    for c in cands:
        score_candidate(c, criteria)
    cand_path.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")

    for kind in sorted({c["kind"] for c in cands}):
        sub = [c for c in cands if c["kind"] == kind]
        chosen, note = decide(sub, criteria, ask_forced=(args.kind == "reshoot"))
        print(f"  {kind}: {note}")
        if chosen:
            (outdir / f"selection_{kind}.json").write_text(json.dumps({"kind": kind, "chosen": chosen["no"], "by": "auto",
                "total": chosen["total"], "date": date.today().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review(outdir / "review.md", artist, cands, "候補を確認して --choose で選んでください" if not criteria["locked"] else "自動選択済み（確信度が低いものだけ確認）")
    print(f"  → {outdir.relative_to(ROOT)}/review.md を書き出しました")
    print("=== 完了 ===")


if __name__ == "__main__":
    main()
