#!/usr/bin/env python3
"""
ブリーフ生成の本体：枠の割り当て（骨組み）に、Suno 用の指示文・歌詞・タイトル候補を Claude が書き足す

入力：select_references.py が出した骨組み JSON（out/briefs/<週>_<組>.json）
出力：同じファイルを上書き（suno_style_prompt / suno_lyrics / title_candidates / vocal_blend / phrase_transform 候補 /
      cover_prompt_seed / notes_ja が埋まる）。あわせて <同名>.prompt.md に Claude へ渡した内容を保存する

使い方：
  python3 scripts/write_brief.py out/briefs/2026-10-05_light.json
  python3 scripts/write_brief.py out/briefs/2026-10-05_*.json --references out/references/   # 解析シートがあれば枠ごとに渡す
  python3 scripts/write_brief.py out/briefs/2026-10-05_light.json --dry-run                   # API を呼ばず指示文だけ書き出す

ANTHROPIC_API_KEY が無いときは自動でドライランになる（.prompt.md を claude.ai に貼れば手動でも進められる）。
必要なもの：pip install anthropic
"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = "claude-opus-5-5"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")

SLOT_SECTION = {   # 枠 → 解析シートのどの欄だけを渡すか（1 曲 1 枠：他の欄は渡さない）
    "lyrics": ["lyrics"], "worldview": ["worldview"], "instruments": ["instruments"], "performance": ["performance"],
    "structure": ["structure"], "harmony": ["harmony"], "groove": ["groove"], "phrase": ["phrases"],
    "vocal_main": ["vocal"], "vocal_sub1": ["vocal"], "vocal_sub2": ["vocal"],
}

# Claude に返してもらう JSON の形（構造化出力）
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_candidates", "suno_style_prompt", "suno_lyrics", "vocal_instruction",
                 "phrase_transform_options", "cover_prompt_seed", "notes_ja", "self_check"],
    "properties": {
        "title_candidates": {"type": "array", "minItems": 3, "maxItems": 5, "items": {"type": "string"}},
        "suno_style_prompt": {"type": "string", "description": "Suno の Style 欄。英語。ジャンル・テンポ・楽器・声の質感・場面。実在アーティスト名を含めない。900 文字以内"},
        "suno_lyrics": {"type": "string", "description": "Suno の Lyrics 欄。[Intro] [Verse 1] [Chorus] [Bridge] [Outro] などのセクションタグと、[Humming] [Whisper] [Shout] [la la la] などの歌い方タグを含む"},
        "vocal_instruction": {"type": "string", "description": "表現層 6:2:2 を合成した歌唱指示（英語 1〜3 文）。音名ではなく描写で"},
        "phrase_transform_options": {
            "type": "array", "minItems": 3, "maxItems": 3,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["kept", "changed", "contour_interval_changed", "description"],
                      "properties": {
                          "kept": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": ["rhythm", "contour", "instrument", "position"]}},
                          "changed": {"type": "array", "items": {"type": "string"}},
                          "contour_interval_changed": {"type": "boolean"},
                          "description": {"type": "string"}}}},
        "cover_prompt_seed": {"type": "string", "description": "ジャケット生成の種（英語 3〜5 行）。色調・光・構図・質感・モチーフ。人の顔・文字・ロゴなし"},
        "notes_ja": {"type": "string", "description": "オーナー向けの日本語メモ：この曲で何を狙ったか、どの枠から何を借りたか、迷った点"},
        "self_check": {"type": "object", "additionalProperties": False,
                       "required": ["no_real_artist_names", "hook_within_limit", "tics_included", "forbidden_avoided"],
                       "properties": {k: {"type": "boolean"} for k in ["no_real_artist_names", "hook_within_limit", "tics_included", "forbidden_avoided"]}},
    },
}

SYSTEM = """You write production briefs for an AI-generated music label. Each brief turns a "skeleton" (which reference
songs feed which slot) plus a fixed artist profile into: a Suno style prompt, full lyrics with section tags, title
candidates, a vocal instruction, three phrase-transformation options, and a cover-art seed.

Hard rules (never break):
1. One source per slot. Borrow ONLY the named dimension from each reference (e.g. from the "lyrics" reference take
   theme/arc/rhyme/syllable structure - never its words; from "phrase" take rhythm skeleton or contour - never pitches).
2. Never copy lyrics lines, melodies, or distinctive phrases from any reference. Never name a real artist, band, song,
   or brand anywhere in the output (Suno rejects them and it is a likeness/IP risk). Describe by attributes instead.
3. The artist's fixed constraints (composition habits, voice spec, signature techniques, tics, landscape words,
   forbidden list, BPM range, drive/scene spec) override anything a reference suggests.
4. Vocal: convert note names to descriptions ("the chorus peak sits just below where the voice would flip to
   falsetto, sung in full voice"). Blend the three vocal references 6:2:2 for EXPRESSION only (breath, ad-libs,
   layering, phrasing) - the timbre is fixed by the artist's Suno Persona.
5. Phrase transformation: keep at most 2 of {rhythm, contour, instrument, position}; words always change; if contour
   is kept, intervals/key must change (contour_interval_changed=true).
