#!/usr/bin/env python3
"""
参考曲の解析シート（analysis_sheet.schema.json）を、音源から自動で下書きする

入力：
  --audio   正規に入手した音源（wav / mp3 / m4a / flac）
  --title / --artist / --tags / --source / --year / --language   曲を特定する情報
  --lyrics  歌詞のテキストファイル（任意。構造の計測と Claude の分析に使う。歌詞の語そのものは生成には渡らない）
  --notes   人の耳メモ（任意。自由な日本語。奏法・エフェクト・環境音・印象などを書く）
  --web     分析した URL（任意、複数可。kind:url の形。例 mv:https://...  sns:https://...）
  --research  Claude に公開情報（Web）を調べさせ、世界観・バックボーン・ビジュアルの欄を埋める（ANTHROPIC_API_KEY が必要）

出力：
  out/references/<id>.json   ← select_references.py --references と write_brief.py --references がそのまま読む
  out/references/<id>.measure.json  ← 機械計測の生データ（確認用）

機械計測（librosa）：BPM・キー・長さ・セクション分割・エネルギー曲線・イントロ長・サビ推定・スイング率・音量の推移
歌詞の構造（規則）  ：1 行あたり音節数・韻の型・繰り返し行・タイトル語の位置
Claude              ：テーマ／起伏／トーン、耳メモの整形（奏法・エフェクト・印象）、--research で背景とビジュアル

必要なもの：pip install librosa soundfile numpy anthropic
人の作業は耳メモ（1 曲 5〜10 分）だけ。分析は著作権法 30 条の 4（情報解析）の範囲で、音源は保存しない。
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


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "track"


# ---------------------------------------------------------------------------
# 1. 機械計測
# ---------------------------------------------------------------------------
KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler のキー推定テンプレート
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def measure(audio: Path) -> dict:
    import numpy as np
    import librosa

    print("  音源を読み込んでいます…")
    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    duration = float(len(y) / sr)

    print("  テンポ（BPM）を測っています…")
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, trim=False)
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beats, sr=sr)

    print("  キーを推定しています…")
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    prof = chroma.mean(axis=1)
    best = (-2, "", "")
    for i in range(12):
        for name, tmpl in (("major", MAJOR), ("minor", MINOR)):
            t = np.roll(tmpl, i)
            r = float(np.corrcoef(prof, t)[0, 1])
            if r > best[0]:
                best = (r, KEY_NAMES[i], name)
    key = f"{best[1]} {best[2]}"

    print("  音量とエネルギーの推移を測っています…")
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    # 1 秒ごとの RMS（dB）
    per_sec = []
    for s in range(int(duration)):
        m = (times >= s) & (times < s + 1)
        v = float(rms[m].mean()) if m.any() else 0.0
        per_sec.append(20 * np.log10(v + 1e-9))
    per_sec = np.array(per_sec)
    # 全体の平均音量（LUFS の代わりの目安：RMS dBFS）
    rms_dbfs = float(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9))
    peak_dbfs = float(20 * np.log10(np.max(np.abs(y)) + 1e-9))
    # 急な無音（-45 dB 以下が 0.8 秒以上）
    silent = per_sec < (per_sec.max() - 45)
    sudden_silences = []
    run = 0
    for i, s in enumerate(silent):
        run = run + 1 if s else 0
        if run == 1 and s and 2 < i < len(silent) - 2:
            sudden_silences.append(i)

    print("  セクションを分割しています…")
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    feat = np.vstack([librosa.util.normalize(mfcc, axis=1), librosa.util.normalize(chroma, axis=1)])
    # 拍ごとに集約してから分割（安定する）
    if len(beats) > 16:
        feat_sync = librosa.util.sync(feat, beats, aggregate=np.median)
        k = int(np.clip(duration // 25, 4, 10))
        bounds = librosa.segment.agglomerative(feat_sync, k)
        bound_times = [0.0] + [float(beat_times[min(b, len(beat_times) - 1)]) for b in bounds[1:]] + [duration]
        # 短すぎる区間（4 小節未満）は隣と併合する
        min_len = 4 * 4 * 60 / tempo
        merged = [bound_times[0]]
        for t_ in bound_times[1:-1]:
            if t_ - merged[-1] >= min_len:
                merged.append(t_)
        if duration - merged[-1] < min_len and len(merged) > 1:
            merged.pop()
        bound_times = merged + [duration]
    else:
        bound_times = [0.0, duration]
    sections = []
    emax = per_sec.max() if len(per_sec) else 0
    emin = per_sec.min() if len(per_sec) else -60
    for i in range(len(bound_times) - 1):
        a, b = bound_times[i], bound_times[i + 1]
        seg = per_sec[int(a):max(int(a) + 1, int(b))]
        e = float(seg.mean()) if len(seg) else emin
        energy = int(round(1 + 9 * (e - emin) / max(1e-6, (emax - emin))))
        bars = (b - a) * tempo / 60 / 4
        sections.append({"name": f"sec{i+1}", "start_sec": round(a, 1), "end_sec": round(b, 1),
                         "bars": int(round(bars)), "energy": int(np.clip(energy, 1, 10))})
    # サビ推定：エネルギーが高い区間のうち、特徴が似た区間が他にもあるもの（繰り返し）→ 最初の出現
    if len(sections) >= 3:
        top = sorted(sections, key=lambda s: -s["energy"])[: max(2, len(sections) // 3)]
        chorus_guess = min(top, key=lambda s: s["start_sec"])
    else:
        chorus_guess = max(sections, key=lambda s: s["energy"])
    # イントロ長：最初のセクション、または最初に声/エネルギーが上がる地点
    intro_end = sections[0]["end_sec"] if sections else 0.0

    print("  グルーヴ（スイング率）を測っています…")
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    swing = None
    if len(beat_times) > 8 and len(onsets) > 8:
        ratios = []
        for i in range(len(beat_times) - 1):
            a, b = beat_times[i], beat_times[i + 1]
            mid = [o for o in onsets if a + 0.25 * (b - a) < o < a + 0.85 * (b - a)]
            if mid:
                ratios.append((min(mid, key=lambda o: abs(o - (a + 0.5 * (b - a)))) - a) / (b - a))
        if ratios:
            swing = float(np.median(ratios))   # 0.5 = 真っすぐ、0.6〜0.67 = シャッフル寄り
    # 名前の割り当て（単純な推定）：先頭 intro、最後 outro、サビ推定 chorus、他 verse/bridge
    for s in sections:
        s["label_guess"] = "intro" if s is sections[0] else "outro" if s is sections[-1] else "chorus" if s["energy"] >= chorus_guess["energy"] else "verse"
    return {
        "duration_sec": round(duration, 1), "bpm": round(tempo, 1), "key": key, "key_confidence": round(best[0], 2),
        "rms_dbfs": round(rms_dbfs, 1), "peak_dbfs": round(peak_dbfs, 1),
        "energy_per_sec_db": [round(float(v), 1) for v in per_sec],
        "sections": sections, "intro_end_sec": round(intro_end, 1),
        "chorus_guess_start_sec": chorus_guess["start_sec"], "chorus_within_30s": chorus_guess["start_sec"] <= 30,
        "sudden_silences_sec": sudden_silences, "swing_ratio": swing,
        "feel_guess": None if swing is None else ("真っすぐ" if swing < 0.56 else "シャッフル寄り" if swing < 0.63 else "シャッフル"),
    }


# ---------------------------------------------------------------------------
# 2. 歌詞の構造（規則で測れる部分）
# ---------------------------------------------------------------------------
def count_syllables(line: str) -> int:
    words = re.findall(r"[a-zA-Z']+", line.lower())
    total = 0
    for w in words:
        w2 = re.sub(r"e$", "", w) if len(w) > 2 else w
        total += max(1, len(re.findall(r"[aeiouy]+", w2)))
    return total


def rhyme_key(line: str) -> str:
    words = re.findall(r"[a-zA-Z']+", line.lower())
    if not words:
        return ""
    w = words[-1]
    m = re.search(r"[aeiouy]+[^aeiouy]*$", w)
    return m.group(0) if m else w[-2:]


def lyrics_structure(text: str, title: str) -> dict:
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    sections, seen = [], {}
    for i, p in enumerate(paras):
        lines = [l.strip() for l in p.split("\n") if l.strip() and not l.strip().startswith("[")]
        if not lines:
            continue
        key = " ".join(lines).lower()
        repeated = key in seen or sum(1 for q in paras if " ".join(l.strip() for l in q.split("\n") if l.strip()).lower() == key) > 1
        seen[key] = True
        keys = [rhyme_key(l) for l in lines]
        scheme = ""
        letters = {}
        for k in keys:
            letters.setdefault(k, chr(65 + len(letters)))
            scheme += letters[k]
        sections.append({"index": i + 1, "guess": "chorus" if repeated else "verse", "lines": len(lines),
                         "syllables_per_line": [count_syllables(l) for l in lines], "rhyme_scheme": scheme})
    title_words = [w for w in re.findall(r"[a-zA-Z']+", title.lower()) if len(w) > 2]
    all_lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("[")]
    title_hits = [i for i, l in enumerate(all_lines) if title_words and all(w in l.lower() for w in title_words)]
    return {"sections": sections, "title_line_positions": title_hits, "title_count": len(title_hits), "total_lines": len(all_lines)}


# ---------------------------------------------------------------------------
# 3. Claude：テーマ・起伏・耳メモの整形・（任意）公開情報の調査
# ---------------------------------------------------------------------------
SHEET_SCHEMA = json.loads((ROOT / "templates" / "analysis_sheet.schema.json").read_text(encoding="utf-8"))

SYSTEM = """You fill in a song-analysis sheet for an internal music database (information analysis only; nothing here is
published). You receive: identification, machine measurements (tempo, key, sections, energy, swing), the lyric
structure measured by rules, optionally the lyric text, optionally the owner's listening notes (Japanese), and
optionally web research results. Produce the sheet in the given JSON schema, in Japanese for descriptive fields.
Rules: record RECIPES, not the work itself - never quote lyric lines (key_words.word may hold single words; the
pipeline strips them before generation), never write note sequences for phrases (use rhythm skeleton + contour +
relative start degree), never write "sounds like <artist>" for the vocal (describe attributes). Where you have no
evidence, leave the field empty or write "不明" - do not invent. Keep 'signature' to one sentence."""


def call_claude(payload: dict, research: bool) -> tuple[dict | None, str]:
    try:
        import anthropic
    except ImportError:
        return None, "anthropic パッケージが無い（pip install anthropic）"
    client = anthropic.Anthropic()
    tools = []
    if research:
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
                 {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6}]
    user = ("Fill the analysis sheet.\n\n## Input\n" + json.dumps(payload, ensure_ascii=False, indent=1)
            + ("\n\nUse web search/fetch to fill worldview.artist_background, worldview.live_context and visual_web "
               "(MV / cover art / official site / SNS / artist photo analysis - methods only, no likeness). Record the URLs you used in visual_web.sources."
               if research else "\n\nDo not guess artist background or visual_web beyond what the input provides."))
    try:
        kwargs = dict(model=MODEL, max_tokens=16000,
                      system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                      messages=[{"role": "user", "content": user}],
                      output_config={"effort": "high", "format": {"type": "json_schema", "schema": SHEET_SCHEMA}},
                      betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        if tools:
            kwargs["tools"] = tools
        with client.beta.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        return None, "ANTHROPIC_API_KEY が無効（401）"
    except anthropic.RateLimitError as e:
        return None, f"回数制限か残高不足（429）: {e}"
    except anthropic.APIStatusError as e:
        return None, f"API エラー {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        return None, f"接続できない: {e}"
    if msg.stop_reason == "refusal":
        return None, "生成が拒否された"
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text), f"OK（入力 {msg.usage.input_tokens} / 出力 {msg.usage.output_tokens} トークン）"
    except json.JSONDecodeError:
        return None, "返答が JSON として読めない"


# ---------------------------------------------------------------------------
def skeleton(args, m: dict | None, ls: dict | None) -> dict:
    """Claude なしでも作れる下書き（機械計測と歌詞構造を解析シートの形に流し込む）"""
    sheet = {"reference": {"title": args.title, "artist_name": args.artist, "language": args.language, "source": args.source,
                           "tags": args.tags, "acquired_from": args.acquired_from, **({"release_year": args.year} if args.year else {})},
             "lyrics": {}, "worldview": {}, "instruments": {}, "performance": {}, "harmony": {}, "groove": {},
             "structure": {}, "phrases": [], "vocal": {}, "visual_web": {"sources": []}, "signature": "", "analyzed_by": "auto", "version": 1}
    if m:
        sheet["groove"] = {"bpm": m["bpm"], "felt_as": "そのまま", "feel": m["feel_guess"] or "不明", "swing_ratio": m["swing_ratio"]}
        sheet["harmony"] = {"progression_character": "不明", "modulation_count": 0}
        sheet["structure"] = {
            "sections": [{"name": s["label_guess"] if s["label_guess"] != "verse" else f"sec{s['name'][3:]}", "start_sec": s["start_sec"], "bars": s["bars"], "energy": s["energy"]} for s in m["sections"]],
            "intro": {"bars": m["sections"][0]["bars"] if m["sections"] else 0, "type": "不明（耳で確認）"},
            "outro": {"bars": m["sections"][-1]["bars"] if m["sections"] else 0, "type": "不明（耳で確認）"},
            "chorus": {"enters_at_sec": m["chorus_guess_start_sec"], "repetitions": sum(1 for s in m["sections"] if s["label_guess"] == "chorus")},
        }
        sheet["instruments"]["balance"] = {"loudness_curve": f"平均 {m['rms_dbfs']} dBFS、ピーク {m['peak_dbfs']} dBFS",
                                           "tuning": f"キー推定 {m['key']}（確信度 {m['key_confidence']}）"}
        sheet["_measure"] = {"duration_sec": m["duration_sec"], "key": m["key"], "chorus_within_30s": m["chorus_within_30s"],
                             "sudden_silences_sec": m["sudden_silences_sec"]}
    if ls:
        sheet["lyrics"]["syllables_per_line"] = {f"section{s['index']}({s['guess']})": s["syllables_per_line"] for s in ls["sections"]}
        schemes = [s["rhyme_scheme"] for s in ls["sections"]]
        sheet["lyrics"]["rhyme"] = {"scheme": " / ".join(schemes)}
        sheet["lyrics"]["title_word"] = {"count": ls["title_count"], "positions": [f"line {i+1}" for i in ls["title_line_positions"]]}
        sheet["lyrics"]["repetition_pattern"] = f"繰り返し段落 {sum(1 for s in ls['sections'] if s['guess']=='chorus')} / 全 {len(ls['sections'])} 段落"
    for w in args.web or []:
        kind, _, url = w.partition(":")
        if url:
            sheet["visual_web"]["sources"].append({"kind": kind if kind in ("mv", "official_site", "sns", "live", "interview", "cover_art") else "other", "url": url, "fetched_at": date.today().isoformat()})
    return sheet


def main() -> None:
    ap = argparse.ArgumentParser(description="参考曲の解析シートを音源から自動で下書きする")
    ap.add_argument("--audio", type=Path, help="音源ファイル（正規に入手したもの）")
    ap.add_argument("--title", required=True); ap.add_argument("--artist", required=True)
    ap.add_argument("--tags", nargs="*", default=[]); ap.add_argument("--source", default="manual", choices=["own", "chart", "manual", "trend"])
    ap.add_argument("--year", type=int); ap.add_argument("--language", default="en"); ap.add_argument("--acquired-from", default="")
    ap.add_argument("--lyrics", type=Path, help="歌詞テキスト（分析用。生成には語そのものは渡らない）")
    ap.add_argument("--notes", type=Path, help="人の耳メモ（自由な日本語）")
    ap.add_argument("--web", nargs="*", help="kind:url（mv / official_site / sns / live / interview / cover_art）")
    ap.add_argument("--research", action="store_true", help="Claude に公開情報を調べさせる（Web 検索）")
    ap.add_argument("--no-claude", action="store_true", help="Claude を使わず機械計測と規則だけで下書き")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "references")
    args = ap.parse_args()

    rid = slugify(f"{args.artist}-{args.title}")
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"=== 解析シートの下書き：{args.artist} - {args.title}（id: {rid}）===")

    m = None
    if args.audio:
        if not args.audio.exists():
            sys.exit(f"[停止] 音源がありません: {args.audio}")
        m = measure(args.audio)
        (args.out / f"{rid}.measure.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  計測結果: {m['duration_sec']} 秒 / {m['bpm']} BPM / {m['key']} / セクション {len(m['sections'])} / "
              f"サビ推定 {m['chorus_guess_start_sec']} 秒{'（30 秒以内）' if m['chorus_within_30s'] else '（30 秒超）'} / "
              f"ノリ {m['feel_guess'] or '不明'}" + (f" / 急な無音 {m['sudden_silences_sec']} 秒" if m['sudden_silences_sec'] else ""))
    else:
        print("  音源なし：歌詞・耳メモ・Web だけで下書きします")

    ls = None
    lyrics_text = None
    if args.lyrics and args.lyrics.exists():
        lyrics_text = args.lyrics.read_text(encoding="utf-8")
        ls = lyrics_structure(lyrics_text, args.title)
        print(f"  歌詞の構造: {len(ls['sections'])} 段落 / タイトル語 {ls['title_count']} 回 / 音節 " +
              ", ".join("-".join(map(str, s["syllables_per_line"][:4])) for s in ls["sections"][:3]) + " …")
    notes = args.notes.read_text(encoding="utf-8") if args.notes and args.notes.exists() else ""

    sheet = skeleton(args, m, ls)
    sheet["id"] = rid

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if not args.no_claude and has_key:
        print(f"  Claude（{MODEL}）で欄を埋めています" + ("（公開情報の調査あり）" if args.research else "") + "…")
        payload = {"identification": sheet["reference"], "measurements": {k: v for k, v in (m or {}).items() if k != "energy_per_sec_db"},
                   "energy_curve_db_every_10s": (m or {}).get("energy_per_sec_db", [])[::10],
                   "lyric_structure": ls, "lyrics_text": lyrics_text, "listening_notes_ja": notes,
                   "web_sources": sheet["visual_web"]["sources"], "draft_sheet": sheet}
        result, status = call_claude(payload, args.research)
        print(f"  {status}")
        if result:
            result["reference"] = {**sheet["reference"], **result.get("reference", {})}
            result["id"] = rid
            result["_measure"] = sheet.get("_measure", {})
            result["analyzed_by"] = "auto + claude" + (" + web" if args.research else "") + (" + human" if notes else "")
            sheet = result
    elif not args.no_claude:
        print("  ANTHROPIC_API_KEY が無いので、機械計測と規則だけの下書きにします（耳メモは notes 欄にそのまま保存）")
    if notes and "performance" in sheet and not sheet["performance"].get("techniques"):
        sheet.setdefault("_listening_notes_ja", notes)

    out = args.out / f"{rid}.json"
    out.write_text(json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  → {out.relative_to(ROOT)} を書き出しました")
    todo = []
    if not notes: todo.append("耳メモ（奏法・エフェクト・環境音・印象）を --notes で追加")
    if not lyrics_text and args.language != "none": todo.append("歌詞テキストを --lyrics で追加（音節数と韻の計測）")
    if not sheet.get("visual_web", {}).get("sources"): todo.append("MV / 公式サイト / SNS の URL を --web で追加")
    if todo:
        print("  まだ埋まっていない項目: " + " / ".join(todo))
    print("=== 完了 ===")


if __name__ == "__main__":
    main()
