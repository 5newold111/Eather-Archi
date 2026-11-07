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

2026-11-07（決定 159。オーナー「クオリティが壊滅的」）：
  ・音符の書き起こしは、Basic Pitch（Spotify が公開している、音から音符を起こす部品。無料・Mac の中）が入っていればそれを使う。
    無ければ前のやり方（pYIN）
  ・品質の確認：音が細かく割れすぎ・音域が広すぎ・跳びすぎの書き起こしは「乱れている」として usable=False（曲作りに渡さない・SSD に MIDI を残さない）
  ・Demucs が無いと歌の取り出しが粗く、伴奏が混ざって乱れやすい（入れ方は docs/12 の決定 159）
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
VERSION = 3   # 書き起こしのやり方の版（上がると、前の版で起こした曲を起こし直す。決定 159・161）
STEMS = {"vocals": "ボーカル", "bass": "ベース", "drums": "ドラム", "other": "その他の楽器"}   # Demucs（htdemucs）の 4 つ
GM_PROGRAM = {"vocals": 53, "bass": 33, "other": 0}   # MIDI の音色（Voice Oohs・Electric Bass・Piano）。ドラムは 10 チャンネル
TRACK_NAME = {"vocals": "Vocals", "bass": "Bass", "drums": "Drums", "other": "Instruments"}   # MIDI のトラック名は英字だけ書ける
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


def separate(path: Path, out_dir: Path) -> tuple[dict[str, Path], str]:
    """Demucs で 4 つ（ボーカル・ベース・ドラム・その他の楽器）に分ける（決定 161）。({名前: wav}, 説明)。できなければ ({}, 理由)"""
    if not demucs_ready():
        return {}, "Demucs が入っていません"
    r = subprocess.run([sys.executable, "-m", "demucs", "-n", "htdemucs", "-o", str(out_dir), str(path)], capture_output=True, text=True)
    got = {k: next(iter(out_dir.rglob(f"{k}.wav")), None) for k in STEMS}
    if r.returncode or not all(got.values()):
        tail = (r.stderr or r.stdout).strip().splitlines()
        return {}, "Demucs で分けられませんでした：" + (tail[-1][:200] if tail else f"終了コード {r.returncode}")
    return {k: v for k, v in got.items() if v}, "Demucs（htdemucs）で 4 つに分けました"


def poly_notes(wav: Path, kind: str) -> list[tuple[float, float, int, int]]:
    """Basic Pitch で、重なっている音も全部起こす（和音・ハモりもそのまま）。[(始まり秒, 終わり秒, 高さ, 強さ 1〜127)]"""
    from basic_pitch.inference import predict
    lo, hi = {"vocals": (70, 1400), "bass": (28, 400), "other": (40, None)}.get(kind, (None, None))
    _o, _m, events = predict(str(wav), onset_threshold=0.5, frame_threshold=0.3, minimum_note_length=80,
                             minimum_frequency=lo, maximum_frequency=hi, melodia_trick=True)
    ns = [(float(a), float(b), int(p), max(1, min(127, int(round(float(v) * 127))))) for a, b, p, v, *_ in events if b > a]
    return join_tails(drop_overtones(ns))


def join_tails(ns: list[tuple[float, float, int, int]]) -> list[tuple[float, float, int, int]]:
    """音の終わりの余韻が、同じ高さの短く弱い別の音として出たものを、前の音につなぐ"""
    out: list[list] = []
    for a, b, p, v in sorted(ns):
        prev = next((x for x in reversed(out) if x[2] == p), None)
        if prev and 0 <= a - prev[1] < 0.04 and b - a < 0.15 and v <= prev[3]:
            prev[1] = max(prev[1], b)
            continue
        out.append([a, b, p, v])
    return [tuple(x) for x in out]


def drop_overtones(ns: list[tuple[float, float, int, int]]) -> list[tuple[float, float, int, int]]:
    """倍音（同じ時に鳴る、もっと強い音の 1・2 オクターブ上や 12 度上）として出た弱い音を除く"""
    out = []
    for a, b, p, v in ns:
        dur = b - a
        over = False
        for a2, b2, p2, v2 in ns:
            if p - p2 in (12, 19, 24, 28, 31) and v2 >= v * 0.9:
                ov = min(b, b2) - max(a, a2)
                if ov > 0.6 * dur:
                    over = True
                    break
        if not over:
            out.append((a, b, p, v))
    return out


