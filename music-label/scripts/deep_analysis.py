#!/usr/bin/env python3
"""
参考曲の深い分析（決定 180）：1 曲を、音・和音・楽器・リズム・盛り上がりの面から細かく測る

  測るもの（音源 1 本から）
    テンポと拍      … BPM・拍子（4 拍子 / 3 拍子）・テンポの揺れ・小節の頭
    和音            … キー・拍ごとのコード・度数で書いた進行（I–V–vi–IV など）・繰り返しの型・コードが変わる速さ・転調・キーの外のコード
    音の多さ        … 1 小節あたりの音の立ち上がりの数・16 分の埋まり具合・同時に鳴る音の高さの数・音の帯の埋まり具合
    テンション      … 小節ごとの盛り上がり（音量・明るさ・音の多さ・和音の緊張）の曲線、山の位置と形、いちばん大きい上がり下がり
    音づくり        … 左右の広がり・低音〜高音の割合・音量の幅
  さらに、メロディーの書き起こし（melody.py）の MIDI があれば
    楽器の構成      … 楽器ごとの鳴っている割合・入ってくる場所・区切りごとの出入り・同時に鳴る楽器の数
    ドラムの型      … キック・スネア・ハイハットを 1 小節 16 マスに並べた型（4 つ打ち・バックビート など）
    ベースの動き    … コードの根音をなぞる割合・音の長さ・キックとの重なり
    ボーカル        … 歌っている割合・入り・区切りごとの有無・ハモリの割合・いちばん高い音の場所
    楽譜            … ボーカルの旋律＋コード名＋ベースの楽譜（MusicXML。MuseScore などで開ける）
  最後に、分析だけから書いた「再現カード」（Suno のスタイル欄に入る形の英文＋区切りごとの指示）を作る。
  これは **分析の答え合わせ用** で、配信する曲には使わない（配信する曲は「1 曲 1 枠」の決まりで、枠ごとに別の曲から借りる）。
  実名・曲名・歌詞・旋律の音の並びは、カードに入れない。

  使い方
    python3 scripts/deep_analysis.py <音源> [--midi <MIDI>] [--json]      # 1 曲を測って表示
    python3 scripts/deep_analysis.py compare <A の結果.json> <B の結果.json>  # 2 曲の近さ（再現カードで作った曲との答え合わせ）
  ふだんは reference_db.py --audio-deep が、既存楽曲を順に測って解析シートに入れる。
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys
from pathlib import Path

VERSION = 1
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
ROMAN = ["I", "bII", "II", "bIII", "III", "IV", "#IV", "V", "bVI", "VI", "bVII", "VII"]
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
DIATONIC = {"major": {(0, "maj"), (2, "min"), (4, "min"), (5, "maj"), (7, "maj"), (9, "min"), (11, "min")},
            "minor": {(0, "min"), (3, "maj"), (5, "min"), (7, "min"), (7, "maj"), (8, "maj"), (10, "maj"), (2, "min")}}
# 和音の緊張（主和音からの遠さ）：主＝0・下属＝0.35・属＝0.7・キーの外＝0.9
FUNCTION = {"major": {0: 0.0, 9: 0.15, 4: 0.25, 5: 0.35, 2: 0.4, 7: 0.7, 11: 0.8},
            "minor": {0: 0.0, 3: 0.15, 8: 0.3, 5: 0.35, 10: 0.5, 7: 0.7, 2: 0.8}}
TRACK_JA = {"vocals": "ボーカル", "guitar": "ギター", "keys": "キーボード", "synth_lead": "打ち込み（リード）", "synth_pad": "打ち込み（パッド）",
            "synth_bass": "打ち込み（ベース）", "bass": "ベース", "drums": "ドラム"}
TRACK_EN = {"vocals": "vocals", "guitar": "guitar", "keys": "piano/keys", "synth_lead": "synth lead", "synth_pad": "synth pad",
            "synth_bass": "synth bass", "bass": "bass", "drums": "drums"}
MIDI_NAME = {"vocals": "vocals", "guitar": "guitar", "keys": "keys", "synth lead": "synth_lead", "synth pad": "synth_pad",
             "synth bass": "synth_bass", "bass": "bass", "drums": "drums"}
BANDS = [("sub", 20, 60), ("bass", 60, 250), ("low_mid", 250, 500), ("mid", 500, 2000), ("presence", 2000, 6000), ("air", 6000, 11000)]


def note_name(m: int) -> str:
    return f"{NAMES[m % 12]}{m // 12 - 1}"


# ---------------------------------------------------------------------------
# 1. 音楽の決まりごと（音源が無くても動く部分）
# ---------------------------------------------------------------------------
def roman(root: int, quality: str, tonic: int, ext: str = "") -> str:
    """コードを、キーの主音から数えた度数で書く（長和音は大文字・短和音は小文字）"""
    r = ROMAN[(root - tonic) % 12]
    if quality == "min":
        head = r[:len(r) - len(r.lstrip("b#"))]
        r = head + r[len(head):].lower()
    return r + ext


def chord_name(root: int, quality: str, ext: str = "", flats: bool = False) -> str:
    n = (FLATS if flats else NAMES)[root % 12]
    if quality == "min":
        return n + ("m7" if ext == "7" else "m")
    return n + ext


def parse_key(key: str | None) -> tuple[int, str] | None:
    m = re.match(r"\s*([A-Ga-g])([#b♯♭]?)\s*(major|minor|maj|min|m)?", str(key or ""))
    if not m:
        return None
    pc = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1).upper()]
    pc += 1 if m.group(2) in ("#", "♯") else -1 if m.group(2) in ("b", "♭") else 0
    return pc % 12, ("minor" if (m.group(3) or "").lower() in ("minor", "min", "m") else "major")


def key_fifths(tonic: int, mode: str) -> int:
    """調号（シャープの数。フラットは負）"""
    rel = (tonic + 3) % 12 if mode == "minor" else tonic
    return {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: -5, 8: -4, 3: -3, 10: -2, 5: -1}[rel]


def find_loop(bars: list[str]) -> dict:
    """小節ごとのコード（文字）の列から、いちばんよく繰り返す型（4 小節 → 2 小節 → 8 小節の順に探す）"""
    best: dict = {}
    n = len(bars)
    for size in (4, 2, 8):
        if n < size * 2:
            continue
        count: dict[tuple, int] = {}
        for i in range(0, n - size + 1):
            g = tuple(bars[i:i + size])
            if "N" in g or len(set(g)) < (2 if size > 2 else 1):
                continue
            count[g] = count.get(g, 0) + 1
        if not count:
            continue
        # 回した形（同じ並びを 1 つずらしただけ）は同じ型として数える
        groups: dict[tuple, int] = {}
        for g, c in count.items():
            rots = [g[k:] + g[:k] for k in range(size)]
            key = min(rots)
            groups[key] = groups.get(key, 0) + c
        key, hits = max(groups.items(), key=lambda kv: kv[1])
        rots = {key[k:] + key[:k] for k in range(size)}
        aligned = {g: sum(1 for i in range(0, n - size + 1, size) if tuple(bars[i:i + size]) == g) for g in count if g in rots}
        shape = max(aligned, key=lambda g: (aligned[g], count[g]))   # 区切りの頭から始まる向きで書く
        covered = [False] * n
        for i in range(0, n - size + 1):
            if tuple(bars[i:i + size]) in rots:
                for k in range(size):
                    covered[i + k] = True
        cov = sum(covered) / n
        if cov > best.get("coverage", 0) + (0.15 if best else 0):   # 長さを変えるのは、はっきり多いときだけ
            best = {"bars": size, "pattern": list(shape), "coverage": round(cov, 2), "times": hits}
    return best


def arc_shape(curve: list[float]) -> str:
    """盛り上がりの曲線の形を言葉にする"""
    n = len(curve)
    if n < 6:
        return "短くて判定できません"
    srt = sorted(curve)
    spread = srt[int(n * 0.95) - 1] - srt[int(n * 0.05)]
    if spread < 15:
        return "ほぼ一定（大きな山が無い）"
    t = [sum(curve[i * n // 3:(i + 1) * n // 3]) / max(1, (i + 1) * n // 3 - i * n // 3) for i in range(3)]
    hi = srt[int(n * 0.8)]
    peaks, last = 0, -99
    for i, v in enumerate(curve):
        if v >= hi and i - last >= 8:
            peaks += 1
        if v >= hi:
            last = i
    top = max(range(n), key=lambda i: curve[i]) / n
    if t[2] > t[1] > t[0] and t[2] - t[0] > 12:
        return "右肩上がり（最後がいちばん高い）"
    if peaks >= 2:
        return f"山が {peaks} 回（上がって下がるを繰り返す）" + ("・最後がいちばん高い" if top > 0.7 else "")
    if top > 0.75:
        return "終盤に 1 つ大きな山"
    if top < 0.3:
        return "序盤が高く、後ろへ落ち着く"
    return "中盤に 1 つ大きな山"


def drum_grid(hits: list[tuple[float, int]], pos, steps: int = 16) -> dict:
    """ドラムの音（時刻, 種類）を、1 小節のマス目（16 分）に畳んで型にする。pos(時刻) → (小節の番号, 小節の中の位置 0〜1)"""
    kinds = {"kick": {36, 35}, "snare": {38, 40, 37}, "hat": {42, 44, 46}}
    cnt = {k: [0] * steps for k in kinds}
    bars: dict[str, set] = {k: set() for k in kinds}
    allbars: set = set()
    for t, note in hits:
        p = pos(t)
        if p is None:
            continue
        bar, frac = p
        step = int(round(frac * steps))
        if step >= steps:
            bar, step = bar + 1, 0
        allbars.add(bar)
        for k, ns in kinds.items():
            if note in ns:
                cnt[k][step] += 1
                bars[k].add(bar)
    nb = max(1, len(allbars))
    out: dict = {"bars_with_drums": len(allbars), "steps": steps}
    if not allbars:
        return out
    prob = {k: [c / nb for c in cnt[k]] for k in kinds}
    out["pattern"] = {k: "".join("x" if p >= 0.45 else "o" if p >= 0.22 else "." for p in prob[k]) for k in kinds}
    out["hits_per_bar"] = {k: round(sum(cnt[k]) / nb, 1) for k in kinds}
    if steps == 16:
        k, s, h = prob["kick"], prob["snare"], prob["hat"]
        on = lambda v, idx, th=0.45: all(v[i] >= th for i in idx)   # noqa: E731
        labels = []
        if on(k, (0, 4, 8, 12)):
            labels.append("4 つ打ち（キックが毎拍）")
        elif on(k, (0, 8)) and not on(k, (4, 12), 0.3):
            labels.append("キックは 1・3 拍目が軸")
        elif on(k, (0,)):
            labels.append("キックは小節の頭＋食い（シンコペーション）" if sum(1 for i in range(16) if i % 4 and k[i] >= 0.3) else "キックは小節の頭だけ")
        if on(s, (4, 12)):
            labels.append("スネアは 2・4 拍目（バックビート）")
        elif on(s, (8,)) and s[4] < 0.25 and s[12] < 0.25:
            labels.append("スネアは 3 拍目だけ（ハーフタイム）")
        elif sum(s) < 0.4:
            labels.append("スネアはほとんど無い")
        n8 = sum(1 for i in range(0, 16, 2) if h[i] >= 0.4)
        n16 = sum(1 for i in range(1, 16, 2) if h[i] >= 0.4)
        off = sum(1 for i in (2, 6, 10, 14) if h[i] >= 0.4)
        down = sum(1 for i in (0, 4, 8, 12) if h[i] >= 0.4)
        if n16 >= 5:
            labels.append("ハイハットは 16 分で刻む")
        elif n8 >= 6:
            labels.append("ハイハットは 8 分で刻む")
        elif off >= 3 and down <= 1:
            labels.append("ハイハットは裏拍")
        elif down >= 3:
            labels.append("ハイハットは 4 分で刻む")
        elif sum(h) < 0.6:
            labels.append("ハイハットはほとんど無い")
        out["labels"] = labels
    return out


# ---------------------------------------------------------------------------
# 2. MIDI を読む（外の部品を使わない。melody.py が書いた「楽器ごとのトラック」の MIDI）
# ---------------------------------------------------------------------------
def read_midi(path: Path) -> dict[str, list[tuple[float, float, int, int]]]:
    """MIDI → {トラックの名前: [(始まりの秒, 終わりの秒, 音の高さ, 強さ)]}。10 チャンネルは drums"""
    data = Path(path).read_bytes()
    if data[:4] != b"MThd":
        raise ValueError("MIDI ではありません")
    _, ntrk, div = struct.unpack(">HHH", data[8:14])
    if div & 0x8000:
        raise ValueError("この時刻の書き方（SMPTE）の MIDI は読めません")
    i = 8 + struct.unpack(">I", data[4:8])[0]
    raw_tracks, tempos = [], [(0, 500000)]
    for _ in range(ntrk):
        if data[i:i + 4] != b"MTrk":
            break
        ln = struct.unpack(">I", data[i + 4:i + 8])[0]
        chunk, i = data[i + 8:i + 8 + ln], i + 8 + ln
        j, tick, status, name, events = 0, 0, 0, "", []
        while j < len(chunk):
            d = 0
            while True:
                b = chunk[j]
                j += 1
                d = (d << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            tick += d
            b = chunk[j]
            if b == 0xFF:
                kind = chunk[j + 1]
                j += 2
                ln2 = 0
                while True:
                    c = chunk[j]
                    j += 1
                    ln2 = (ln2 << 7) | (c & 0x7F)
                    if not c & 0x80:
                        break
                body = chunk[j:j + ln2]
                j += ln2
                if kind == 0x51 and ln2 == 3:
                    tempos.append((tick, int.from_bytes(body, "big")))
                elif kind == 0x03:
                    name = body.decode("utf-8", "replace")
                continue
            if b in (0xF0, 0xF7):
                j += 1
                ln2 = 0
                while True:
                    c = chunk[j]
                    j += 1
                    ln2 = (ln2 << 7) | (c & 0x7F)
                    if not c & 0x80:
                        break
                j += ln2
                continue
            if b & 0x80:
                status = b
                j += 1
            hi = status & 0xF0
            if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                a, v = chunk[j], chunk[j + 1]
                j += 2
                if hi == 0x90 and v > 0:
                    events.append((tick, True, status & 0x0F, a, v))
                elif hi == 0x80 or hi == 0x90:
                    events.append((tick, False, status & 0x0F, a, 0))
            elif hi in (0xC0, 0xD0):
                j += 1
        raw_tracks.append((name, events))
    tempos = sorted(set(tempos))

    def sec(tick: int) -> float:
        t, last, us = 0.0, 0, 500000
        for tk, u in tempos:
            if tk >= tick:
                break
            t += (tk - last) * us / div / 1e6
            last, us = tk, u
        return t + (tick - last) * us / div / 1e6
    out: dict[str, list] = {}
    for name, events in raw_tracks:
        open_: dict[tuple, list] = {}
        notes = []
        drum = False
        for tick, on, ch, p, v in events:
            drum = drum or ch == 9
            if on:
                open_.setdefault((ch, p), []).append((tick, v))
            elif open_.get((ch, p)):
                t0, v0 = open_[(ch, p)].pop(0)
                notes.append((sec(t0), sec(tick), p, v0))
        if not notes:
            continue
        key = "drums" if drum else MIDI_NAME.get(name.strip().lower(), name.strip().lower() or f"track{len(out) + 1}")
        out.setdefault(key, []).extend(notes)
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# 3. 音源を測る（librosa）
# ---------------------------------------------------------------------------
def _load(path: Path):
    """(モノラル, ステレオか None, サンプルレート)。そのまま読めない形（M4A など）は ffmpeg で一時的に WAV にする"""
    import librosa
    import numpy as np

    def read(p):
        y, sr = librosa.load(str(p), sr=22050, mono=False)
        if y.ndim == 1:
            return y, None, sr
        return np.mean(y, axis=0), y[:2], sr
    try:
        return read(path)
    except Exception:  # noqa: BLE001
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("ffmpeg"):
            raise
        with tempfile.TemporaryDirectory() as d:
            w = Path(d) / "tmp.wav"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-ar", "22050", str(w)], check=True)
            return read(w)


class Grid:
    """拍と小節の物差し：時刻 ↔ 拍の番号 ↔ 小節"""

    def __init__(self, beat_times, meter: int = 4, down: int = 0, bpm: float = 120.0):
        import numpy as np
        self.bt = np.asarray(beat_times, dtype=float)
        self.meter, self.down, self.bpm = meter, down, bpm
        self.ibi = float(np.median(np.diff(self.bt))) if len(self.bt) > 1 else 60.0 / bpm

    def beat_of(self, t: float) -> float:
        """時刻 → 拍の番号（小数つき。拍の外側は、ふつうの拍の長さで延ばす）"""
        import numpy as np
        if not len(self.bt):
            return t / self.ibi
        if t <= self.bt[0]:
            return (t - self.bt[0]) / self.ibi
        if t >= self.bt[-1]:
            return len(self.bt) - 1 + (t - self.bt[-1]) / self.ibi
        return float(np.interp(t, self.bt, np.arange(len(self.bt))))

    def pos(self, t: float):
        """時刻 → (小節の番号〔最初の小節の頭が 0〕, 小節の中の位置 0〜1)。最初の小節の頭より前は None"""
        b = self.beat_of(t) - self.down
        if b < -0.13:
            return None
        b = max(0.0, b)
        return int(b // self.meter), (b % self.meter) / self.meter

    def bar_times(self) -> list[tuple[float, float]]:
        """小節ごとの (始まりの秒, 終わりの秒)"""
        out = []
        i = self.down
        while 0 <= i and i + self.meter <= len(self.bt):
            a = self.bt[i]
            b = self.bt[i + self.meter] if i + self.meter < len(self.bt) else self.bt[-1] + self.ibi
            out.append((float(a), float(b)))
            i += self.meter
        return out


HOP_BEAT = 256   # 拍を探すときの刻み（約 12 ミリ秒）。librosa は拍の間隔を「刻みいくつ分」の整数で持つので、粗いと 120 BPM が 117.5 や 123 になる


def low_flux(y, sr: int, hop: int = 512):
    """低い音（150 Hz より下）が、その瞬間にどれだけ増えたか（キック・ベースの立ち上がり）"""
    import librosa
    import numpy as np
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    low = S[librosa.fft_frequencies(sr=sr, n_fft=2048) < 150].sum(axis=0)
    return np.concatenate([[0.0], np.maximum(0.0, np.diff(low))])


def beat_grid(y, sr: int, bpm_hint: float | None = None, lowflux=None) -> tuple[Grid, dict, object]:
    """(拍の物差し, テンポの情報, 音の立ち上がりの強さ〔刻み HOP_BEAT〕)"""
    import librosa
    import numpy as np
    hop = HOP_BEAT
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop, start_bpm=float(bpm_hint or 120), trim=False)
    tempo = float(np.atleast_1d(tempo)[0])
    bt = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    info: dict = {"bpm": round(tempo, 1), "beats": int(len(bt))}
    if len(bt) < 12:
        return Grid(bt, 4, 0, tempo or 120.0), {**info, "meter": 4, "time_signature": "4/4", "note": "拍がほとんど取れませんでした"}, oenv
    span = (bt[8:] - bt[:-8]) / 8                      # 8 拍ぶんの長さで測る（刻みの粗さの影響を 1/8 にする）
    info["bpm"] = round(60.0 / float(np.median(span)), 1)
    four = bt[4:] - bt[:-4]
    drift = float(np.std(four) / np.mean(four))
    info["tempo_stability"] = round(1 - min(1.0, drift * 8), 2)
    info["tempo_drift"] = "ほぼ一定（打ち込み・クリックに合わせた演奏）" if drift < 0.012 else "少し揺れる（人の演奏）" if drift < 0.035 else "大きく揺れる（テンポが動く曲）"
    # 拍子：拍ごとの強さが、3 拍おきに似るか 4 拍おきに似るか
    lowflux = low_flux(y, sr) if lowflux is None else lowflux
    lf = librosa.time_to_frames(bt, sr=sr, hop_length=512)
    at = lambda env, fr: np.array([float(env[max(0, b - 2):b + 3].max()) if b < len(env) else 0.0 for b in fr])   # noqa: E731
    so, sl = at(oenv, beats), at(lowflux, lf)
    strength = so / (so.mean() + 1e-9) + sl / (sl.mean() + 1e-9)

    def ac(lag: int) -> float:
        if len(strength) <= lag + 4:
            return 0.0
        a, b = strength[:-lag], strength[lag:]
        if a.std() < 1e-9 or b.std() < 1e-9:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])
    three, four_ = (ac(3) + ac(6)) / 2, (ac(4) + ac(8)) / 2
    meter = 3 if three > four_ + 0.12 and three > 0.15 else 4
    info["meter"] = meter
    info["time_signature"] = f"{meter}/4"
    return Grid(bt, meter, 0, info["bpm"]), info, oenv


def chords(y, sr: int, grid: Grid, key_hint: str | None = None) -> tuple[dict, list[dict], object]:
    """拍ごとのコード・キー・進行。返り値：(まとめ, 拍ごとのコード [{root, quality, ext, fit}], 拍ごとのクロマ)"""
    import librosa
    import numpy as np
    hop = 512
    yh = librosa.effects.harmonic(y, margin=2.0)
    C = librosa.feature.chroma_cqt(y=yh, sr=sr, hop_length=hop, bins_per_octave=36)
    frames = librosa.time_to_frames(grid.bt, sr=sr, hop_length=hop)
    edges = list(frames) + [C.shape[1]]
    cols = []
    for i in range(len(frames)):
        a, b = int(edges[i]), int(max(edges[i] + 1, edges[i + 1]))
        seg = C[:, a:b]
        cols.append(np.median(seg, axis=1) if seg.shape[1] else np.zeros(12))
    B = np.array(cols).T if cols else np.zeros((12, 0))            # 12 × 拍
    rms = librosa.feature.rms(y=yh, hop_length=hop)[0]
    lvl = np.array([float(rms[int(edges[i]):int(max(edges[i] + 1, edges[i + 1]))].mean()) if int(edges[i]) < len(rms) else 0.0 for i in range(len(frames))])
    quiet = lvl < max(1e-5, float(np.percentile(lvl, 95)) * 0.03) if len(lvl) else np.array([], dtype=bool)
    # 型との近さ（24 の長和音・短和音）
    T = np.zeros((24, 12))
    for r in range(12):
        for q, iv in enumerate(((0, 4, 7), (0, 3, 7))):
            for k, w in zip(iv, (1.0, 0.85, 0.9)):
                T[q * 12 + r, (r + k) % 12] = w
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    Bn = B / (np.linalg.norm(B, axis=0, keepdims=True) + 1e-9)
    score = T @ Bn                                                     # 24 × 拍
    if score.shape[1]:
        p = np.exp(12.0 * score)
        p /= p.sum(axis=0, keepdims=True)
        trans = librosa.sequence.transition_loop(24, 0.55)
        path = librosa.sequence.viterbi(p, trans)
    else:
        path = np.array([], dtype=int)
    beats_ch: list[dict] = []
    for i, s in enumerate(path):
        fit = float(score[s, i])
        if quiet[i] or fit < 0.55:
            beats_ch.append({"root": None, "quality": "N", "ext": "", "fit": round(fit, 2)})
        else:
            beats_ch.append({"root": int(s % 12), "quality": "maj" if s < 12 else "min", "ext": "", "fit": round(fit, 2)})
    # 7 度の音（続いた区間ごとに見る）
    i = 0
    while i < len(beats_ch):
        j = i
        while j + 1 < len(beats_ch) and beats_ch[j + 1]["root"] == beats_ch[i]["root"] and beats_ch[j + 1]["quality"] == beats_ch[i]["quality"]:
            j += 1
        c = beats_ch[i]
        if c["root"] is not None and j - i >= 1:
            m = B[:, i:j + 1].mean(axis=1)
            r = c["root"]
            tri = np.mean([m[r], m[(r + (4 if c["quality"] == "maj" else 3)) % 12], m[(r + 7) % 12]])
            b7, M7 = m[(r + 10) % 12] / (tri + 1e-9), m[(r + 11) % 12] / (tri + 1e-9)
            ext = "7" if b7 >= 0.62 and b7 > M7 else ("maj7" if c["quality"] == "maj" and M7 >= 0.62 else "")
            for k in range(i, j + 1):
                beats_ch[k]["ext"] = ext
        i = j + 1
    # キー：音の高さの分布（クルムハンスル）＋ コードがキーに収まる割合 ＋ 主和音の多さ
    prof = (B * lvl).sum(axis=1) if B.shape[1] else np.zeros(12)
    cands = []
    for t in range(12):
        for mode, tmpl in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = float(np.corrcoef(prof, np.roll(tmpl, t))[0, 1]) if prof.std() > 0 else 0.0
            cands.append((r, t, mode))
    cands.sort(reverse=True)
    real = [c for c in beats_ch if c["root"] is not None]

    def fit_key(t: int, mode: str) -> tuple[float, float]:
        if not real:
            return 0.0, 0.0
        dia = sum(1 for c in real if ((c["root"] - t) % 12, c["quality"]) in DIATONIC[mode]) / len(real)
        ton = sum(1 for c in real if c["root"] == t and c["quality"] == ("maj" if mode == "major" else "min")) / len(real)
        return dia, ton
    best = max(cands[:4], key=lambda c: c[0] + 0.35 * fit_key(c[1], c[2])[0] + 0.3 * fit_key(c[1], c[2])[1]) if cands else (0.0, 0, "major")
    hint = parse_key(key_hint)
    tonic, mode = best[1], best[2]
    conf = best[0]
    summary: dict = {"key": f"{NAMES[tonic]} {mode}", "key_confidence": round(conf, 2)}
    if hint and (hint[0], hint[1]) != (tonic, mode):
        summary["key_note"] = f"前の計測は {NAMES[hint[0]]} {hint[1]}（コードの出方から {NAMES[tonic]} {mode} と見ました）"
    for c in beats_ch:
        if c["root"] is not None:
            c["roman"] = roman(c["root"], c["quality"], tonic, c["ext"])
    return {**summary, "_tonic": tonic, "_mode": mode}, beats_ch, B


def find_downbeat(grid: Grid, beats_ch: list[dict], flux, sr: int) -> int:
    """小節の頭（何拍目が 1 拍目か）：コードが変わる拍・低い音が強く鳴る拍がいちばん集まる位置"""
    import librosa
    import numpy as np
    m = grid.meter
    if len(beats_ch) < m * 4:
        return 0
    frames = librosa.time_to_frames(grid.bt, sr=sr, hop_length=512)
    lowb = np.array([float(flux[max(0, f - 2):f + 3].max()) if f < len(flux) else 0.0 for f in frames])
    lowb = lowb / (lowb.mean() + 1e-9)
    change = np.array([0.0] + [1.0 if (beats_ch[i]["root"], beats_ch[i]["quality"]) != (beats_ch[i - 1]["root"], beats_ch[i - 1]["quality"]) else 0.0
                               for i in range(1, len(beats_ch))])
    change = change / (change.mean() + 1e-9) if change.any() else change
    n = min(len(lowb), len(change))
    score = [float(lowb[k:n:m].mean()) * 0.5 + float(change[k:n:m].mean()) for k in range(m)]
    return int(max(range(m), key=lambda k: score[k]))


def _sections(sheet: dict | None, duration: float, grid: Grid, bar_energy: list[float]) -> list[dict]:
    """区切り：解析シートにあればそれを使う。無ければ、4 小節ごとの盛り上がりの段差で分ける"""
    secs = ((sheet or {}).get("structure") or {}).get("sections") or []
    out = []
    if secs:
        for i, s in enumerate(secs):
            a = float(s.get("start_sec") or 0)
            b = float(secs[i + 1].get("start_sec") or duration) if i + 1 < len(secs) else duration
            if b - a > 0.5:
                out.append({"name": str(s.get("name") or f"sec{i + 1}"), "start_sec": round(a, 1), "end_sec": round(b, 1)})
        if out:
            names = [s["name"] for s in out]
            seen: dict[str, int] = {}
            for s in out:               # 同じ名前（chorus が 3 回など）には番号を付けて、区切りを取り違えないようにする
                if names.count(s["name"]) > 1:
                    seen[s["name"]] = seen.get(s["name"], 0) + 1
                    s["name"] = f"{s['name']} {seen[s['name']]}"
            return out
    bars = grid.bar_times()
    if len(bars) < 8:
        return [{"name": "whole", "start_sec": 0.0, "end_sec": round(duration, 1)}]
    cuts = [0]
    for i in range(4, len(bars) - 3, 4):
        prev = sum(bar_energy[max(0, i - 4):i]) / 4
        nxt = sum(bar_energy[i:i + 4]) / 4
        if abs(nxt - prev) >= 9 and i - cuts[-1] >= 4:
            cuts.append(i)
    cuts.append(len(bars))
    means = [sum(bar_energy[a:b]) / max(1, b - a) for a, b in zip(cuts, cuts[1:])]
    hi = sorted(means)[int(len(means) * 0.66)] if means else 0
    count: dict[str, int] = {}
    for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
        label = "intro" if k == 0 and len(means) > 2 else "outro" if k == len(means) - 1 and len(means) > 2 else "chorus" if means[k] >= hi else "verse"
        count[label] = count.get(label, 0) + 1
        name = label if label in ("intro", "outro") else f"{label} {count[label]}"
        out.append({"name": name, "start_sec": round(bars[a][0] if a else 0.0, 1), "end_sec": round(bars[b - 1][1] if b < len(bars) else duration, 1)})
    return out


def _norm(v, lo_p: float = 5, hi_p: float = 95):
    import numpy as np
    v = np.asarray(v, dtype=float)
    if not len(v):
        return v
    lo, hi = np.percentile(v, lo_p), np.percentile(v, hi_p)
    return np.clip((v - lo) / (hi - lo), 0, 1) if hi - lo > 1e-9 else np.zeros_like(v)


def _resample(v: list[float], n: int) -> list[float]:
    if not v:
        return []
    if len(v) <= n:
        return [round(float(x), 1) for x in v]
    out = []
    for i in range(n):
        a, b = i * len(v) // n, max(i * len(v) // n + 1, (i + 1) * len(v) // n)
        out.append(round(sum(v[a:b]) / (b - a), 1))
    return out


def _range_name(notes) -> str:
    ps = sorted(p for _, _, p, _ in notes)
    if not ps:
        return ""
    lo, hi = ps[int(len(ps) * 0.03)], ps[min(len(ps) - 1, int(len(ps) * 0.97))]
    return f"{note_name(lo)}〜{note_name(hi)}"


def _sec_of(sections: list[dict], t: float) -> str:
    for s in sections:
        if s["start_sec"] <= t < s["end_sec"]:
            return s["name"]
    return sections[-1]["name"] if sections else ""


def analyze(audio: Path, sheet: dict | None = None, tracks: dict | None = None, stems: dict | None = None, say=None) -> dict:
    """1 曲を測る。sheet：解析シート（あれば、BPM・キー・区切り・楽器の名札を使う）。tracks：read_midi の結果。stems：stem_levels の結果"""
    import librosa
    import numpy as np
    say = say or (lambda s: None)
    sheet = sheet or {}
    say("音源を読み込んでいます")
    y, st, sr = _load(Path(audio))
    duration = float(len(y) / sr)
    hop = 512
    say("テンポと拍を測っています")
    flux = low_flux(y, sr)
    grid, tinfo, oenv = beat_grid(y, sr, (sheet.get("groove") or {}).get("bpm"), flux)
    say("コードとキーを読んでいます")
    hsum, beats_ch, B = chords(y, sr, grid, (sheet.get("_measure") or {}).get("key"))
    tonic, mode = hsum.pop("_tonic"), hsum.pop("_mode")
    grid.down = find_downbeat(grid, beats_ch, flux, sr)
    bars = grid.bar_times()
    m = grid.meter
    nb = len(bars)

    # 小節ごとの測りもの
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    onsets = librosa.onset.onset_detect(onset_envelope=oenv, sr=sr, hop_length=HOP_BEAT, units="time")
    ft = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    mel = librosa.power_to_db(librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop, n_mels=8, fmax=11000) + 1e-10)
    b_db, b_cent, b_on, b_fill, b_ch, b_fn, b_slots = [], [], [], [], [], [], []
    for k, (a, b) in enumerate(bars):
        msk = (ft >= a) & (ft < b)
        b_db.append(20 * math.log10(float(rms[msk].mean()) + 1e-9) if msk.any() else -80.0)
        b_cent.append(math.log2(float(cent[msk].mean()) + 1) if msk.any() else 0.0)
        on = [o for o in onsets if a <= o < b]
        b_on.append(len(on))
        b_slots.append(len({int((o - a) / (b - a) * m * 4) for o in on}) / (m * 4))
        if msk.any():
            band = mel[:, msk].mean(axis=1)
            b_fill.append(int((band >= band.max() - 18).sum()))
        else:
            b_fill.append(0)
        cs = beats_ch[grid.down + k * m: grid.down + (k + 1) * m]
        labels = []
        for c in cs:
            lab = c.get("roman") or "N"
            if not labels or labels[-1] != lab:
                labels.append(lab)
        if len(labels) > 1 and "N" in labels:
            labels = [x for x in labels if x != "N"]
        b_ch.append(" ".join(labels) or "N")
        fn = [FUNCTION[mode].get((c["root"] - tonic) % 12, 0.9) * 0.7 + (1 - c["fit"]) * 0.3 for c in cs if c["root"] is not None]
        b_fn.append(sum(fn) / len(fn) if fn else 0.3)
    loud, bright, dens, harm = _norm(b_db), _norm(b_cent), _norm(b_on), np.asarray(b_fn)
    tension = (0.4 * loud + 0.2 * bright + 0.25 * dens + 0.15 * harm) * 100 if nb else np.array([])
    if nb >= 3:
        tension = np.convolve(np.pad(tension, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    tension = [round(float(x), 1) for x in tension]
    sections = _sections(sheet, duration, grid, tension)

    def bar_range(s: dict) -> tuple[int, int]:
        idx = [k for k, (a, b) in enumerate(bars) if s["start_sec"] - 0.01 <= (a + b) / 2 < s["end_sec"]]
        return (idx[0], idx[-1] + 1) if idx else (0, 0)

    out: dict = {"version": VERSION, "duration_sec": round(duration, 1),
                 "source": "音源" + ("＋MIDI" if tracks else "") + ("＋楽器ごとの音量" if stems else "")}
    # --- テンポと拍 ---
    out["groove"] = {**tinfo, "bars": nb}
    sw = (sheet.get("groove") or {}).get("swing_ratio")
    if sw:
        out["groove"]["swing_ratio"] = sw

    # --- 和音 ---
    real = [c for c in beats_ch if c["root"] is not None]
    vocab: dict[str, int] = {}
    for c in real:
        vocab[c["roman"]] = vocab.get(c["roman"], 0) + 1
    changes = sum(1 for i in range(1, len(beats_ch)) if beats_ch[i]["root"] is not None and beats_ch[i - 1]["root"] is not None
                  and (beats_ch[i]["root"], beats_ch[i]["quality"]) != (beats_ch[i - 1]["root"], beats_ch[i - 1]["quality"]))
    loop = find_loop(b_ch)
    outside = sorted({c["roman"] for c in real if ((c["root"] - tonic) % 12, c["quality"]) not in DIATONIC[mode]},
                     key=lambda r: -vocab.get(r, 0))
    outside = [r for r in outside if vocab[r] >= max(2, len(real) * 0.02)]
    harmony = {**hsum, "chords_per_bar": round(changes / max(1, nb), 2),
               "harmonic_rhythm": "1 小節に 2 つ以上（速く変わる）" if changes / max(1, nb) >= 1.6 else "1 小節に 1 つくらい" if changes / max(1, nb) >= 0.7
               else "2 小節以上で 1 つ（ゆっくり変わる・同じコードが続く）",
               "vocabulary": [{"chord": r, "share": round(n / len(real), 2)} for r, n in sorted(vocab.items(), key=lambda kv: -kv[1])[:8]] if real else [],
               "loop": loop, "outside_key": outside[:6], "sections": []}
    if loop:
        harmony["progression_character"] = (f"ループ型（{loop['bars']} 小節の型 {' | '.join(loop['pattern'])} が全体の {int(loop['coverage'] * 100)}%）"
                                            if loop["coverage"] >= 0.5 else f"展開型（いちばん多い型 {' | '.join(loop['pattern'])} でも {int(loop['coverage'] * 100)}%）")
    else:
        harmony["progression_character"] = "同じコードが続く（1〜2 個のコードで進む）" if len(vocab) <= 2 else "展開型（決まった繰り返しが見つからない）"
    mods = []
    for s in sections:
        a, b = bar_range(s)
        if b - a < 2:
            continue
        seg = b_ch[a:b]
        entry = {"name": s["name"], "bars": b - a, "progression": seg[:8], "loop": (find_loop(seg) or {}).get("pattern")}
        if b - a >= 8 and B.shape[1]:
            pr = B[:, grid.down + a * m: grid.down + b * m].sum(axis=1)
            if pr.std() > 0:
                r_here = float(np.corrcoef(pr, np.roll(MAJOR_PROFILE if mode == "major" else MINOR_PROFILE, tonic))[0, 1])
                alt = max(((float(np.corrcoef(pr, np.roll(MAJOR_PROFILE if md == "major" else MINOR_PROFILE, t))[0, 1]), t, md)
                           for t in range(12) for md in ("major", "minor")), key=lambda x: x[0])
                rel = (alt[2] != mode and (alt[1] - tonic) % 12 in (3, 9))   # 平行調（同じ音の集まり）は転調に数えない
                if alt[0] - r_here > 0.18 and (alt[1], alt[2]) != (tonic, mode) and not rel:
                    entry["key"] = f"{NAMES[alt[1]]} {alt[2]}"
                    mods.append({"at_sec": s["start_sec"], "section": s["name"], "to": entry["key"], "semitones_up": (alt[1] - tonic) % 12})
        harmony["sections"].append(entry)
    harmony["modulations"] = mods
    harmony["modulation_count"] = len(mods)
    out["harmony"] = harmony

    # --- 音の多さ ---
    poly = float((B >= 0.5 * (B.max(axis=0, keepdims=True) + 1e-9)).sum(axis=0).mean()) if B.shape[1] else 0.0
    opb = sum(b_on) / max(1, nb)
    per_beat = opb / m
    out["density"] = {"onsets_per_bar": round(opb, 1), "onsets_per_sec": round(len(onsets) / max(1.0, duration), 2),
                      "grid_fill": round(sum(b_slots) / max(1, nb), 2), "pitch_classes_at_once": round(poly, 1),
                      "bands_filled": round(sum(b_fill) / max(1, nb), 1),
                      "level": "少ない（すき間が多い）" if per_beat < 1.4 else "ふつう" if per_beat < 2.6 else "多い（細かく埋まっている）",
                      "sections": []}

    # --- テンション ---
    ten: dict = {"per_bar": _resample(tension, 64), "shape": arc_shape(tension), "sections": []}
    if nb:
        top = max(range(nb), key=lambda i: tension[i])
        ten.update({"average": round(sum(tension) / nb), "peak": round(tension[top]), "peak_at": round(top / nb, 2), "peak_section": _sec_of(sections, bars[top][0]),
                    "lowest": round(min(tension)), "range": round(max(tension) - min(tension))})
        if nb > 6:
            d = [(sum(tension[i:i + 2]) - sum(tension[i - 2:i])) / 2 for i in range(2, nb - 1)]
            up, dn = max(range(len(d)), key=lambda i: d[i]), min(range(len(d)), key=lambda i: d[i])
            ten["biggest_rise"] = {"at_sec": round(bars[up + 2][0], 1), "into": _sec_of(sections, bars[up + 2][0]), "by": round(d[up])}
            ten["biggest_drop"] = {"at_sec": round(bars[dn + 2][0], 1), "into": _sec_of(sections, bars[dn + 2][0]), "by": round(d[dn])}
    structure = []
    for s in sections:
        a, b = bar_range(s)
        n = max(1, b - a)
        row = {"name": s["name"], "start_sec": s["start_sec"], "bars": b - a,
               "tension": round(sum(tension[a:b]) / n) if b > a else None,
               "onsets_per_bar": round(sum(b_on[a:b]) / n, 1) if b > a else None,
               "loudness_db": round(sum(b_db[a:b]) / n, 1) if b > a else None}
        structure.append(row)
        ten["sections"].append({"name": s["name"], "tension": row["tension"]})
        out["density"]["sections"].append({"name": s["name"], "onsets_per_bar": row["onsets_per_bar"]})
    out["tension"] = ten
    out["structure"] = {"sections": structure, "total_bars": nb, "pickup_beats": grid.down}

    # --- 音づくり ---
    out["mix"] = _mix(y, st, sr, b_db)

    # --- 楽器・ドラム・ベース・ボーカル（MIDI があるとき）---
    drum_hits = [(a, p) for a, _, p, _ in (tracks or {}).get("drums", [])]
    how_drums = "MIDI"
    if not drum_hits:
        found = _drums_from_mix(y, sr)
        drum_hits, how_drums = found or [], "音源から（目安。スネアとハイハットは拾えますが、キックは拾いにくいので型に入れていません）"
    dg = drum_grid(drum_hits, grid.pos, 16 if m == 4 else 12)
    if not drum_hits and not tracks and found is None:
        dg["unknown"] = True          # 拾う部品が動かなかった：「ドラムが無い」とは言わない
    if dg.get("pattern"):
        dg["from"] = how_drums
        if how_drums != "MIDI":      # 音源からの目安：キックの読みは当てにならないので、言葉にはしない
            dg["labels"] = [x for x in dg.get("labels") or [] if "キック" not in x]
            dg["pattern"]["kick"] = "?" * dg["steps"]
            dg["reliability"] = "low"
        by = []
        for s in sections:
            a, b = bar_range(s)
            if b - a >= 4:
                g = drum_grid([h for h in drum_hits if s["start_sec"] <= h[0] < s["end_sec"]], grid.pos, dg["steps"])
                if g.get("pattern"):
                    by.append({"name": s["name"], "kick": g["pattern"]["kick"], "snare": g["pattern"]["snare"], "hat": g["pattern"]["hat"]})
        dg["sections"] = by
    out["drums"] = dg
    out["instruments"] = _instruments(tracks, stems, sections, bars, y, sr, sheet)
    if tracks:
        out["bass"] = _bass(tracks, grid, beats_ch, drum_hits)
        out["vocal"] = _vocal(tracks, sections, duration, sheet)
    out["impression"] = impression(out, sheet)
    out["card"] = card(out, sheet)
    out["_grid"] = {"beats": [round(float(t), 3) for t in grid.bt], "meter": m, "down": grid.down,
                    "chords": [[c["root"], c["quality"], c["ext"]] for c in beats_ch], "tonic": tonic, "mode": mode}
    return out


def _mix(y, st, sr: int, b_db: list[float]) -> dict:
    import numpy as np
    out: dict = {}
    n = 1 << 13
    seg = y[:len(y) // n * n].reshape(-1, n) if len(y) >= n else y.reshape(1, -1)
    psd = (np.abs(np.fft.rfft(seg * np.hanning(seg.shape[1]), axis=1)) ** 2).mean(axis=0)
    fr = np.fft.rfftfreq(seg.shape[1], 1 / sr)
    tot = float(psd[(fr >= 20)].sum()) + 1e-12
    out["bands"] = {name: round(float(psd[(fr >= lo) & (fr < hi)].sum()) / tot, 3) for name, lo, hi in BANDS}
    low = out["bands"]["sub"] + out["bands"]["bass"]
    high = out["bands"]["presence"] + out["bands"]["air"]
    out["tone"] = ("低音が厚い" if low > 0.75 else "低音は控えめ" if low < 0.35 else "低音はふつう") + "・" + \
                  ("高音がよく出ている（明るい）" if high > 0.06 else "高音は控えめ（暗め・やわらかい）" if high < 0.012 else "高音はふつう")
    peak, r = float(np.max(np.abs(y)) + 1e-9), float(np.sqrt(np.mean(y ** 2)) + 1e-9)
    out["crest_db"] = round(20 * math.log10(peak / r), 1)
    if len(b_db) >= 4:
        s = sorted(b_db)
        rng = s[int(len(s) * 0.95) - 1] - s[int(len(s) * 0.1)]
        out["loudness_range_db"] = round(rng, 1)
        out["dynamics"] = "音量の差が大きい（静かな所と大きい所がはっきり）" if rng > 9 else "音量の差は中くらい" if rng > 4 else "音量はほぼ一定（ずっと詰まっている）"
    if st is not None and st.shape[0] == 2:
        mid, side = (st[0] + st[1]) / 2, (st[0] - st[1]) / 2
        w = float(np.sqrt(np.mean(side ** 2)) / (np.sqrt(np.mean(mid ** 2)) + 1e-9))
        out["stereo_width"] = round(w, 2)
        out["stereo"] = "ほぼ真ん中（モノラルに近い）" if w < 0.1 else "やや狭い" if w < 0.25 else "ふつうの広がり" if w < 0.5 else "左右に広い"
    return out


def _drums_from_mix(y, sr: int) -> list[tuple[float, int]] | None:
    """MIDI が無い曲：音源から打楽器の成分を取り出して、キック・スネア・ハイハットを拾う（melody.py の拾い方）"""
    try:
        import tempfile
        import librosa
        import soundfile as sf
        import melody
        yp = librosa.effects.percussive(y, n_fft=512, hop_length=128)   # 短い窓で切り出す（長い窓だと、音の頭が前ににじんで 1 マス早く見える）
        with tempfile.TemporaryDirectory() as d:
            w = Path(d) / "perc.wav"
            sf.write(str(w), yp, sr)
            return [(a, p) for a, _, p, _ in melody.drum_notes(w)]
    except Exception:  # noqa: BLE001  拾う部品が動かなければ None（ドラムの型は「分からない」のまま）
        return None


def _instruments(tracks, stems, sections, bars, y, sr, sheet) -> dict:
    import numpy as np
    listed = [x.get("name") if isinstance(x, dict) else str(x) for x in ((sheet.get("instruments") or {}).get("list") or [])][:12]
    out: dict = {"listed": listed, "formation_feel": (sheet.get("instruments") or {}).get("formation_feel")}
    share = (stems or {}).get("share") or {}
    if not tracks:
        import librosa
        yh, yp = librosa.effects.hpss(y)
        eh, ep = float(np.mean(yh ** 2)), float(np.mean(yp ** 2))
        out.update({"from": "音源だけ（楽器ごとには分けていません。メロディーの書き起こしが済むと、楽器ごとに出ます）",
                    "percussive_share": round(ep / (eh + ep + 1e-12), 2)})
        if share:
            out["stem_share"] = share
        return out
    nb = len(bars)
    parts, active_bars = [], {}
    total_w = sum(sum(v for *_, v in ns) for ns in tracks.values()) or 1
    for key, ns in tracks.items():
        if len(ns) < 4:
            continue
        on = set()
        starts = [x for x, _ in bars]
        import bisect
        for a, b, _, _ in ns:
            k = max(0, bisect.bisect_right(starts, a) - 1)
            while k < len(bars) and bars[k][0] < max(b, a + 0.05):
                if bars[k][1] > a:
                    on.add(k)
                k += 1
        active_bars[key] = on
        first = ns[0][0]
        part = {"key": key, "name": TRACK_JA.get(key, key), "en": TRACK_EN.get(key, key), "notes": len(ns),
                "active": round(len(on) / max(1, nb), 2), "enters_sec": round(first, 1), "enters_in": _sec_of(sections, first),
                "notes_per_bar": round(len(ns) / max(1, len(on)), 1),
                "weight": round(sum(v for *_, v in ns) / total_w, 2)}
        if key != "drums":
            part["range"] = _range_name(ns)
        if key in share:
            part["loudness_share"] = share[key]
        parts.append(part)
    parts.sort(key=lambda p: -(p.get("loudness_share") or p["weight"]))
    timeline, prev = [], None
    for s in sections:
        idx = [k for k, (a, b) in enumerate(bars) if s["start_sec"] - 0.01 <= (a + b) / 2 < s["end_sec"]]
        if not idx:
            continue
        playing = [p["key"] for p in parts if len(active_bars[p["key"]] & set(idx)) / len(idx) >= 0.34]
        row = {"section": s["name"], "playing": [TRACK_JA.get(k, k) for k in playing]}
        if prev is not None:
            row["added"] = [TRACK_JA.get(k, k) for k in playing if k not in prev]
            row["dropped"] = [TRACK_JA.get(k, k) for k in prev if k not in playing]
        timeline.append(row)
        prev = playing
    layers = [sum(1 for on in active_bars.values() if k in on) for k in range(nb)]
    out.update({"from": "MIDI（メロディーの書き起こし）" + ("＋楽器ごとの音量" if share else ""), "parts": parts, "timeline": timeline,
                "layers": {"average": round(sum(layers) / max(1, nb), 1), "max": max(layers) if layers else 0, "min": min(layers) if layers else 0}})
    return out


def _bass(tracks, grid: Grid, beats_ch: list[dict], drum_hits) -> dict:
    ns = sorted((tracks.get("bass") or []) + (tracks.get("synth_bass") or []))
    if len(ns) < 8:
        return {"usable": False, "note": "ベースの音がほとんど取れていません"}
    on_root, n_ch = 0, 0
    for a, _, p, _ in ns:
        i = int(round(grid.beat_of(a)))
        if 0 <= i < len(beats_ch) and beats_ch[i]["root"] is not None:
            n_ch += 1
            on_root += p % 12 == beats_ch[i]["root"]
    root = on_root / max(1, n_ch)
    length = sorted((b - a) / grid.ibi for a, b, _, _ in ns)[len(ns) // 2]
    iv = [abs(b[2] - a[2]) for a, b in zip(ns, ns[1:])]
    kicks = sorted(t for t, p in drum_hits if p in (35, 36))
    with_kick = 0
    if kicks:
        import bisect
        for a, *_ in ns:
            j = bisect.bisect_left(kicks, a)
            near = min((abs(kicks[k] - a) for k in (j - 1, j) if 0 <= k < len(kicks)), default=9)
            with_kick += near <= 0.06
    bars = max(1, len({grid.pos(a)[0] for a, *_ in ns if grid.pos(a)}))
    out = {"usable": True, "range": _range_name(ns), "notes_per_bar": round(len(ns) / bars, 1), "root_follow": round(root, 2),
           "motion": "コードの根音を刻む" if root >= 0.7 else "根音が中心で、ときどき動く" if root >= 0.45 else "よく動く（フレーズで歌うベース）",
           "note_length_beats": round(length, 2),
           "articulation": "長く伸ばす" if length >= 1.5 else "短く刻む" if length <= 0.4 else "中くらいの長さ",
           "octave_jump_ratio": round(sum(1 for x in iv if x == 12) / max(1, len(iv)), 2),
           "repeated_note_ratio": round(sum(1 for x in iv if x == 0) / max(1, len(iv)), 2)}
    if kicks:
        r = with_kick / len(ns)
        out["with_kick_ratio"] = round(r, 2)
        out["kick_bass_relation"] = "キックと同じ所で鳴る（一体で動く）" if r >= 0.5 else "キックと半分くらい重なる" if r >= 0.25 else "キックのすき間で鳴る（掛け合い）"
    return out


def _vocal(tracks, sections, duration: float, sheet) -> dict:
    ns = tracks.get("vocals") or []
    if len(ns) < 8:
        return {"usable": False, "note": "歌の音がほとんど取れていません（インストか、歌が聞き取りにくい曲）"}
    secs = [False] * (int(duration) + 1)
    for a, b, _, _ in ns:
        for t in range(int(a), min(len(secs), int(b) + 1)):
            secs[t] = True
    overlap = sum(1 for x, z in zip(ns, ns[1:]) if z[0] < x[1] - 0.05 and z[2] != x[2])
    long_ = max(ns, key=lambda n: n[1] - n[0])
    held = [n for n in ns if n[1] - n[0] >= 0.35] or ns
    top = max(held, key=lambda n: n[2])
    rows = []
    for s in sections:
        span = range(int(s["start_sec"]), min(len(secs), int(s["end_sec"]) + 1))
        act = sum(1 for t in span if secs[t]) / max(1, len(span))
        rows.append({"name": s["name"], "singing": round(act, 2)})
    mel = sheet.get("melody") or {}
    out = {"usable": True, "active": round(sum(secs) / len(secs), 2), "enters_sec": round(ns[0][0], 1), "enters_in": _sec_of(sections, ns[0][0]),
           "range": _range_name(ns), "notes_per_sec": round(len(ns) / max(1, sum(secs)), 2),
           "harmony_ratio": round(overlap / len(ns), 2),
           "layers": "ハモリ・重ねが多い" if overlap / len(ns) > 0.25 else "ところどころ重ねる" if overlap / len(ns) > 0.08 else "ほぼ 1 本の声",
           "longest_note_sec": round(long_[1] - long_[0], 1), "longest_note_in": _sec_of(sections, long_[0]),
           "highest_in": _sec_of(sections, top[0]), "highest_at": round(top[0] / max(1.0, duration), 2), "sections": rows}
    if mel.get("usable"):
        out["melody"] = {k: mel.get(k) for k in ("range", "motion", "rhythm") if mel.get(k)}
    v = sheet.get("vocal") or {}
    for k in ("sex", "timbre"):
        if v.get(k):
            out[k] = v[k]
    return out


def stem_levels(stems: dict[str, Path]) -> dict:
    """分けた音（Demucs）ごとの音量：1 秒ごとの大きさと、全体に占める割合。melody.py が分けた直後に呼ぶ（分けた音は消すので、数字だけ残す）"""
    import librosa
    import numpy as np
    levels, power = {}, {}
    for k, p in stems.items():
        y, sr = librosa.load(str(p), sr=11025, mono=True)
        n = len(y) // sr
        if not n:
            continue
        e = (y[:n * sr].reshape(n, sr) ** 2).mean(axis=1)
        levels[k] = [round(float(10 * np.log10(x + 1e-10)), 1) for x in e]
        power[k] = float(e.sum())
    tot = sum(power.values()) or 1.0
    key = {"piano": "keys"}
    return {"version": 1, "share": {key.get(k, k): round(v / tot, 3) for k, v in power.items()}, "per_sec_db": {key.get(k, k): v for k, v in levels.items()}}


# ---------------------------------------------------------------------------
# 4. 印象の言葉と、再現カード
# ---------------------------------------------------------------------------
def impression(d: dict, sheet: dict | None = None) -> dict:
    """測った数字から、印象の言葉を決まりで付ける（人やほかの解析が書いたムードがあれば、それも並べる）"""
    g, h, den, t, mx = d.get("groove") or {}, d.get("harmony") or {}, d.get("density") or {}, d.get("tension") or {}, d.get("mix") or {}
    bpm = float(g.get("bpm") or 0)
    minor = "minor" in str(h.get("key"))
    ja, en = [], []
    tempo = ("ゆっくり", "slow") if bpm < 84 else ("ゆったり", "laid-back mid-tempo") if bpm < 106 else ("中くらいの速さ", "mid-tempo") if bpm < 124 \
        else ("速め", "upbeat") if bpm < 150 else ("とても速い", "fast, driving")
    ja.append(tempo[0])
    en.append(tempo[1])
    dense = str(den.get("level", ""))
    if dense.startswith("少ない"):
        ja.append("すき間が多い")
        en.append("sparse, spacious")
    elif dense.startswith("多い"):
        ja.append("音が詰まっている")
        en.append("dense, busy")
    avg = t.get("average") or 50
    if minor and bpm < 106:
        ja.append("かげりがある・内向き")
        en.append("wistful, introspective")
    elif minor:
        ja.append("かげりがありつつ前に進む")
        en.append("moody but driving")
    elif bpm < 106:
        ja.append("あたたかい・おだやか")
        en.append("warm, gentle")
    else:
        ja.append("明るい・ひらけた")
        en.append("bright, uplifting")
    shape = str(t.get("shape", ""))
    if shape.startswith("右肩上がり") or "終盤" in shape:
        ja.append("後半に向けて高まる")
        en.append("slow-building to a late climax")
    elif shape.startswith("ほぼ一定"):
        ja.append("ずっと同じ温度で続く")
        en.append("steady, hypnotic")
    elif "山が" in shape:
        ja.append("静と動がはっきり")
        en.append("clear quiet-loud contrast")
    if avg < 35:
        ja.append("全体に静か")
        en.append("understated")
    elif avg > 65:
        ja.append("全体に熱量が高い")
        en.append("high-energy")
    tone = str(mx.get("tone", ""))
    if "暗め" in tone:
        ja.append("音はやわらかく暗め")
        en.append("dark, soft-edged tone")
    elif "明るい" in tone:
        ja.append("音は明るく抜けがいい")
        en.append("bright, airy tone")
    if "低音が厚い" in tone:
        en.append("heavy low end")
    if str(mx.get("stereo", "")).startswith("左右に広い"):
        ja.append("左右に広がる")
        en.append("wide stereo")
    mood = ((sheet or {}).get("worldview") or {}).get("mood")
    return {"words_ja": ja, "words_en": en, **({"mood_from_tags": mood} if mood else {})}


_BAN = re.compile(r"[^\x20-\x7e]")


def _ascii(s: object) -> str:
    return re.sub(r"\s+", " ", _BAN.sub("", str(s or ""))).strip(" ,")


def card(d: dict, sheet: dict | None = None) -> dict:
    """再現カード：分析だけから書いた、Suno のスタイル欄に入る形の英文（1000 文字まで）と、区切りごとの指示。
    実名・曲名・歌詞・旋律の音の並びは入れない。分析の答え合わせ用で、配信する曲には使わない"""
    sheet = sheet or {}
    g, h, den, t, mx, dr = (d.get(k) or {} for k in ("groove", "harmony", "density", "tension", "mix", "drums"))
    ins, bs, vo, imp = (d.get(k) or {} for k in ("instruments", "bass", "vocal", "impression"))
    bits: list[str] = []
    tags = [_ascii(x) for x in ((sheet.get("reference") or {}).get("tags") or [])]
    bits += [x for x in tags if x][:4]
    bits.append(f"{round(float(g.get('bpm') or 0))} BPM")
    if h.get("key"):
        bits.append(str(h["key"]))
    if g.get("meter") == 3:
        bits.append("3/4 waltz time")
    sw = g.get("swing_ratio")
    if sw and float(sw) >= 0.58:
        bits.append("swung, shuffled feel")
    lab = " ".join(dr.get("labels") or [])
    if "4 つ打ち" in lab:
        bits.append("four-on-the-floor kick")
    if "バックビート" in lab:
        bits.append("snare on 2 and 4")
    if "ハーフタイム" in lab:
        bits.append("half-time drums")
    if "16 分" in lab:
        bits.append("16th-note hi-hats")
    elif "8 分" in lab:
        bits.append("8th-note hi-hats")
    elif "裏拍" in lab:
        bits.append("off-beat hi-hats")
    if dr and not dr.get("pattern") and not dr.get("unknown"):
        bits.append("no drums")
    if bs.get("usable"):
        b = "sustained" if "長く" in bs["articulation"] else "staccato" if "短く" in bs["articulation"] else ""
        mv = "root-note" if "刻む" in bs["motion"] else "melodic, moving" if "よく動く" in bs["motion"] else ""
        bits.append(" ".join(x for x in (b, mv, "bass") if x))
    parts = [p for p in ins.get("parts") or [] if p["key"] not in ("drums", "bass", "vocals", "synth_bass")]
    for p in parts[:4]:
        role = "throughout" if p["active"] >= 0.75 else "in places" if p["active"] < 0.35 else ""
        bits.append(" ".join(x for x in (p["en"], role) if x))
    for name in (ins.get("listed") or [])[:6]:
        a = _ascii(name).lower()
        if a and not any(a in b.lower() or b.lower().split(" ")[0] in a for b in bits):
            bits.append(a)
    if vo.get("usable"):
        sex = {"male": "male", "female": "female"}.get(str(vo.get("sex") or (sheet.get("vocal") or {}).get("sex") or ""), "")
        lay = "layered harmonies" if "多い" in vo["layers"] else "single lead voice" if "1 本" in vo["layers"] else ""
        deliv = _ascii(((sheet.get("vocal") or {}).get("delivery") or {}).get("style"))
        bits.append(", ".join(x for x in (" ".join(x for x in (sex, "vocals") if x), deliv, lay) if x))
    elif ins.get("parts") is not None and "vocal" in d:
        bits.append("instrumental")
    lp = h.get("loop") or {}
    if lp.get("pattern") and lp.get("coverage", 0) >= 0.5:
        bits.append(f"looping {lp['bars']}-bar progression ({'-'.join(x.replace(' ', '/') for x in lp['pattern'])})")
    elif h.get("harmonic_rhythm", "").startswith("2 小節"):
        bits.append("static harmony, long held chords")
    bits += imp.get("words_en") or []
    if str(mx.get("dynamics", "")).startswith("音量の差が大きい"):
        bits.append("wide dynamics")
    style = ""
    for b in bits:
        b = b.strip()
        if b and b.lower() not in style.lower() and len(style) + len(b) + 2 <= 1000:
            style += (", " if style else "") + b
    lines = []
    tl = {r["section"]: r for r in ins.get("timeline") or []}
    en = {v: TRACK_EN[k] for k, v in TRACK_JA.items()}
    for s in (d.get("structure") or {}).get("sections") or []:
        row = tl.get(s["name"]) or {}
        who = ", ".join(en.get(x, x) for x in row.get("playing") or [])
        add = ", ".join(en.get(x, x) for x in row.get("added") or [])
        tag = re.sub(r"\s*\d+$", "", _ascii(s["name"])).title() or "Section"
        extra = "; ".join(x for x in (f"{s['bars']} bars" if s.get("bars") else "", who, f"adds {add}" if add else "",
                                      f"energy {s['tension']}/100" if s.get("tension") is not None else "") if x)
        lines.append(f"[{tag}] {extra}".strip())
    return {"style": style, "style_chars": len(style), "structure": lines,
            "note": "分析の答え合わせ用です。配信する曲には使いません（配信する曲は、枠ごとに別の曲から借りる「1 曲 1 枠」の決まり）。実名・歌詞・旋律の音は入っていません"}


# ---------------------------------------------------------------------------
# 5. 楽譜（MusicXML）：旋律＋コード名＋ベース
# ---------------------------------------------------------------------------
_TYPES = [(16, "whole", 0), (12, "half", 1), (8, "half", 0), (6, "quarter", 1), (4, "quarter", 0), (3, "eighth", 1), (2, "eighth", 0), (1, "16th", 0)]


def _split(n: int) -> list[tuple[int, str, int]]:
    """16 分音符いくつ分 → 書ける音符の長さの並び（大きい順）"""
    out = []
    for size, typ, dots in _TYPES:
        while n >= size:
            out.append((size, typ, dots))
            n -= size
    return out


def _line(notes, grid: Grid, lowest: bool = False) -> list[tuple[int, int, int]]:
    """音の並び → 1 本の旋律（始まりの 16 分の位置, 長さ, 高さ）。同時に鳴る音は、いちばん高い音（ベースは低い音）を取る"""
    slots: dict[int, tuple[int, int]] = {}
    for a, b, p, _ in notes:
        q = int(round((grid.beat_of(a) - grid.down) * 4))
        ln = max(1, int(round((grid.beat_of(b) - grid.beat_of(a)) * 4)))
        cur = slots.get(q)
        if cur is None or (p < cur[1] if lowest else p > cur[1]):
            slots[q] = (ln, p)
    seq = sorted(slots.items())
    out = []
    for i, (q, (ln, p)) in enumerate(seq):
        if i + 1 < len(seq):
            ln = min(ln, seq[i + 1][0] - q)
        if ln > 0:
            out.append((q, ln, p))
    return out


def _pitch_xml(p: int, flats: bool) -> str:
    step = (FLATS if flats else NAMES)[p % 12]
    alter = "<alter>-1</alter>" if len(step) > 1 and step[1] == "b" else "<alter>1</alter>" if len(step) > 1 else ""
    return f"<pitch><step>{step[0]}</step>{alter}<octave>{p // 12 - 1}</octave></pitch>"


def write_leadsheet(path: Path, title: str, tracks: dict, deep: dict) -> Path | None:
    """ボーカルの旋律（コード名つき）とベースの楽譜を MusicXML で書く。deep：analyze の結果（_grid が要る）"""
    from xml.sax.saxutils import escape
    gd = deep.get("_grid") or {}
    if not gd.get("beats") or not (tracks.get("vocals") or tracks.get("bass")):
        return None
    grid = Grid(gd["beats"], gd.get("meter", 4), gd.get("down", 0), (deep.get("groove") or {}).get("bpm") or 120)
    m = grid.meter
    per_bar = m * 4
    tonic, mode = gd.get("tonic", 0), gd.get("mode", "major")
    fifths = key_fifths(tonic, mode)
    flats = fifths < 0
    lines = {}
    if tracks.get("vocals"):
        lines["Vocal"] = (_line(tracks["vocals"], grid), "G", 2, 0)
    bass = sorted((tracks.get("bass") or []) + (tracks.get("synth_bass") or []))
    if bass:
        lines["Bass"] = (_line(bass, grid, lowest=True), "F", 4, 0)
    pre = min((q for ln, *_ in lines.values() for q, _, _ in ln), default=0)
    shift = -(pre // per_bar) * per_bar if pre < 0 else 0          # 最初の小節の頭より前に音があれば、小節ごと前に足す
    last = max((q + n for ln, *_ in lines.values() for q, n, _ in ln), default=0) + shift
    nbars = max(1, -(-last // per_bar))
    # コード名：拍ごとのコードを、変わった所だけ（16 分の位置）
    marks: dict[int, tuple] = {}
    prev = None
    for i, (root, qual, ext) in enumerate(gd.get("chords") or []):
        q = (i - grid.down) * 4 + shift
        cur = (root, qual, ext)
        if root is not None and cur != prev and 0 <= q < nbars * per_bar:
            marks[q] = cur
        prev = cur if root is not None else prev
    kind = {("maj", ""): "major", ("min", ""): "minor", ("maj", "7"): "dominant", ("maj", "maj7"): "major-seventh", ("min", "7"): "minor-seventh"}

    def harmony(c) -> str:
        step = (FLATS if flats else NAMES)[c[0] % 12]
        alter = "<root-alter>-1</root-alter>" if len(step) > 1 and step[1] == "b" else "<root-alter>1</root-alter>" if len(step) > 1 else ""
        return f"<harmony><root><root-step>{step[0]}</root-step>{alter}</root><kind>{kind.get((c[1], c[2]), 'major')}</kind></harmony>"
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">',
           '<score-partwise version="3.1">', f"<work><work-title>{escape(title)}</work-title></work>",
           "<identification><encoding><software>Postdog Records deep_analysis</software></encoding></identification>", "<part-list>"]
    for i, name in enumerate(lines, 1):
        xml.append(f'<score-part id="P{i}"><part-name>{name}</part-name></score-part>')
    xml.append("</part-list>")
    bpm = round(float((deep.get("groove") or {}).get("bpm") or 120))
    for i, (name, (seq, sign, cline, _)) in enumerate(lines.items(), 1):
        cells: dict[int, tuple[int, int]] = {q + shift: (n, p) for q, n, p in seq}
        xml.append(f'<part id="P{i}">')
        carry = None   # 前の小節から続く音（残りの長さ, 高さ）
        for bar in range(nbars):
            xml.append(f'<measure number="{bar + 1}">')
            if bar == 0:
                xml.append(f"<attributes><divisions>4</divisions><key><fifths>{fifths}</fifths><mode>{mode}</mode></key>"
                           f"<time><beats>{m}</beats><beat-type>4</beat-type></time><clef><sign>{sign}</sign><line>{cline}</line></clef></attributes>")
                if i == 1:
                    xml.append(f'<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>{bpm}</per-minute>'
                               f'</metronome></direction-type><sound tempo="{bpm}"/></direction>')
            pos, start = 0, bar * per_bar
            cuts = sorted({q - start for q in marks if start < q < start + per_bar}) if i == 1 else []

            def emit(n: int, pitch, tie_in: bool, tie_out: bool, at: int) -> None:
                """長さ n の音（か休み）を、コード名が付く位置で切りながら書く"""
                edges = [c for c in cuts if at < c < at + n]
                pieces, a = [], at
                for e in edges + [at + n]:
                    pieces.append((a, e - a))
                    a = e
                flat = [(pa, sz, typ, dots) for pa, pn in pieces for sz, typ, dots in _split(pn)]
                for k, (pa, sz, typ, dots) in enumerate(flat):
                    first = k == 0 or flat[k - 1][0] != pa      # この切れ目の最初の音符
                    if i == 1 and first and (start + pa) in marks:
                        xml.append(harmony(marks[start + pa]))
                    if pitch is None:
                        xml.append(f"<note><rest/><duration>{sz}</duration><type>{typ}</type>{'<dot/>' * dots}</note>")
                        continue
                    t_in = tie_in or k > 0
                    t_out = tie_out or k < len(flat) - 1
                    ties = ('<tie type="stop"/>' if t_in else "") + ('<tie type="start"/>' if t_out else "")
                    nots = ('<tied type="stop"/>' if t_in else "") + ('<tied type="start"/>' if t_out else "")
                    xml.append(f"<note>{_pitch_xml(pitch, flats)}<duration>{sz}</duration>{ties}<type>{typ}</type>{'<dot/>' * dots}"
                               + (f"<notations>{nots}</notations>" if nots else "") + "</note>")
            while pos < per_bar:
                if carry:
                    n = min(carry[0], per_bar - pos)
                    rest = carry[0] - n
                    emit(n, carry[1], True, rest > 0, pos)
                    carry = (rest, carry[1]) if rest > 0 else None
                    pos += n
                    continue
                if start + pos in cells:
                    ln, p = cells[start + pos]
                    n = min(ln, per_bar - pos)
                    emit(n, p, False, ln > n, pos)
                    carry = (ln - n, p) if ln > n else None
                    pos += n
                    continue
                nxt = min((q - start for q in cells if start + pos < q < start + per_bar), default=per_bar)
                emit(nxt - pos, None, False, False, pos)
                pos = nxt
            xml.append("</measure>")
        xml.append("</part>")
    xml.append("</score-partwise>")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(xml), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 6. 2 曲の近さ（答え合わせ）
# ---------------------------------------------------------------------------
def _cos(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _corr(a: list[float], b: list[float]) -> float:
    n = 32
    a, b = _resample(a, n), _resample(b, n)
    k = min(len(a), len(b))
    if k < 4:
        return 0.0
    a, b = a[:k], b[:k]
    ma, mb = sum(a) / k, sum(b) / k
    va, vb = sum((x - ma) ** 2 for x in a), sum((x - mb) ** 2 for x in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb) if va and vb else 0.0


def compare(a: dict, b: dict) -> dict:
    """2 つの分析結果の近さ（0〜100）。面ごとに出すので、分析のどこが伝わっていないかが分かる"""
    out: dict[str, int] = {}
    ga, gb = a.get("groove") or {}, b.get("groove") or {}
    if ga.get("bpm") and gb.get("bpm"):
        r = max(ga["bpm"], gb["bpm"]) / min(ga["bpm"], gb["bpm"])
        if abs(r - 2) < 0.16:                 # 倍・半分に聞こえる取り違え：同じ速さの曲でも起きるので、60 点まで
            out["テンポ"] = round(max(0.0, 1 - abs(r - 2) * 3) * 60)
        else:
            out["テンポ"] = round(max(0.0, 1 - (r - 1) * 4) * 100)
        out["拍子"] = 100 if ga.get("meter") == gb.get("meter") else 0
    ka, kb = parse_key((a.get("harmony") or {}).get("key")), parse_key((b.get("harmony") or {}).get("key"))
    if ka and kb:
        d = (kb[0] - ka[0]) % 12
        out["キー"] = 100 if ka == kb else 75 if ka[1] != kb[1] and ((ka[1] == "major" and d == 9) or (ka[1] == "minor" and d == 3)) \
            else 55 if ka[1] == kb[1] and d in (5, 7) else 45 if ka[1] == kb[1] else 15
    va = {x["chord"]: x["share"] for x in (a.get("harmony") or {}).get("vocabulary") or []}
    vb = {x["chord"]: x["share"] for x in (b.get("harmony") or {}).get("vocabulary") or []}
    if va and vb:
        out["コードの使い方"] = round(_cos(va, vb) * 100)
    da, db = (a.get("density") or {}).get("onsets_per_bar"), (b.get("density") or {}).get("onsets_per_bar")
    if da and db:
        out["音の多さ"] = round(max(0.0, 1 - abs(da - db) / max(da, db) * 1.5) * 100)
    ta, tb = (a.get("tension") or {}).get("per_bar") or [], (b.get("tension") or {}).get("per_bar") or []
    if ta and tb:
        out["盛り上がりの形"] = round(max(0.0, _corr(ta, tb)) * 100)
    pa, pb = (a.get("drums") or {}).get("pattern"), (b.get("drums") or {}).get("pattern")
    if pa and pb and len(pa["kick"]) == len(pb["kick"]):
        kinds = [k for k in ("kick", "snare", "hat") if "?" not in pa[k] and "?" not in pb[k]]   # 読めていない種類（?）は比べない
        hit = sum(1 for k in kinds for x, y in zip(pa[k], pb[k]) if x != "." or y != ".")
        both = sum(1 for k in kinds for x, y in zip(pa[k], pb[k]) if x != "." and y != ".")
        if kinds:
            out["ドラムの型"] = round(both / hit * 100) if hit else 100
    ia = {p["key"]: p["active"] for p in (a.get("instruments") or {}).get("parts") or []}
    ib = {p["key"]: p["active"] for p in (b.get("instruments") or {}).get("parts") or []}
    if ia and ib:
        out["楽器の構成"] = round(_cos(ia, ib) * 100)
    ma, mb = (a.get("mix") or {}).get("bands"), (b.get("mix") or {}).get("bands")
    if ma and mb:
        diff = sum(abs(10 * math.log10((ma[k] + 1e-4) / (mb[k] + 1e-4))) for k in ma) / len(ma)
        out["音の帯のバランス"] = round(max(0.0, 1 - diff / 12) * 100)
    weights = {"テンポ": 2, "拍子": 1, "キー": 1, "コードの使い方": 2, "音の多さ": 1.5, "盛り上がりの形": 2, "ドラムの型": 2, "楽器の構成": 2, "音の帯のバランス": 1}
    tot = sum(weights[k] for k in out)
    weak = sorted(out, key=lambda k: out[k])[:3]
    return {"overall": round(sum(out[k] * weights[k] for k in out) / tot) if tot else None, "dimensions": out, "weakest": [k for k in weak if out[k] < 70]}


# ---------------------------------------------------------------------------
# 7. 解析シートへ入れる形・ブリーフへ渡す形
# ---------------------------------------------------------------------------
SLOT_PARTS = {   # 枠 → 深い分析のどの部分だけを渡すか（1 曲 1 枠：ほかの部分は渡さない。再現カードはどの枠にも渡さない）
    "instruments": ["instruments", "mix"], "performance": ["density", "bass"], "structure": ["structure", "tension"],
    "harmony": ["harmony"], "groove": ["groove", "drums"], "vocal_main": ["vocal"],
}


def for_slot(deep: dict | None, slot: str) -> dict:
    """ブリーフの 1 枠に渡す部分（その枠の測りものだけ）"""
    if not deep or (deep.get("version") or 0) < 1:
        return {}
    out = {}
    for k in SLOT_PARTS.get(slot, []):
        v = deep.get(k)
        if isinstance(v, dict) and v.get("usable") is False:
            continue
        if v:
            v = json.loads(json.dumps(v))
            if k == "tension":
                v.pop("per_bar", None)
                v["curve_16"] = _resample((deep.get("tension") or {}).get("per_bar") or [], 16)
            if k == "instruments":
                v.pop("listed", None)       # 名札は解析シートの欄（C）にすでにある
            out[k] = v
    return out


def to_sheet(d: dict, deep: dict) -> None:
    """測った結果を解析シートに入れる（人や前の解析が書いた欄は上書きしない）"""
    d["deep"] = {k: v for k, v in deep.items() if k != "_grid"}
    h, g = d.setdefault("harmony", {}), d.setdefault("groove", {})
    dh = deep.get("harmony") or {}
    if h.get("progression_character") in (None, "", "不明"):
        h["progression_character"] = dh.get("progression_character")
    if not h.get("modulation_count") and dh.get("modulation_count"):
        h["modulation_count"] = dh["modulation_count"]
        h["modulations"] = [f"{x['section']}（{x['at_sec']} 秒）で {x['to']} へ" for x in dh.get("modulations") or []]
    bs = deep.get("bass") or {}
    if bs.get("usable"):
        if not h.get("bass_motion"):
            h["bass_motion"] = bs["motion"]
        if not g.get("kick_bass_relation") and bs.get("kick_bass_relation"):
            g["kick_bass_relation"] = bs["kick_bass_relation"]
    labels = (deep.get("drums") or {}).get("labels") or []
    hat = next((x for x in labels if "ハイハット" in x), None)
    if hat and not g.get("hihat_pattern"):
        g["hihat_pattern"] = hat
    if (deep.get("groove") or {}).get("tempo_drift") and not g.get("tempo_drift"):
        g["tempo_drift"] = deep["groove"]["tempo_drift"]
    if "深い分析" not in str(d.get("analyzed_by", "")):
        d["analyzed_by"] = str(d.get("analyzed_by", "auto")) + " + 深い分析"


def summary_line(deep: dict) -> str:
    g, h, den, t = (deep.get(k) or {} for k in ("groove", "harmony", "density", "tension"))
    lp = h.get("loop") or {}
    return (f"{g.get('bpm')} BPM・{g.get('time_signature', '4/4')}・{h.get('key')}／進行 {' | '.join(lp['pattern']) if lp.get('pattern') else '—'}"
            f"／音の多さ {den.get('level', '—')}（1 小節 {den.get('onsets_per_bar')} 音）／盛り上がり {t.get('shape', '—')}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "compare" and len(args) == 3:
        a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args[1:])
        a, b = a.get("deep", a), b.get("deep", b)
        r = compare(a, b)
        print(f"【結果】全体の近さ {r['overall']} / 100")
        for k, v in r["dimensions"].items():
            print(f"  {k}：{v}")
        if r["weakest"]:
            print("【次にすること】近さが低い面（" + "・".join(r["weakest"]) + "）の書き方を、再現カードで見直します")
        return
    if not args or args[0].startswith("-"):
        print(__doc__)
        return
    audio = Path(args[0])
    tracks = None
    if "--midi" in args:
        tracks = read_midi(Path(args[args.index("--midi") + 1]))
    d = analyze(audio, tracks=tracks, say=lambda s: print(f"▶ {s}…", flush=True))
    if "--json" in args:
        print(json.dumps({k: v for k, v in d.items() if k != "_grid"}, ensure_ascii=False, indent=1))
        return
    print("【結果】" + summary_line(d))
    print("  印象：" + "・".join(d["impression"]["words_ja"]))
    if (d.get("drums") or {}).get("pattern"):
        p = d["drums"]["pattern"]
        print(f"  ドラム：キック {p['kick']}／スネア {p['snare']}／ハイハット {p['hat']}（{'・'.join(d['drums'].get('labels') or [])}）")
    print("  再現カード（スタイル欄・" + str(d["card"]["style_chars"]) + " 文字）：" + d["card"]["style"])
    for ln in d["card"]["structure"]:
        print("   " + ln)


if __name__ == "__main__":
    main()