6. Hook/chorus element within the hook_within_sec limit; total length inside length_sec range; no sudden silence;
   nothing from forbidden/forbidden_topics; lyrics in the primary language; if trend_language is given and the
   artist allows it, use it only for one hook line or call-out, within the max ratio.
7. Tics with frequency every_song MUST appear in the lyrics as section/performance tags (e.g. [Humming], [Whisper],
   [Shout], [la la la]) at the stated position.
8. Suno style prompt: English, under 900 characters, comma-separated descriptors (genre, era feel, tempo/BPM,
   instruments, vocal timbre and delivery, mood, scene), no artist names, no lyrics.
9. notes_ja is written in Japanese for the owner; everything else in English unless the brief says otherwise.
Return only the JSON object described by the schema."""


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def load_artist(slug: str, label_slug: str) -> dict:
    if label_slug and label_slug != "drive":
        lab = load_json(ROOT / "templates" / "labels" / f"{label_slug}.json")
        return next(a for a in lab["artists"] if a["slug"] == slug)
    return load_json(ROOT / "templates" / "artists" / f"{slug}.json")


def slot_excerpts(brief: dict, refs_dir: Path | None) -> dict[str, dict]:
    """枠ごとに、参考曲の解析シートから『その枠の欄だけ』を抜き出す。歌詞の語（key_words.word）は落とす"""
    out = {}
    for slot, src in brief["slots"].items():
        entry = {"reference_title": src["title"], "reference_artist": src.get("artist_name", ""), "weight": src.get("weight", 1.0)}
        sheet = None
        if refs_dir:
            for cand in (refs_dir / f"{src['reference_id']}.json",):
                if cand.exists():
                    sheet = load_json(cand)
        if sheet:
            for sec in SLOT_SECTION.get(slot, []):
                data = json.loads(json.dumps(sheet.get(sec, {})))
                if sec == "lyrics" and isinstance(data, dict) and "key_words" in data:
                    data["key_words"] = [{"category": k.get("category")} for k in data["key_words"]]   # 語そのものは渡さない
                entry[sec] = data
        else:
            entry["note"] = "解析シートなし。タイトルと組の固定制約から推定すること（具体的な模倣は禁止）"
        out[slot] = entry
    return out


def public_safe_artist(artist: dict) -> dict:
    """Claude に渡す設定書。実在アーティスト名（favorite_artists_real / influences.name）は落とす"""
    a = json.loads(json.dumps(artist))
    a.pop("profile", None); a.pop("distribution", None); a.pop("name_check", None); a.pop("concept_history", None)
    for inf in a.get("persona", {}).get("influences", []):
        inf.pop("name", None); inf.pop("reference_id", None)
    for k in list(a.keys()):
        if k.startswith("_"):
            a.pop(k)
    return a


def build_user_message(brief: dict, artist: dict, excerpts: dict) -> str:
    return (
        "## Artist profile (fixed constraints; names of real artists already removed)\n"
        + json.dumps(public_safe_artist(artist), ensure_ascii=False, indent=1)
        + "\n\n## Brief skeleton (slots -> references, growth hints, trend language)\n"
        + json.dumps({k: v for k, v in brief.items() if k not in ("slots",)}, ensure_ascii=False, indent=1)
        + "\n\n## Per-slot reference excerpts (only the borrowed dimension of each reference)\n"
        + json.dumps(excerpts, ensure_ascii=False, indent=1)
        + "\n\nWrite the brief now."
    )


def banned_terms(artist: dict) -> list[str]:
    terms = list(artist.get("profile", {}).get("favorite_artists_real", []))
    terms += [i.get("name", "") for i in artist.get("persona", {}).get("influences", [])]
    return [t for t in terms if t and t not in ("（bio のみ）",)]


def validate(result: dict, artist: dict, brief: dict) -> list[str]:
    problems = []
    text = json.dumps(result, ensure_ascii=False)
    for t in banned_terms(artist):
        if t.lower() in text.lower():
            problems.append(f"実在アーティスト名が含まれている: {t}")
    if len(result["suno_style_prompt"]) > 900:
        problems.append(f"Suno のスタイル指示が長すぎる（{len(result['suno_style_prompt'])} 文字 > 900）")
    for opt in result["phrase_transform_options"]:
        if len(opt["kept"]) > 2:
            problems.append("フレーズ変形で残す要素が 3 つ以上")
        if "contour" in opt["kept"] and not opt["contour_interval_changed"]:
            problems.append("輪郭を残しているのに音程の幅を変えていない")
    if any(re.search(r"\b(feat|ft)\.?\s", t, re.I) for t in result["title_candidates"]):
        problems.append("曲名に feat. が入っている（フィーチャリング欄で登録する運用）")
    tics = [t for t in artist.get("vocal", {}).get("signature_techniques", {}).get("tics", []) if t.get("frequency") == "every_song"]
    if tics and "[" not in result["suno_lyrics"]:
        problems.append("歌詞にセクション／歌い方タグが無い（毎曲の癖が入っていない可能性）")
    if not all(result["self_check"].values()):
        problems.append(f"Claude の自己チェックで未達: {[k for k, v in result['self_check'].items() if not v]}")
    return problems


def call_claude(system: str, user: str) -> tuple[dict | None, str]:
    """Claude を呼んで JSON を受け取る。(結果, 状態メッセージ)"""
    try:
        import anthropic
    except ImportError:
        return None, "anthropic パッケージが無い（pip install anthropic）"
    client = anthropic.Anthropic()
    try:
        # ストリーミング：長い出力でもタイムアウトしない。拒否時はサーバー側で別モデルに自動フォールバック
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],   # 固定部分はキャッシュ
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        return None, "ANTHROPIC_API_KEY が無効（401）。set_key.py ANTHROPIC_API_KEY で入れ直す"
    except anthropic.RateLimitError as e:
        return None, f"回数制限か残高不足（429）: {e}"
    except anthropic.APIStatusError as e:
        return None, f"API エラー {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        return None, f"接続できない（ネットワーク／証明書）: {e}"
    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        return None, f"安全上の理由で生成が拒否された（{cat}）。歌詞テーマや語を見直す"
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text), f"OK（入力 {msg.usage.input_tokens} / 出力 {msg.usage.output_tokens} トークン、モデル {msg.model}）"
    except json.JSONDecodeError:
        return None, "返答が JSON として読めない（max_tokens 不足の可能性）"


def main() -> None:
    ap = argparse.ArgumentParser(description="ブリーフに Suno 用の指示文・歌詞・タイトルを書き足す")
    ap.add_argument("briefs", nargs="+", type=Path, help="骨組み JSON（複数可）")
    ap.add_argument("--references", type=Path, help="解析シート JSON のフォルダ（<reference_id>.json）")
    ap.add_argument("--dry-run", action="store_true", help="API を呼ばず、Claude に渡す内容だけ書き出す")
    args = ap.parse_args()

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    dry = args.dry_run or not has_key
    print("=== ブリーフ生成を始めます ===")
    if dry:
        print("  " + ("--dry-run 指定" if args.dry_run else "ANTHROPIC_API_KEY が無い") + "ため、Claude には送らず指示文（.prompt.md）だけ書き出します")
        print("  → .prompt.md の内容を claude.ai に貼れば、手動でも同じ結果が得られます")

    def rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(ROOT))
        except ValueError:
            return str(p)

    for bp in args.briefs:
        bp = bp.resolve()
        if not bp.exists():
            print(f"\n[停止] 骨組みファイルがありません: {rel(bp)}")
            print("       先に  python3 scripts/select_references.py --all --demo --week <月曜の日付>  を実行して骨組みを作ってください")
            print("       （実際の参考曲 DB があるときは --demo の代わりに --references <解析シートのフォルダ>）")
            continue
        brief = load_json(bp)
        artist = load_artist(brief["artist_slug"], brief.get("label_slug", "drive"))
        print(f"\n--- {bp.name}（{artist['name']}）---")
        excerpts = slot_excerpts(brief, args.references)
        n_sheets = sum(1 for e in excerpts.values() if "note" not in e)
        print(f"  参考曲の解析シート: {n_sheets}/{len(excerpts)} 枠ぶんを渡します（無い枠はタイトルだけ）")
        user = build_user_message(brief, artist, excerpts)
        prompt_path = bp.with_suffix(".prompt.md")
        prompt_path.write_text("# System\n\n" + SYSTEM + "\n\n# User\n\n" + user + "\n\n# 期待する返答の形（JSON Schema）\n\n```json\n"
                               + json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=1) + "\n```\n", encoding="utf-8")
        print(f"  Claude に渡す内容を保存: {rel(prompt_path)}（約 {len(SYSTEM) + len(user):,} 文字）")
        if dry:
            continue

        print(f"  Claude（{MODEL}）に送っています…")
        result, status = call_claude(SYSTEM, user)
        print(f"  {status}")
        if not result:
            continue
        problems = validate(result, artist, brief)
        if problems:
            print("  [要確認] " + " / ".join(problems))
        brief["title_candidates"] = result["title_candidates"]
        brief["suno_style_prompt"] = result["suno_style_prompt"]
        brief["suno_lyrics"] = result["suno_lyrics"]
        brief["vocal_blend"]["synthesized_instruction"] = result["vocal_instruction"]
        brief["phrase_transform"]["options"] = result["phrase_transform_options"]
        brief["phrase_transform"]["description"] = "options から 1 つ選び、kept / changed / contour_interval_changed を上に写す"
        brief["cover_prompt_seed"] = result["cover_prompt_seed"]
        brief["notes_ja"] = result["notes_ja"]
        brief["validation"] = {"problems": problems, "checked_at": date.today().isoformat()}
        brief["generated_by"] = f"select_references.py + write_brief.py ({MODEL})"
        bp.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  タイトル候補: {' / '.join(result['title_candidates'][:3])}")
        print(f"  → {rel(bp)} を更新しました")

    print("\n=== 完了 ===" + ("" if dry else "。次：Suno に suno_style_prompt と suno_lyrics を貼って 4〜6 テイク生成"))


if __name__ == "__main__":
    main()