def drum_notes(wav: Path) -> list[tuple[float, float, int, int]]:
    """ドラムの音を、キック（36）・スネア（38）・ハイハット（42）に分けて起こす（音の帯ごとの立ち上がりで見分ける）"""
    import librosa
    import numpy as np
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=256))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    P = S ** 2
    bands = {36: (20, 150), 38: (150, 2500), 42: (6000, 11000)}
    power = {k: P[(freqs >= lo) & (freqs < hi)].sum(axis=0) for k, (lo, hi) in bands.items()}

    def strongest_here(note: int, f: int) -> bool:   # その時の音の強さで確かめる：キックは低い帯、スネアは真ん中の帯がハイハットの帯より強い
        g = slice(f, min(f + 4, P.shape[1]))
        if note == 36:
            return power[36][g].sum() > power[38][g].sum() * 0.5
        if note == 38:
            return power[38][g].sum() > power[42][g].sum() * 0.15 and power[38][g].sum() > power[36][g].sum() * 0.3
        return True
    out = []
    for note, (lo, hi) in bands.items():
        band = S[(freqs >= lo) & (freqs < hi)]
        if not band.size:
            continue
        env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(band, ref=np.max), sr=sr, hop_length=256)
        on = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=256, units="time", backtrack=False,
                                        pre_max=3, post_max=3, pre_avg=10, post_avg=10, delta=0.35, wait=4)
        peak = float(np.max(env)) or 1.0
        frames = librosa.time_to_frames(on, sr=sr, hop_length=256)
        last = -1.0
        for t, f in zip(on, frames):
            if t - last < 0.05 or not strongest_here(note, int(f)):
                continue
            last = t
            v = int(40 + 87 * min(1.0, float(env[min(f, len(env) - 1)]) / peak))
            out.append((float(t), float(t) + 0.1, note, v))
    return sorted(out)


def write_full_midi(tracks: dict[str, list[tuple[float, float, int, int]]], bpm: float, path: Path) -> None:
    """全部の音を、楽器ごとのトラックに分けた MIDI（形式 1）に書く。時刻は曲の秒のまま（演奏の揺れもそのまま）。GarageBand・Logic で開ける"""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(bpm or 120))
    for kind in ("vocals", "other", "bass", "drums"):
        ns = tracks.get(kind) or []
        if not ns:
            continue
        inst = pretty_midi.Instrument(program=GM_PROGRAM.get(kind, 0), is_drum=(kind == "drums"), name=TRACK_NAME[kind])
        inst.notes = [pretty_midi.Note(velocity=v, pitch=p, start=a, end=b) for a, b, p, v in ns]
        pm.instruments.append(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(path))


def export_audio(src: list[Path], dst: Path) -> Path | None:
    """分けた音を 1 つにまとめて書き出す（ffmpeg があれば m4a〔小さい〕、無ければ wav）"""
    import shutil
    import numpy as np
    import soundfile as sf
    mix, sr = None, 44100
    for f in src:
        y, sr = sf.read(str(f), always_2d=True)
        mix = y if mix is None else mix[: min(len(mix), len(y))] + y[: min(len(mix), len(y))]
    if mix is None:
        return None
    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 0.99:
        mix = mix / peak * 0.99
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "x.wav"
        sf.write(str(wav), mix, sr)
        ff = shutil.which("ffmpeg") or next((x for x in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if Path(x).exists()), None)
        if ff:
            out = dst.with_suffix(".m4a")
            if subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "256k", str(out)]).returncode == 0:
                return out
        out = dst.with_suffix(".wav")
        shutil.copy2(wav, out)
        return out


