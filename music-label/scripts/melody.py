#!/usr/bin/env python3
"""
メロディー（歌の旋律）を曲から起こして、GarageBand で開ける MIDI に残し、曲作りに使う「旋律の特徴」を測る（決定 90）

流れ（Mac の中。無料。1 曲 2〜5 分）：
  1 歌を取り出す：Demucs（入っていれば。曲を歌と伴奏に分ける部品）か、librosa の歌の分離（くり返す伴奏を引き算する方法）
  2 音の高さを読む：librosa の pYIN（1 つの声の高さを時間ごとに読む）→ 音符に区切る（同じ高さが続く間を 1 音）
  3 拍に合わせる：曲のテンポの拍（16 分音符の細かさ）に音の始まりと長さをそろえる
  4 MIDI に書く：out/reference_db/melody/<アーティスト> - <曲名>.mid（GarageBand で開くと、楽譜の表示でも見られる）
  5 旋律の特徴を測る（解析シートの melody と phrases に入れる。曲の案を書く Claude に渡すのはここだけ。音の並びは渡さない）：
     音域（いちばん低い音〜高い音・幅・よく使う高さ）／動き方（順次進行・跳躍・最大の跳躍・同じ音のくり返し）／
     リズム（1 小節の音の数・裏拍の多さ・弱起〔小節の前から入る〕・フレーズの長さ・フレーズ終わりの伸ばし）／
     区切りごと（ヴァース・サビ）の輪郭（上がる・下がる・山なり・波）と最高音の位置／サビのリズムの骨格（音の高さなし）

自動の旋律の書き起こしは「おおよそ」：はっきりした歌なら大まかな旋律は取れるが、こぶし・ビブラートで音が割れる・1 オクターブずれることがある。
"""
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT  # noqa: E402

MELODY_DIR = OUT / "reference_db" / "melody"
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
DEGREES = ["1", "b2", "2", "b3", "3", "4", "#4", "5", "b6", "6", "b7", "7"]   # 主音からの度数（キーに対する位置）


def note_name(m: int) -> str:
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def demucs_ready() -> bool:
    try:
        import demucs  # noqa: F401
        return True
    except ImportError:
        return False


def vocals(path: Path, sr: int = 22050):
    """歌の部分を取り出す。(波形, サンプル数/秒, 方法)"""
    import librosa
    import numpy as np
    if demucs_ready():
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", d, str(path)],
                               capture_output=True, text=True)
            hit = list(Path(d).rglob("vocals.wav"))
            if r.returncode == 0 and hit:
                y, _ = librosa.load(str(hit[0]), sr=sr, mono=True)
                return y, sr, "demucs"
    # librosa の歌の分離：くり返す伴奏（近いフレームの中央値）を引き算して、残りを歌とみる
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    S_full, phase = librosa.magphase(librosa.stft(y))
    S_filter = librosa.decompose.nn_filter(S_full, aggregate=np.median, metric="cosine",
                                           width=int(librosa.time_to_frames(2, sr=sr)))
    S_filter = np.minimum(S_full, S_filter)
    mask_v = librosa.util.softmask(S_full - S_filter, 10 * S_filter, power=2)
    yv = librosa.istft(mask_v * S_full * phase, length=len(y))
    return yv, sr, "librosa"


