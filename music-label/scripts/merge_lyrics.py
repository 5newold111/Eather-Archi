#!/usr/bin/env python3
"""
核（こちらで固定した歌詞）と、Suno の歌詞生成が書いた節を合体して最終歌詞を作る（core_fixed モード）

流れ：
  1. write_brief.py が出したブリーフの suno_lyrics には {{SUNO_VERSE_1}} などの差し込み位置がある
  2. Suno の Write Lyrics に brief["suno_lyric_prompt"] を貼り、出てきた節をテキストファイルに保存する
     （[Verse 1] / [Pre-Chorus] / [Verse 2] / [Bridge] の見出し付きで。見出しが無ければ空行区切りの順番で割り当てる）
  3. このスクリプトが差し込み → 検査 → brief["suno_lyrics_final"] に保存し、Suno Custom に貼る文を表示する

検査：
  - 核（サビ・決め台詞・タグ）が 1 文字も変わっていない
  - 実名（favorite_artists_real / influences.name）・禁止トピックの語が入っていない
  - 節の 1 行あたり音節数が目標 ±2 以内（外れた行は警告）
  - 行の長さが極端でない、空の差し込みが残っていない

使い方：
  python3 scripts/merge_lyrics.py out/briefs/2026-10-05_light.json --verses out/briefs/2026-10-05_light.suno_verses.txt
  python3 scripts/merge_lyrics.py out/briefs/2026-10-05_light.json --show-prompt     # Suno に貼る節の指示文を表示
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDERS = ["{{SUNO_VERSE_1}}", "{{SUNO_PRE_CHORUS}}", "{{SUNO_VERSE_2}}", "{{SUNO_BRIDGE}}"]
HEADINGS = {"{{SUNO_VERSE_1}}": ["verse 1", "verse1", "verse one"], "{{SUNO_PRE_CHORUS}}": ["pre-chorus", "pre chorus", "prechorus"],
            "{{SUNO_VERSE_2}}": ["verse 2", "verse2", "verse two"], "{{SUNO_BRIDGE}}": ["bridge"]}


def load_artist(slug: str, label_slug: str) -> dict:
    if label_slug and label_slug != "drive":
        lab = json.loads((ROOT / "templates" / "labels" / f"{label_slug}.json").read_text(encoding="utf-8"))
        return next(a for a in lab["artists"] if a["slug"] == slug)
    return json.loads((ROOT / "templates" / "artists" / f"{slug}.json").read_text(encoding="utf-8"))


def count_syllables(line: str) -> int:
    """英語の音節数のざっくり推定（母音のまとまりを数える）。精密ではないが行同士の比較には足りる"""
    words = re.findall(r"[a-zA-Z']+", line.lower())
    total = 0
    for w in words:
        w = re.sub(r"e$", "", w) if len(w) > 2 else w
        groups = re.findall(r"[aeiouy]+", w)
        total += max(1, len(groups))
    return total


HEADING_RE = re.compile(r"^\[?\s*(verse\s*(?:1|one)|verse\s*(?:2|two)|pre[- ]?chorus|bridge)\s*\]?\s*:?\s*$", re.I)


def heading_to_placeholder(key: str) -> str | None:
    k = re.sub(r"\s+", "", key.lower())
    if k.startswith("verse1") or k.startswith("verseone"):
        return "{{SUNO_VERSE_1}}"
    if k.startswith("verse2") or k.startswith("versetwo"):
        return "{{SUNO_VERSE_2}}"
    if k.startswith("pre"):
        return "{{SUNO_PRE_CHORUS}}"
    if k.startswith("bridge"):
        return "{{SUNO_BRIDGE}}"
    return None


def parse_verses(text: str) -> dict[str, list[str]]:
    """Suno の出力を差し込み位置ごとに分ける。[Verse 1] などの見出しがあればそれで、無ければ空行区切りの順番で"""
    text = text.replace("\r", "")
    blocks: dict[str, list[str]] = {}
    current = None
    found_heading = False
    for raw in text.split("\n"):
        line = raw.strip()
        m = HEADING_RE.match(line)
        if m:
            current = heading_to_placeholder(m.group(1))
            found_heading = True
            if current:
                blocks.setdefault(current, [])
            continue
        if line and current:
            blocks[current].append(line)
    if not found_heading:
        paras = [p.strip().split("\n") for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        for ph, para in zip(PLACEHOLDERS, paras):
            blocks[ph] = [l.strip() for l in para if l.strip()]
    return blocks


def check_content(final: str, artist: dict) -> list[str]:
    """実名・禁止トピックの検査（core_fixed / topic_only 共通）"""
    problems = []
    banned = list(artist.get("profile", {}).get("favorite_artists_real", [])) + \
             [i.get("name", "") for i in artist.get("persona", {}).get("influences", []) if i.get("name") not in (None, "", "（bio のみ）")]
    for b in banned:
        if b and b.lower() in final.lower():
            problems.append(f"実名が含まれている: {b}")
    for topic in artist.get("lyrics", {}).get("forbidden_topics", []):
        for kw in {"宗教": ["god", "jesus", "allah", "pray", "church"], "政治": ["president", "election", "government", "vote"]}.get(topic, []):
            if re.search(rf"\b{kw}\b", final, re.I):
                problems.append(f"禁止トピック『{topic}』の語: {kw}")
    return problems


def topic_only_merge(text: str, core: dict, artist: dict) -> tuple[str, list[str], list[str]]:
    """
    お題だけモード：Suno が書いた歌詞全文を受け取り、
    - セクション見出しが無ければ付ける（[Verse 1] [Chorus] … の推定）
    - 固定タグ（毎曲の癖）が無ければ該当セクションに補う
    - 実名・禁止語・音節数を検査する
    """
    problems, warnings = [], []
    lines = [l.rstrip() for l in text.replace("\r", "").split("\n")]
    has_headings = any(re.match(r"^\[[^\]]+\]\s*$", l) for l in lines)
    if not has_headings:
        # 空行区切りの段落を Verse / Chorus … に見立てる（繰り返しが出る段落を Chorus と推定）
        paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        seen, out, v = {}, [], 0
        for para in paras:
            key = para.lower()
            if key in seen:
                out.append("[Chorus]"); out.append(para); out.append("")
                continue
            seen[key] = True
            if paras.count(para) > 1:
                out.append("[Chorus]")
            else:
                v += 1; out.append(f"[Verse {v}]")
            out.append(para); out.append("")
        lines = "\n".join(out).split("\n")
        warnings.append("Suno の出力に見出しが無かったので、[Verse]/[Chorus] を推定して付けました。確認してください")
    final = "\n".join(lines).strip()

    # 固定タグの補完：'[Outro] [Humming]' → [Outro] の直後に [Humming] が無ければ入れる
    for tag in core.get("fixed_tags", []):
        parts = re.findall(r"\[([^\]]+)\]", tag)
        if len(parts) >= 2:
            section, perf = parts[0], parts[-1]
            if f"[{perf}]" in final:
                continue
            if f"[{section}]" in final:
                final = final.replace(f"[{section}]", f"[{section}]\n[{perf}]", 1)
                warnings.append(f"固定タグ [{perf}] を [{section}] に補いました")
            elif section.lower().startswith("intro"):
                final = f"[{section}]\n[{perf}]\n\n" + final
                warnings.append(f"[{section}] が無かったので先頭に [{section}] [{perf}] を追加しました")
            else:
                final += f"\n\n[{section}]\n[{perf}]"
                warnings.append(f"[{section}] が無かったので末尾に [{section}] [{perf}] を追加しました")
        elif len(parts) == 1 and f"[{parts[0]}]" not in final:
            warnings.append(f"固定タグ [{parts[0]}] がどこにも無い（手で位置を決めて入れてください）")

    target = int(core.get("syllables_per_line_target") or 0)
    if target:
        off = [l for l in final.split("\n") if l and not l.startswith("[") and abs(count_syllables(l) - target) > 3]
        if len(off) > 4:
            warnings.append(f"音節数が目標 {target} から大きく外れる行が {len(off)} 行あります（歌が詰まる可能性）")
    if "[Chorus]" not in final:
        warnings.append("[Chorus] が見当たりません")
    problems += check_content(final, artist)
    return final, problems, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description="固定した核と Suno の節を合体して最終歌詞を作る")
    ap.add_argument("brief", type=Path)
    ap.add_argument("--verses", type=Path, help="Suno の Write Lyrics の出力を保存したテキスト")
    ap.add_argument("--show-prompt", action="store_true", help="Suno に貼る節の指示文を表示して終わる")
    ap.add_argument("--tolerance", type=int, default=2, help="音節数の許容差（既定 ±2）")
    args = ap.parse_args()

    brief = json.loads(args.brief.read_text(encoding="utf-8"))
    artist = load_artist(brief["artist_slug"], brief.get("label_slug", "drive"))
    core = brief.get("core", {})
    skeleton = brief.get("suno_lyrics", "")

    if args.show_prompt or not args.verses:
        print("=== Suno の Write Lyrics に貼る指示文（節だけを書かせる） ===\n")
        print(brief.get("suno_lyric_prompt") or "（このブリーフは core_fixed ではないか、まだ write_brief.py を通していません）")
        print("\n=== 出てきた節を保存するファイル名の例 ===")
        print(f"  {args.brief.with_suffix('.suno_verses.txt')}")
        print("  見出し [Verse 1] [Pre-Chorus] [Verse 2]（必要なら [Bridge]）を付けて保存し、--verses で渡してください")
        return

    text = args.verses.read_text(encoding="utf-8")
    if brief.get("lyrics_mode") == "topic_only":
        final, problems, warnings = topic_only_merge(text, core, artist)
        for w in warnings: print(f"  [注意] {w}")
        for p in problems: print(f"  [問題] {p}")
        brief["suno_lyrics_final"] = final
        brief["merge"] = {"mode": "topic_only", "problems": problems, "warnings": warnings, "suno_share_actual": 1.0, "verses_file": str(args.verses)}
        args.brief.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        final_path = args.brief.with_suffix(".lyrics_final.txt"); final_path.write_text(final + "\n", encoding="utf-8")
        if problems:
            print(f"\n=== 問題あり。Suno で出し直すか該当行を書き換えてください。途中結果: {final_path.name} ===")
            sys.exit(1)
        print(f"\n=== 完了（お題だけモード）。Suno Custom の Lyrics 欄に貼る文: {final_path.name} ===\n")
        print(final)
        return

    if "{{SUNO_" not in skeleton:
        sys.exit("[停止] このブリーフの歌詞に差し込み位置がありません（lyrics.mode が core_fixed ではないか、既に合体済み）")
    blocks = parse_verses(text)
    needed = [ph for ph in PLACEHOLDERS if ph in skeleton]
    print(f"=== 合体を始めます：{artist['name']} ===")
    print(f"  差し込み位置: {', '.join(needed)}")
    print(f"  Suno の節: {', '.join(k for k in blocks) or 'なし'}")

    problems, warnings = [], []
    final = skeleton
    target = int(core.get("syllables_per_line_target") or 0)
    for ph in needed:
        lines = blocks.get(ph)
        if not lines:
            problems.append(f"{ph} に対応する節が Suno の出力に見つからない"); continue
        for l in lines:
            if target:
                n = count_syllables(l)
                if abs(n - target) > args.tolerance:
                    warnings.append(f"{ph}: 音節 {n}（目標 {target}）→ 「{l}」")
            if len(l) > 90:
                warnings.append(f"{ph}: 1 行が長い → 「{l[:40]}…」")
        final = final.replace(ph, "\n".join(lines))

    # 核が変わっていないか
    for l in core.get("chorus_lines", []) + ([core.get("hook_line")] if core.get("hook_line") else []):
        if l and l not in final:
            problems.append(f"核の行が最終歌詞に無い → 「{l}」")
    for tag in core.get("fixed_tags", []):
        t = tag.split("]")[-2].split("[")[-1] if "[" in tag else tag   # '[Outro] [Humming]' → 'Humming'
        if f"[{t}]" not in final:
            warnings.append(f"固定タグ [{t}] が最終歌詞に見当たらない")

    # 実名・禁止トピック
    banned = [n for n in artist.get("profile", {}).get("favorite_artists_real", [])] + \
             [i.get("name", "") for i in artist.get("persona", {}).get("influences", []) if i.get("name") not in (None, "", "（bio のみ）")]
    for b in banned:
        if b and b.lower() in final.lower():
            problems.append(f"実名が含まれている: {b}")
    for topic in artist.get("lyrics", {}).get("forbidden_topics", []):
        for kw in {"宗教": ["god", "jesus", "allah", "pray", "church"], "政治": ["president", "election", "government", "vote"],
                   "特定の人物": []}.get(topic, []):
            if re.search(rf"\b{kw}\b", final, re.I):
                problems.append(f"禁止トピック『{topic}』の語: {kw}")
    if "{{SUNO_" in final:
        problems.append("差し込み位置が残っている")

    suno_words = sum(len(" ".join(v).split()) for v in blocks.values())
    total_words = len(re.sub(r"\[[^\]]*\]", "", final).split())
    share = suno_words / total_words if total_words else 0

    for w in warnings:
        print(f"  [注意] {w}")
    for p in problems:
        print(f"  [問題] {p}")
    print(f"  Suno が書いた割合: {share:.0%}（目標 {artist.get('lyrics', {}).get('suno_share', 0.45):.0%}）")

    brief["suno_lyrics_final"] = final
    brief["merge"] = {"problems": problems, "warnings": warnings, "suno_share_actual": round(share, 2), "verses_file": str(args.verses)}
    args.brief.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    final_path = args.brief.with_suffix(".lyrics_final.txt")
    final_path.write_text(final + "\n", encoding="utf-8")
    if problems:
        print(f"\n=== 問題あり。節を直すか Suno で出し直してください。途中結果: {final_path.name} ===")
        sys.exit(1)
    print(f"\n=== 完了。Suno Custom の Lyrics 欄に貼る文: {final_path.name} ===\n")
    print(final)


if __name__ == "__main__":
    main()