def basic_pitch_ready() -> bool:
    """Basic Pitch が「動く」か（書き起こしの本体まで読み込めるか）。部品の名前だけ入っていて、中で使う部品（setuptools の
    pkg_resources など）が無いと、前は黙って前のやり方に戻っていた（2026-11-07。決定 161）"""
    try:
        from basic_pitch.inference import predict  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def notes_basic_pitch(y, sr: int, bpm: float | None) -> tuple[list[dict], float, str]:
    """Basic Pitch で歌の波形から音符を起こす（決定 159）。同じ時刻に音が重なれば大きい方（旋律）を残す"""
    import librosa
    import numpy as np
    import soundfile as sf
    from basic_pitch.inference import predict
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "vocals.wav"
        sf.write(str(wav), y, sr)
        _out, _midi, events = predict(str(wav), onset_threshold=0.5, frame_threshold=0.3, minimum_note_length=110,
                                      minimum_frequency=70, maximum_frequency=1100, melodia_trick=True)
    ev = sorted(((float(a), float(b), int(p), float(v)) for a, b, p, v, *_ in events), key=lambda x: (x[0], -x[3]))
    mono: list[list] = []
    for a, b, p, v in ev:   # 重なり：大きい音を残し、小さい方は切る・捨てる
        if mono and a < mono[-1][1]:
            if v > mono[-1][3]:
                if a - mono[-1][0] < 0.08:
                    mono[-1] = [a, b, p, v]
                    continue
                mono[-1][1] = a
            else:
                if b > mono[-1][1] + 0.08:
                    a = mono[-1][1]
                else:
                    continue
        mono.append([a, b, p, v])
    tempo = bpm
    if not tempo:
        t, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(np.atleast_1d(t)[0]) or 100.0
    beat = 60.0 / tempo
    if mono:   # 歌のよく使う高さから 14 半音より外れた音は、伴奏（ベース・和音）が混ざったものとして除く
        mid = float(np.median([x[2] for x in mono]))
        mono = [x for x in mono if abs(x[2] - mid) <= 14]
    notes = []
    for a, b, p, _v in mono:
        if b - a < 0.09:
            continue
        sb = round(a / beat * 4) / 4
        nb = max(0.25, round((b - a) / beat * 4) / 4)
        if notes and abs(sb - notes[-1]["start_beat"]) < 1e-6:
            continue
        notes.append({"start_beat": sb, "beats": nb, "pitch": p})
    _fix_octaves(notes)
    for i in range(len(notes) - 1):
        gap = notes[i + 1]["start_beat"] - notes[i]["start_beat"]
        if notes[i]["beats"] > gap:
            notes[i]["beats"] = max(0.25, gap)
    return notes, float(tempo), f"音 {len(notes)} 個（Basic Pitch）"


def _fix_octaves(notes: list[dict]) -> None:
    """1 オクターブのずれを直す（前後とくらべて 12 半音近く飛ぶ音）"""
    for i in range(1, len(notes) - 1):
        a, b, c = notes[i - 1]["pitch"], notes[i]["pitch"], notes[i + 1]["pitch"]
        for k in (-12, 12):
            if abs(b + k - a) < abs(b - a) - 7 and abs(b + k - c) < abs(b - c) - 7:
                notes[i]["pitch"] = b + k