def notes_from(y, sr: int, bpm: float | None) -> tuple[list[dict], float, str]:
    """歌の波形から音符を起こす。[{start_beat, beats, pitch}], テンポ, 説明"""
    import librosa
    import numpy as np
    hop = 256
    f0, voiced, prob = librosa.pyin(y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"), sr=sr,
                                    frame_length=2048, hop_length=hop)
    times = librosa.times_like(f0, sr=sr, hop_length=hop)
    midi = np.where(voiced & (prob > 0.5), librosa.hz_to_midi(np.nan_to_num(f0, nan=1.0)), np.nan)
    # 小さなゆれ（ビブラート）をならす
    sm = np.copy(midi)
    for i in range(2, len(midi) - 2):
        w = midi[i - 2:i + 3]
        if not np.isnan(midi[i]) and np.sum(~np.isnan(w)) >= 3:
            sm[i] = np.nanmedian(w)
    tuning = float(np.nanmedian((sm[~np.isnan(sm)] - np.round(sm[~np.isnan(sm)])))) if np.any(~np.isnan(sm)) else 0.0
    # 拍
    tempo = bpm
    if not tempo:
        t, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(np.atleast_1d(t)[0]) or 100.0
    beat = 60.0 / tempo
    # 音符に区切る：声があって、高さが 0.7 半音以内で続く間
    raw, cur = [], None
    for t, m in zip(times, sm):
        if np.isnan(m):
            if cur:
                raw.append(cur)
                cur = None
            continue
        p = m - tuning
        if cur and abs(p - cur["mean"]) <= 0.7:
            cur["end"] = t
            cur["vals"].append(p)
            cur["mean"] = float(np.median(cur["vals"]))
        else:
            if cur:
                raw.append(cur)
            cur = {"start": t, "end": t, "vals": [p], "mean": p}
    if cur:
        raw.append(cur)
    # 声が一瞬切れて割れた同じ高さの音をつなぐ（0.12 秒未満のすき間）
    merged: list[dict] = []
    for n in raw:
        if merged and n["start"] - merged[-1]["end"] < 0.12 and abs(n["mean"] - merged[-1]["mean"]) <= 0.7:
            merged[-1]["end"] = n["end"]
            merged[-1]["vals"] += n["vals"]
            merged[-1]["mean"] = float(np.median(merged[-1]["vals"]))
        else:
            merged.append(n)
    raw = merged
    notes = []
    for n in raw:
        dur = n["end"] - n["start"] + hop / sr
        if dur < 0.09:
            continue   # 短すぎる音（子音・ノイズ）は落とす
        pitch = int(round(n["mean"]))
        sb = round(n["start"] / beat * 4) / 4          # 16 分音符にそろえる
        nb = max(0.25, round(dur / beat * 4) / 4)
        if notes and abs(sb - notes[-1]["start_beat"]) < 1e-6:
            continue
        notes.append({"start_beat": sb, "beats": nb, "pitch": pitch})
    # 1 オクターブのずれを直す（前後とくらべて 12 半音近く飛ぶ音）
    for i in range(1, len(notes) - 1):
        a, b, c = notes[i - 1]["pitch"], notes[i]["pitch"], notes[i + 1]["pitch"]
        for k in (-12, 12):
            if abs(b + k - a) < abs(b - a) - 7 and abs(b + k - c) < abs(b - c) - 7:
                notes[i]["pitch"] = b + k
    # 重なりをなくす
    for i in range(len(notes) - 1):
        gap = notes[i + 1]["start_beat"] - notes[i]["start_beat"]
        if notes[i]["beats"] > gap:
            notes[i]["beats"] = max(0.25, gap)
    return notes, float(tempo), f"音 {len(notes)} 個（ずれの補正 {tuning:+.2f} 半音）"


def write_midi(notes: list[dict], bpm: float, path: Path, name: str = "Melody") -> None:
    """MIDI（形式 0）に書く。GarageBand・Logic で開ける"""
    ppq = 480

    def vlq(n: int) -> bytes:
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)
    ev = []
    for n in notes:
        on = int(round(n["start_beat"] * ppq))
        off = on + max(1, int(round(n["beats"] * ppq)))
        ev.append((on, 1, bytes([0x90, n["pitch"], 90])))
        ev.append((off, 0, bytes([0x80, n["pitch"], 0])))
    ev.sort(key=lambda x: (x[0], x[1]))
    body = b"\x00\xff\x03" + vlq(len(name.encode())) + name.encode()
    tempo = int(60_000_000 / max(1.0, bpm))
    body += b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big")
    body += b"\x00\xff\x58\x04\x04\x02\x18\x08"   # 4/4 拍子
    last = 0
    for t, _, data in ev:
        body += vlq(t - last) + data
        last = t
    body += b"\x00\xff\x2f\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq) + b"MTrk" + struct.pack(">I", len(body)) + body)


