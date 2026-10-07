#!/usr/bin/env python3
"""
Suno で作った数テイクを機械で計測し、レーベルの自動化段階（Tier）に合わせて選ぶ。

  Tier A（本体）  : 全テイクを計測して「聴く順番」を出すだけ。決めるのは人
  Tier B（子・立ち上げ期）: 6 テイク → 2 テイクに絞る。人が 2 つから選ぶ
  Tier C（子・安定期）    : 機械が 1 テイクを選ぶ。週の 20% は人が抜き取り確認

見るもの（docs/07_sublabels.md の 6 項目）
  1. 長さ        設定書の length_sec_min〜max
  2. 音量        元の音量・ピーク・無音や急な音量差（-14 LUFS への調整は master_track.py が行う）
  3. サビの位置  hook_within_sec 秒以内にエネルギーの山（0 の組は「山を作らない」ことを評価）
  4. 歌詞の一致  文字起こし（Whisper）とブリーフの最終歌詞の一致率
  5. 癖の有無    ooh / la la / hum など、文字で確かめられる癖が入っているか
  6. 終わり方    途中で切れていない（最後が大きな音のまま終わっていない）

使い方
  # テイクを out/takes/<ブリーフ名>/ に置く（take_01.mp3, take_02.mp3 …）
  python scripts/select_takes.py out/briefs/2026-10-05_light.json
  python scripts/select_takes.py out/briefs/2026-10-05_light.json --takes ~/Downloads/light_takes
  python scripts/select_takes.py out/briefs/2026-10-05_light.json --choose 3 --note "サビの裏声がいちばん細い"
  python scripts/select_takes.py --demo          # 合成音で動作確認（Suno の音源がなくても試せる）

文字起こし
  faster-whisper か openai-whisper が入っていれば自動で使う（pip install faster-whisper）。
  入っていなければ、テイクと同じ名前の .txt（take_01.txt）を文字起こしとして読む。どちらも無ければ「未計測」。
"""
from __future__ import annotations

import argparse
import difflib
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, length_spec, load_artist, load_label, read_json, step, write_json  # noqa: E402

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aif", ".aiff", ".ogg"}
SAMPLE_RATE_C = 0.2  # Tier C の抜き取り率

# 文字で確かめられる癖 → 文字起こしの中で探す語
TIC_WORDS = {
    "ooh": r"\b(o+h+|oo+)\b",
    "ah": r"\bah+\b",
    "la": r"\b(la[\s-]*){2,}",
    "hum": r"\b(m{2,}|hm+|mm+)\b",
    "ハミング": r"\b(m{2,}|hm+|mm+)\b",
    "na na": r"\b(na[\s-]*){2,}",
    "yeah": r"\byeah\b",
    "hey": r"\bhey\b",
}


# ---------------------------------------------------------------------------
# 計測
# ---------------------------------------------------------------------------
def ebur128(path: Path) -> dict:
    """ffmpeg で音量（統合ラウドネス LUFS・ラウドネスの幅 LRA・トゥルーピーク dBTP）を測る"""
    if not shutil.which("ffmpeg"):
        return {}
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = r.stderr
    summary = txt[txt.rfind("Summary:"):] if "Summary:" in txt else txt
    out = {}
    for key, pat in (("lufs", r"I:\s+(-?[\d.]+|-inf) LUFS"), ("lra", r"LRA:\s+(-?[\d.]+) LU"),
                     ("true_peak_db", r"Peak:\s+(-?[\d.]+|-inf) dBFS")):
        m = re.search(pat, summary)
        if m and m.group(1) != "-inf":
            out[key] = float(m.group(1))
    return out


def energy_profile(y: np.ndarray, sr: int, hop_sec: float = 0.1) -> tuple[np.ndarray, float]:
    import librosa
    hop = int(sr * hop_sec)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    return db, hop_sec


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) < n:
        return x
    return np.convolve(x, np.ones(n) / n, mode="same")