def quality(notes: list[dict]) -> str:
    """書き起こしが乱れていれば、その理由（空なら使える。決定 159）"""
    if len(notes) < 8:
        return ""
    ps = [n["pitch"] for n in notes]
    sp = sorted(ps)
    span = sp[int(len(sp) * 0.95) - 1] - sp[int(len(sp) * 0.05)]   # 外れた数音に引っぱられないよう、真ん中 9 割の幅
    iv = sorted(abs(b - a) for a, b in zip(ps, ps[1:]))
    med_iv = iv[len(iv) // 2] if iv else 0
    bars = max(1.0, (notes[-1]["start_beat"] + notes[-1]["beats"] - notes[0]["start_beat"]) / 4)
    npb = len(notes) / bars
    tiny = sum(1 for n in notes if n["beats"] <= 0.25) / len(notes)
    big = sum(1 for x in iv if x >= 7) / max(1, len(iv))
    why = []
    if npb > 12:
        why.append(f"1 小節に {npb:.0f} 音（細かく割れすぎ）")
    if tiny > 0.6:
        why.append(f"16 分音符より短い音が {int(tiny * 100)}%")
    if span > 26:
        why.append(f"音域が {span} 半音（広すぎ）")
    if med_iv > 5 or big > 0.35:
        why.append(f"跳躍が多すぎ（5 度以上の跳びが {int(big * 100)}%）")
    return "・".join(why)


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
    _fix_octaves(notes)
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
    """曲 1 つ（決定 161）：Demucs で ボーカル・ベース・ドラム・その他の楽器 に分ける → それぞれの全部の音を Basic Pitch で起こし、
    楽器ごとのトラックの MIDI にする → ボーカルと演奏（ボーカル以外）の音も書き出す → 曲作りに渡す旋律の特徴はボーカルの主旋律から。
    (特徴, 書き出したフォルダ〔MELODY_DIR/<曲>/〕)。Demucs か Basic Pitch が無ければ、前のやり方（主旋律だけの MIDI）"""
    bpm = (sheet.get("groove") or {}).get("bpm") or None
    key = (sheet.get("_measure") or {}).get("key")
    safe = re.sub(r'[\\/:*?"<>|]+', "_", out_name).strip() or "melody"
    if demucs_ready() and basic_pitch_ready():
        tmp = Path(tempfile.mkdtemp())
        try:
            stems, how = separate(audio, tmp)
            if stems:
                return _transcribe_stems(stems, how, bpm, key, sheet, out_name, safe)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        how = "Demucs か Basic Pitch が入っていません"
    y, sr, how2 = vocals(audio)
    how = f"{how}（{how2} で歌だけを取り出しました）"
    tool = "pyin"
    if basic_pitch_ready():
        try:
            notes, tempo, note = notes_basic_pitch(y, sr, bpm)
            tool = "basic_pitch"
        except Exception as e:  # noqa: BLE001  Basic Pitch の動く部品が無いときなど：前のやり方へ
            notes, tempo, note = notes_from(y, sr, bpm)
            note += f"（Basic Pitch は動きませんでした：{type(e).__name__}）"
    else:
        notes, tempo, note = notes_from(y, sr, bpm)
    feat = features(notes, tempo, key, (sheet.get("structure") or {}).get("sections") or [])
    bad = quality(notes)
    if bad and feat.get("usable"):   # 乱れた書き起こしは曲作りに渡さない（決定 159）
        feat = {"usable": False, "note": f"書き起こしが乱れているので使いません：{bad}"}
    feat.update({"separation": how, "transcriber": tool, "version": VERSION, "bpm_used": round(tempo, 1), "detail": note})
    mid = None
    if notes:
        mid = MELODY_DIR / safe / f"{safe}.mid"
        write_midi(notes, tempo, mid, out_name[:60])
        feat["midi_file"] = str(mid)
    return feat, (mid.parent if mid else None)


def _transcribe_stems(stems: dict[str, Path], how: str, bpm, key, sheet: dict, out_name: str, safe: str) -> tuple[dict, Path]:
    """分けた 4 つから：全部の音の MIDI・ボーカル／演奏の音・曲作りに渡す旋律の特徴（決定 161）"""
    import librosa
    folder = MELODY_DIR / safe
    folder.mkdir(parents=True, exist_ok=True)
    yv, sr = librosa.load(str(stems["vocals"]), sr=22050, mono=True)
    lead, tempo, note = notes_basic_pitch(yv, sr, bpm)   # 曲作りに渡す特徴は、ボーカルの主旋律（1 本）から
    tracks = {k: poly_notes(stems[k], k) for k in ("vocals", "bass", "other") if k in stems}
    if "drums" in stems:
        tracks["drums"] = drum_notes(stems["drums"])
    write_full_midi(tracks, tempo, folder / f"{safe}.mid")
    export_audio([stems["vocals"]], folder / f"{safe}_ボーカル")
    export_audio([stems[k] for k in ("drums", "bass", "other") if k in stems], folder / f"{safe}_演奏")
    feat = features(lead, tempo, key, (sheet.get("structure") or {}).get("sections") or [])
    bad = quality(lead)
    if bad and feat.get("usable"):
        feat = {"usable": False, "note": f"主旋律の書き起こしが乱れているので、曲作りには使いません：{bad}"}
    counts = "・".join(f"{STEMS[k]} {len(v)} 音" for k, v in tracks.items())
    feat.update({"separation": how, "transcriber": "basic_pitch", "version": VERSION, "bpm_used": round(tempo, 1),
                 "detail": f"{note}／全部の音の MIDI：{counts}", "midi_file": str(folder / f"{safe}.mid"), "folder": str(folder)})
    return feat, folder


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--json":   # 別の Python から 1 曲（決定 160）：--json <音源> <解析シート> <名前>
    sh = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    f, m = transcribe_melody(Path(sys.argv[2]), sh, sys.argv[4])
    print("@@RESULT@@" + json.dumps({"feat": f, "folder": str(m) if m else None}, ensure_ascii=False))
elif __name__ == "__main__":   # 試しに 1 曲：python3 scripts/melody.py <音源> [テンポ] [キー]
    p = Path(sys.argv[1])
    sh = {"groove": {"bpm": float(sys.argv[2])} if len(sys.argv) > 2 else {}, "_measure": {"key": sys.argv[3] if len(sys.argv) > 3 else None}}
    f, m = transcribe_melody(p, sh, p.stem)
    print(json.dumps(f, ensure_ascii=False, indent=1))
    print("MIDI:", m, "／ 歌の取り出し：", "Demucs" if demucs_ready() else "librosa", "／ 音符：", "Basic Pitch" if basic_pitch_ready() else "pYIN")