def _tonic(key: str | None) -> int | None:
    m = re.match(r"([A-G][#b]?)", key or "")
    if not m:
        return None
    k = m.group(1).replace("Db", "C#").replace("Eb", "D#").replace("Gb", "F#").replace("Ab", "G#").replace("Bb", "A#")
    return NAMES.index(k) if k in NAMES else None


def _contour(ps: list[int]) -> str:
    """フレーズの形：上がる・下がる・山なり・谷・波・平ら"""
    if len(ps) < 3:
        return "短い"
    a, b, c = ps[: len(ps) // 3 or 1], ps[len(ps) // 3: 2 * len(ps) // 3 or 2], ps[2 * len(ps) // 3:]
    m = [sum(x) / len(x) for x in (a, b, c) if x]
    if len(m) < 3:
        return "短い"
    d1, d2 = m[1] - m[0], m[2] - m[1]
    if abs(d1) < 1 and abs(d2) < 1:
        return "平ら"
    if d1 > 0.8 and d2 > 0.8:
        return "上がっていく"
    if d1 < -0.8 and d2 < -0.8:
        return "下がっていく"
    if d1 > 0.8 and d2 < -0.8:
        return "山なり"
    if d1 < -0.8 and d2 > 0.8:
        return "谷（下がって戻る）"
    return "波"


def phrases_of(notes: list[dict]) -> list[list[dict]]:
    """1 拍以上の休みでフレーズを分ける"""
    out, cur = [], []
    for n in notes:
        if cur and n["start_beat"] - (cur[-1]["start_beat"] + cur[-1]["beats"]) >= 1.0:
            out.append(cur)
            cur = []
        cur.append(n)
    if cur:
        out.append(cur)
    return [p for p in out if len(p) >= 3]


def _skeleton(ph: list[dict]) -> str:
    """リズムの骨格（音の高さなし）：16 分音符のマスで、音の始まりを x・それ以外を .、小節の区切りを |"""
    start = int(ph[0]["start_beat"] // 4) * 4
    end = ph[-1]["start_beat"] + ph[-1]["beats"]
    cells = int(min(32, max(4, (end - start) * 4)))   # 2 小節まで
    grid = ["."] * cells
    for n in ph:
        i = int(round((n["start_beat"] - start) * 4))
        if 0 <= i < cells:
            grid[i] = "x"
    return "|".join("".join(grid[i:i + 16]) for i in range(0, cells, 16))


def features(notes: list[dict], bpm: float, key: str | None, sections: list[dict]) -> dict:
    """旋律の特徴（曲作りに渡す。音の並びは入れない）"""
    if len(notes) < 8:
        return {"usable": False, "note": "歌の音がほとんど取れなかった（インストか、歌が聞き取りにくい曲）"}
    ps = [n["pitch"] for n in notes]
    iv = [b - a for a, b in zip(ps, ps[1:])]
    absiv = [abs(x) for x in iv]
    srt = sorted(ps)
    tonic = _tonic(key)
    deg = (lambda p: DEGREES[(p - tonic) % 12]) if tonic is not None else (lambda p: NAMES[p % 12])
    beat = 60.0 / bpm
    off = sum(1 for n in notes if abs(n["start_beat"] - round(n["start_beat"])) > 1e-6) / len(notes)
    phs = phrases_of(notes)
    total_bars = (notes[-1]["start_beat"] + notes[-1]["beats"] - notes[0]["start_beat"]) / 4 or 1
    out = {
        "usable": True,
        "range": {"low": note_name(srt[0]), "high": note_name(srt[-1]), "span_semitones": srt[-1] - srt[0],
                  "core": f"{note_name(srt[len(srt) // 4])}〜{note_name(srt[3 * len(srt) // 4])}（よく使う高さ）"},
        "motion": {"stepwise_ratio": round(sum(1 for x in absiv if x <= 2) / len(absiv), 2),
                   "leap_ratio": round(sum(1 for x in absiv if x >= 5) / len(absiv), 2),
                   "largest_leap_semitones": max(absiv), "repeated_note_ratio": round(sum(1 for x in absiv if x == 0) / len(absiv), 2),
                   "up_down_balance": round(sum(1 for x in iv if x > 0) / max(1, sum(1 for x in iv if x != 0)), 2)},
        "rhythm": {"notes_per_bar": round(len(notes) / total_bars, 1), "offbeat_ratio": round(off, 2),
                   "pickup_ratio": round(sum(1 for p in phs if p[0]["start_beat"] % 4 > 2.5) / max(1, len(phs)), 2),
                   "phrase_bars_avg": round(sum((p[-1]["start_beat"] + p[-1]["beats"] - p[0]["start_beat"]) / 4 for p in phs) / max(1, len(phs)), 1),
                   "phrase_end_hold_beats": round(sum(p[-1]["beats"] for p in phs) / max(1, len(phs)), 2),
                   "notes_per_phrase_avg": round(sum(len(p) for p in phs) / max(1, len(phs)), 1)},
        "sections": [],
    }
    for s in sections or []:   # 区切りごと：開始の度数・最高音の度数と位置・輪郭
        a = float(s.get("start_sec") or 0) / beat
        idx = sections.index(s)
        b = float(sections[idx + 1].get("start_sec") or 0) / beat if idx + 1 < len(sections) else 1e9
        sn = [n for n in notes if a <= n["start_beat"] < b]
        if len(sn) < 4:
            continue
        sp = [n["pitch"] for n in sn]
        top = max(range(len(sn)), key=lambda i: sp[i])
        out["sections"].append({"name": s.get("name"), "start_degree": deg(sp[0]), "peak": note_name(sp[top]), "peak_degree": deg(sp[top]),
                                "peak_at_beat_in_bar": round(sn[top]["start_beat"] % 4 + 1, 2), "contour": _contour(sp),
                                "span_semitones": max(sp) - min(sp), "notes": len(sn)})
    # サビ（いちばん高いところを通る区切り）のリズムの骨格と、フレーズの音の数（歌詞の音節の数の目安）
    ch = [x for x in out["sections"] if "chorus" in str(x.get("name"))] or sorted(out["sections"], key=lambda x: -x["span_semitones"])[:1]
    if ch:
        nm = ch[0]["name"]
        sec = next(s for s in sections if s.get("name") == nm)
        a = float(sec.get("start_sec") or 0) / beat
        cph = [p for p in phs if p[0]["start_beat"] >= a - 1][:2]
        out["hook"] = {"section": nm, "rhythm_skeleton": [_skeleton(p) for p in cph], "notes_per_phrase": [len(p) for p in cph],
                       "contour": [_contour([n["pitch"] for n in p]) for p in cph], "start_degree": [deg(p[0]["pitch"]) for p in cph]}
    return out


def transcribe_melody(audio: Path, sheet: dict, out_name: str) -> tuple[dict, Path | None]:
    """曲 1 つ：歌を取り出す → 音符 → MIDI → 特徴。(特徴, MIDI の場所)"""
    bpm = (sheet.get("groove") or {}).get("bpm") or None
    key = (sheet.get("_measure") or {}).get("key")
    y, sr, how = vocals(audio)
    notes, tempo, note = notes_from(y, sr, bpm)
    feat = features(notes, tempo, key, (sheet.get("structure") or {}).get("sections") or [])
    feat.update({"separation": how, "bpm_used": round(tempo, 1), "detail": note})
    mid = None
    if notes:
        safe = re.sub(r'[\\/:*?"<>|]+', "_", out_name).strip() or "melody"
        mid = MELODY_DIR / f"{safe}.mid"
        write_midi(notes, tempo, mid, out_name[:60])
        feat["midi_file"] = str(mid)
    return feat, mid


if __name__ == "__main__":   # 試しに 1 曲：python3 scripts/melody.py <音源> [テンポ] [キー]
    p = Path(sys.argv[1])
    sh = {"groove": {"bpm": float(sys.argv[2])} if len(sys.argv) > 2 else {}, "_measure": {"key": sys.argv[3] if len(sys.argv) > 3 else None}}
    f, m = transcribe_melody(p, sh, p.stem)
    print(json.dumps(f, ensure_ascii=False, indent=1))
    print("MIDI:", m, "／ 歌の取り出し：", "Demucs" if demucs_ready() else "librosa")