def measure_audio(path: Path, spec: dict) -> dict:
    import librosa
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    dur = len(y) / sr
    db, hop = energy_profile(y, sr)
    med = float(np.median(db))
    m: dict = {"duration_sec": round(dur, 1), "median_db": round(med, 1)}

    # 無音：曲の途中（頭 2 秒と最後 4 秒を除く）で、中央値より 35 dB 以上小さい区間が 1 秒以上続く
    inner = db[int(2 / hop): max(int(2 / hop) + 1, len(db) - int(4 / hop))]
    quiet = inner < med - 35
    longest, run = 0, 0
    for q in quiet:
        run = run + 1 if q else 0
        longest = max(longest, run)
    m["longest_gap_sec"] = round(longest * hop, 1)

    # 急な音量差：1 秒平均で、隣どうしが 15 dB 以上違う箇所の数（頭と最後を除く）
    sec = smooth(db, int(1 / hop))[:: int(1 / hop)]
    jumps = np.abs(np.diff(sec[2:-4])) if len(sec) > 8 else np.array([])
    m["sudden_jumps"] = int((jumps >= 15).sum())

    # サビの山：2 秒平均のエネルギーが「曲の上位 10%」の 9 割の高さに初めて届く時刻
    s2 = smooth(db, int(2 / hop))
    p90, p20 = float(np.percentile(s2, 90)), float(np.percentile(s2, 20))
    first_peak = next((i * hop for i, v in enumerate(s2) if v >= p90 - 1.5), None)
    m["first_peak_sec"] = round(first_peak, 1) if first_peak is not None else None
    m["contrast_db"] = round(p90 - p20, 1)  # 山と谷の差（サビの盛り上がり）

    # 終わり方：最後 0.3 秒が中央値より何 dB 小さいか。小さいほど自然に終わっている
    tail = float(np.mean(db[-max(1, int(0.3 / hop)):]))
    m["tail_drop_db"] = round(med - tail, 1)

    m.update(ebur128(path))
    return m


def transcribe(path: Path) -> tuple[str | None, str]:
    """文字起こし。faster-whisper → openai-whisper → 同名 .txt の順に試す"""
    side = path.with_suffix(".txt")
    try:
        from faster_whisper import WhisperModel  # type: ignore
        model = WhisperModel("small", compute_type="int8")
        segs, _ = model.transcribe(str(path), language="en")
        return " ".join(s.text for s in segs), "faster-whisper"
    except ImportError:
        pass
    try:
        import whisper  # type: ignore
        model = whisper.load_model("small")
        return model.transcribe(str(path), language="en")["text"], "openai-whisper"
    except ImportError:
        pass
    if side.exists():
        return side.read_text(encoding="utf-8"), "テキストファイル"
    return None, "なし"


def normalize_words(text: str) -> list[str]:
    text = re.sub(r"\[[^\]]*\]", " ", text)          # [Chorus] などのタグを外す
    text = re.sub(r"\([^)]*\)", " ", text)            # (ooh) などの括弧書きも外す
    return re.findall(r"[a-z']+", text.lower())


def lyric_match(transcript: str, lyrics: str) -> float:
    a, b = normalize_words(transcript), normalize_words(lyrics)
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def tic_targets(artist: dict) -> list[tuple[str, str]]:
    """設定書の tics から、文字で確かめられるものだけ取り出す"""
    tics = ((artist.get("vocal") or {}).get("signature_techniques") or {}).get("tics") or []
    out = []
    for t in tics:
        tech = str(t.get("technique", ""))
        if t.get("frequency") not in (None, "every_song"):
            continue
        for key, pat in TIC_WORDS.items():
            if key in tech.lower() or key in tech:
                out.append((tech, pat))
                break
    return out


# ---------------------------------------------------------------------------
# 判定と採点
# ---------------------------------------------------------------------------
def judge(m: dict, spec: dict, match: float | None, tics: list[tuple[str, str]], transcript: str | None,
          instrumental: bool) -> dict:
    checks: dict[str, dict] = {}

    lo, hi = spec["length_sec_min"], spec["length_sec_max"]
    checks["長さ"] = {"ok": lo <= m["duration_sec"] <= hi, "detail": f"{m['duration_sec']} 秒（{lo}〜{hi}）"}

    ok_vol = m["longest_gap_sec"] < 1.0 and m["sudden_jumps"] == 0
    if not spec.get("no_sudden_silence", True):
        ok_vol = True
    checks["音量"] = {"ok": ok_vol,
                      "detail": f"途中の無音 最長 {m['longest_gap_sec']} 秒 / 急な音量差 {m['sudden_jumps']} か所"
                                + (f" / 元の音量 {m['lufs']} LUFS・ピーク {m.get('true_peak_db')} dBTP" if "lufs" in m else "")}

    hook = spec.get("hook_within_sec", 30)
    if hook and hook > 0:
        fp = m["first_peak_sec"]
        checks["サビの位置"] = {"ok": fp is not None and fp <= hook,
                                "detail": f"最初の山 {fp} 秒（{hook} 秒以内）"}
    else:
        checks["サビの位置"] = {"ok": m["contrast_db"] <= 9, "detail": f"山と谷の差 {m['contrast_db']} dB（山を作らない組：9 dB 以下）"}

    if instrumental:
        checks["歌詞の一致"] = {"ok": True, "detail": "歌なし（判定しない）"}
    elif match is None:
        checks["歌詞の一致"] = {"ok": None, "detail": "未計測（文字起こしなし）"}
    else:
        checks["歌詞の一致"] = {"ok": match >= 0.6, "detail": f"一致率 {int(match * 100)}%（60% 以上）"}

    if not tics or instrumental:
        checks["癖"] = {"ok": None, "detail": "文字で確かめられる癖なし（聴いて確認）"}
    elif transcript is None:
        checks["癖"] = {"ok": None, "detail": "未計測（文字起こしなし）"}
    else:
        found = [t for t, pat in tics if re.search(pat, transcript.lower())]
        checks["癖"] = {"ok": len(found) == len(tics), "detail": f"{len(found)}/{len(tics)} 個 入っている"}

    checks["終わり方"] = {"ok": m["tail_drop_db"] >= 12,
                          "detail": f"最後の 0.3 秒は中央値より {m['tail_drop_db']} dB 小さい（12 dB 以上で自然な終わり）"}

    hard_fail = [k for k in ("長さ", "音量", "サビの位置", "終わり方", "歌詞の一致") if checks[k]["ok"] is False]
    # 採点（100 点満点）。必須を満たしたうえで「サビの盛り上がり」「歌詞の一致」「癖」「尺の中心」を見る
    score = 0.0
    center = (lo + hi) / 2
    score += 15 * max(0.0, 1 - abs(m["duration_sec"] - center) / max(1, (hi - lo) / 2))
    if hook and hook > 0:
        score += 35 * min(1.0, m["contrast_db"] / 12)
    else:
        score += 35 * max(0.0, 1 - m["contrast_db"] / 12)
    score += 30 * (1.0 if instrumental else (match if match is not None else 0.5))
    tic_ok = checks["癖"]["ok"]
    score += 10 * (1.0 if tic_ok else 0.5 if tic_ok is None else 0.0)
    score += 10 * min(1.0, m["tail_drop_db"] / 24)
    return {"checks": checks, "hard_fail": hard_fail, "passed": not hard_fail, "score": round(score, 1)}


def decide(results: list[dict], tier: str, seed_key: str) -> dict:
    passed = sorted([r for r in results if r["passed"]], key=lambda r: -r["score"])
    order = [r["take"] for r in sorted(results, key=lambda r: (not r["passed"], -r["score"]))]
    d: dict = {"tier": tier, "listen_order": order}
    if not passed:
        d.update(action="regenerate", message="条件を満たすテイクがありません。Suno で出し直してください（落ちた理由は各テイクの ✕ を参照）")
        return d
    if tier == "A":
        d.update(action="human_all", message="全テイクを人が聴いて選びます（下の順番で聴くと早い）")
    elif tier == "B":
        d.update(action="human_from_shortlist", shortlist=[r["take"] for r in passed[:2]],
                 message="機械が 2 テイクに絞りました。この 2 つを聴いて選んでください")
    else:
        sampled = random.Random(seed_key).random() < SAMPLE_RATE_C
        d.update(action="auto", selected=passed[0]["take"], spot_check=sampled,
                 message="機械が 1 テイクを選びました" + ("。今週は抜き取り確認の対象です（人が 1 回聴いてください）" if sampled else ""))
    return d


# ---------------------------------------------------------------------------
# デモ（合成音）
# ---------------------------------------------------------------------------
def make_demo(folder: Path) -> Path:
    import soundfile as sf
    sr = 22050
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)

    def tone(sec, amp, f=220.0):
        t = np.arange(int(sec * sr)) / sr
        return amp * (np.sin(2 * np.pi * f * t) + 0.3 * rng.standard_normal(len(t)))

    def fade(x, sec):
        n = int(sec * sr)
        x[-n:] *= np.linspace(1, 0, n) ** 2
        return x

    takes = {
        # 良い：静かな A メロ → 20 秒で山 → 自然に終わる
        "take_01": fade(np.concatenate([tone(20, .05), tone(40, .3), tone(30, .06), tone(40, .3), tone(40, .3, 330)]), 6),
        # 長すぎ
        "take_02": fade(np.concatenate([tone(20, .05), tone(200, .3)]), 6),
        # 途中に無音
        "take_03": fade(np.concatenate([tone(20, .05), tone(60, .3), np.zeros(3 * sr), tone(90, .3)]), 6),
        # 途中で切れる（最後まで大音量）
        "take_04": np.concatenate([tone(20, .05), tone(150, .3)]),
        # 山が遅い（50 秒）
        "take_05": fade(np.concatenate([tone(50, .05), tone(120, .3)]), 6),
        # まあまあ：山の差が小さい
        "take_06": fade(np.concatenate([tone(20, .15), tone(150, .3)]), 6),
    }
    for name, y in takes.items():
        sf.write(folder / f"{name}.wav", y.astype(np.float32), sr)
        (folder / f"{name}.txt").write_text(
            "ooh first light on the window, east we go, la la la" if name in ("take_01", "take_06")
            else "first light on the window", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Suno のテイクを計測して選ぶ（Tier A/B/C）")
    ap.add_argument("brief", nargs="?", help="out/briefs/<週>_<組>.json")
    ap.add_argument("--takes", help="テイクのフォルダ（既定：out/takes/<ブリーフ名>/）")
    ap.add_argument("--lyrics", help="最終歌詞（既定：ブリーフと同じ名前の .lyrics_final.txt か suno_lyrics）")
    ap.add_argument("--tier", choices=["A", "B", "C"], help="自動化の段階を上書き")
    ap.add_argument("--choose", type=int, help="人が選んだテイク番号を記録する（Tier A/B）")
    ap.add_argument("--note", default="", help="選んだ理由のメモ")
    ap.add_argument("--demo", action="store_true", help="合成音で動作確認")
    args = ap.parse_args()

    if args.demo:
        brief_path = None
        brief = {"artist_slug": "light", "label_slug": "drive", "week_start": "demo", "suno_lyrics": "first light on the window, east we go"}
        folder = make_demo(OUT / "takes" / "demo")
        step(f"合成音のテイクを 6 本作りました: {folder.relative_to(ROOT)}")
    else:
        if not args.brief:
            sys.exit("[エラー] ブリーフのファイルを指定してください（例：out/briefs/2026-10-05_light.json）。動作確認は --demo")
        brief_path = Path(args.brief)
        if not brief_path.exists():
            sys.exit(f"[エラー] ブリーフがありません: {brief_path}")
        brief = read_json(brief_path)
        folder = Path(args.takes) if args.takes else OUT / "takes" / brief_path.stem

    if args.choose is not None:
        sel_path = folder / "selection.json"
        if not sel_path.exists():
            sys.exit("[エラー] 先に計測してください（--choose なしで 1 回実行）")
        sel = read_json(sel_path)
        take = next((r for r in sel["takes"] if r["take_no"] == args.choose), None)
        if not take:
            sys.exit(f"[エラー] take {args.choose} がありません")
        sel["decision"].update(selected=take["take"], chosen_by="human", note=args.note)
        write_json(sel_path, sel)
        step(f"take {args.choose}（{take['take']}）を選んだと記録しました → 次は master_track.py で音量を整えます")
        return

    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        sys.exit(f"[案内] テイクのフォルダを作りました: {folder}\n"
                 "        Suno からダウンロードしたテイクを take_01.mp3, take_02.mp3 … の名前で置いて、もう一度実行してください")
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT)
    if not files:
        sys.exit(f"[案内] {folder} に音源がありません。Suno のテイク（mp3 / wav）を置いてください")

    artist = load_artist(brief["artist_slug"], brief.get("label_slug"))
    label = load_label(brief.get("label_slug"))
    spec = length_spec(artist, label)
    tier = args.tier or label.get("automation_tier", "B")
    instrumental = (artist.get("lyrics") or {}).get("mode") == "instrumental"

    lyrics = ""
    if args.lyrics:
        lyrics = Path(args.lyrics).read_text(encoding="utf-8")
    elif brief_path and brief_path.with_suffix(".lyrics_final.txt").exists():
        lyrics = brief_path.with_suffix(".lyrics_final.txt").read_text(encoding="utf-8")
    else:
        lyrics = brief.get("suno_lyrics") or ""
    tics = tic_targets(artist)

    step(f"{artist.get('name')}：テイク {len(files)} 本を計測します（段階 Tier {tier}）")
    results = []
    for i, f in enumerate(files, 1):
        step(f"  {f.name}：長さ・音量・サビの位置・終わり方を測っています")
        m = measure_audio(f, spec)
        transcript, how = (None, "歌なし") if instrumental else transcribe(f)
        if transcript is not None:
            step(f"  {f.name}：文字起こし（{how}）と歌詞を照合しています")
        match = lyric_match(transcript, lyrics) if (transcript and lyrics) else None
        j = judge(m, spec, match, tics, transcript, instrumental)
        num = re.search(r"(\d+)", f.stem)
        results.append({"take": f.name, "take_no": int(num.group(1)) if num else i, "path": str(f),
                        "measure": m, "transcript_source": how, "lyric_match": match, **j})

    seed_key = f"{brief.get('week_start')}|{brief.get('artist_slug')}|spot_check"
    decision = decide(results, tier, seed_key)

    print()
    print(f"{'テイク':<14}{'判定':<6}{'点':>6}  落ちた理由")
    for r in sorted(results, key=lambda r: (not r["passed"], -r["score"])):
        print(f"{r['take']:<14}{'○' if r['passed'] else '✕':<6}{r['score']:>6}  {'、'.join(r['hard_fail']) or '—'}")
    print()
    for r in results:
        print(f"[{r['take']}]")
        for k, c in r["checks"].items():
            mark = "○" if c["ok"] else ("✕" if c["ok"] is False else "－")
            print(f"   {mark} {k}: {c['detail']}")
    print()
    print(f"■ {decision['message']}")
    if decision.get("shortlist"):
        print(f"   候補: {', '.join(decision['shortlist'])}")
    if decision.get("selected"):
        print(f"   選択: {decision['selected']}")
    if decision["action"] in ("human_all", "human_from_shortlist"):
        print("   聴いて決めたら:  python scripts/select_takes.py <ブリーフ> --choose <番号> --note \"理由\"")

    out = {"brief": str(brief_path) if brief_path else "demo", "artist_slug": brief["artist_slug"],
           "label_slug": brief.get("label_slug"), "spec": spec, "takes": results, "decision": decision}
    if decision.get("selected"):
        decision["chosen_by"] = "machine"
    prev_path = folder / "selection.json"
    if prev_path.exists():
        prev = read_json(prev_path).get("decision", {})
        if prev.get("chosen_by") == "human" and any(r["take"] == prev.get("selected") for r in results):
            # 計測をやり直しても、人が選んだテイクは消さない
            decision.update(selected=prev["selected"], chosen_by="human", note=prev.get("note", ""))
            print(f"   （前回あなたが選んだ {prev['selected']} をそのまま残しました）")
        if read_json(prev_path).get("master"):
            out["master"] = read_json(prev_path)["master"]
    write_json(prev_path, out)
    step(f"結果を保存しました: {(folder / 'selection.json').relative_to(ROOT) if folder.is_relative_to(ROOT) else folder / 'selection.json'}")


if __name__ == "__main__":
    main()
