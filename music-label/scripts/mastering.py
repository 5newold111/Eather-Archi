#!/usr/bin/env python3
"""
仕上げ（マスタリング）：選んだテイクを、配信に出せる音に整える（決定 168）

  これまでは「音量をそろえるだけ」だった。ここでは曲を測って、その曲に要る手当てだけをする。
    1 掃除        … 前後の無音を整える・聞こえない超低音（25 Hz より下）と直流のずれを取る・途中で切れた終わりにフェードを付ける
    2 音のバランス … 低い音〜高い音の 1/3 オクターブごとの量を測り、目標の傾き（市販曲に近い なだらかな右下がり）との差を
                      なめらかな EQ（音の高さごとの音量調整）で半分ほど埋める。動かす幅は強さで決まる（弱 ±2・中 ±3.5・強 ±5 dB）
    3 耳に痛い高音 … Suno に出やすい 5〜11 kHz のシャリつき・サ行の刺さりを、強く出た瞬間だけ下げる（ディエッサー）
    4 低音の整理   … 120 Hz より下を真ん中に寄せる（左右に散った低音はスピーカーやクラブでぼやけるため）
    5 強弱         … 低・中・高の 3 つの帯で、飛び出した瞬間だけ軽く押さえる。すでに強くつぶれている曲には何もしない
    6 音量と天井   … -14 LUFS にそろえ、波形の本当の最大値（トゥルーピーク）が -1 dBTP を超えないように先読みのリミッターで止める
    7 確認         … 書き出したファイルを ffmpeg で測り直す。外れていたら天井を下げてやり直す

  味付け（style）：auto おまかせ / warm あたたかく / bright 明るく・クリア / punch 太く・力強く / light そのまま（掃除と音量だけ）
                    / ref 参考曲に寄せる（--ref で音源を指定。その曲の音のバランスを目標にする）
  強さ（strength）：low 弱 / mid 中 / high 強

  出力：44.1 kHz / 24 bit の WAV（DistroKid に登録する形式）と、聴き比べ用の小さいファイル
        （preview_after.m4a＝仕上げ後、preview_before.m4a＝仕上げ前を同じ音量にそろえたもの。大きい音のほうが良く聞こえるのを防ぐ）
  記録：master.report.json（前と後の測定値・した手当てとその理由・味付け・版）

使い方
  python3 scripts/mastering.py song <曲 ID> [--style auto] [--strength mid]   # 1 曲を仕上げ直す（ダッシュボードの「仕上げ直す」）
  python3 scripts/mastering.py file <元の音源> --out master.wav               # ファイルを直接
  python3 scripts/mastering.py analyze <音源>                                  # 測るだけ
  python3 scripts/mastering.py default <組の slug|all> --style warm --strength mid   # その組の既定の味付け
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, read_json, step, write_json  # noqa: E402

VERSION = 2                       # 仕上げの作り方の版（1＝音量だけ。2＝この仕上げ）。上げると、まだ登録していない曲を仕上げ直す
SR = 44100
TARGET_LUFS, CEILING_DBTP = -14.0, -1.0
SETTINGS = OUT / "mastering" / "settings.json"
TARGETS = OUT / "mastering" / "targets.json"      # 市販曲から学んだ目標（mastering.py learn）
SPEC_CACHE = OUT / "mastering" / "ref_spec"
STYLES = {"auto": "おまかせ（曲に合わせる）", "warm": "あたたかく", "bright": "明るく・クリア", "punch": "太く・力強く",
          "light": "そのまま（掃除と音量だけ）", "ref": "参考曲に寄せる"}
STRENGTHS = {"low": "弱", "mid": "中", "high": "強"}
EQ_LIMIT = {"low": 2.0, "mid": 3.5, "high": 5.0}      # EQ で動かす幅（dB）
EQ_AMOUNT = {"low": 0.5, "mid": 0.7, "high": 0.9}     # 目標との差をどれだけ埋めるか
BANDS = [("超低音", 20, 60), ("低音", 60, 250), ("中低音", 250, 500), ("中音", 500, 2000),
         ("中高音", 2000, 4000), ("高音", 4000, 8000), ("超高音", 8000, 16000)]
THIRD_OCT = [25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
             2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000]


# ---------------------------------------------------------------------------
# 読み書き
def need_tools() -> str:
    """足りない道具の説明（そろっていれば空）"""
    if not shutil.which("ffmpeg"):
        return "音を読み書きする道具（ffmpeg）がありません。ターミナルで  brew install ffmpeg  を 1 回実行してください"
    try:
        import scipy  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError as e:
        return f"音を処理する部品（{e.name}）がありません。ターミナルで  python3 -m pip install --user -r requirements.txt  を実行してください"
    return ""


def load(src: Path) -> np.ndarray:
    """どの形式でも 44.1 kHz・ステレオ・小数の波形（[サンプル数, 2]）にして読む"""
    import soundfile as sf
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "in.wav"
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SR),
                            "-af", "aresample=resampler=soxr:precision=28" if _has_soxr() else "aresample",
                            "-c:a", "pcm_f32le", str(tmp)], capture_output=True, text=True)
        if r.returncode != 0 or not tmp.exists():
            raise RuntimeError("音源を読めませんでした：" + r.stderr[-300:])
        x, _ = sf.read(tmp, dtype="float32", always_2d=True)
    return x.astype(np.float64)


_SOXR: list[bool] = []


def _has_soxr() -> bool:
    if not _SOXR:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-buildconf"], capture_output=True, text=True)
        _SOXR.append("--enable-libsoxr" in (r.stdout + r.stderr))
    return _SOXR[0]


def save(x: np.ndarray, dst: Path) -> None:
    import soundfile as sf
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.stem}.part.wav")
    sf.write(tmp, np.clip(x, -1.0, 1.0), SR, subtype="PCM_24")
    tmp.replace(dst)


def preview(x: np.ndarray, dst: Path) -> bool:
    """聴き比べ用の小さいファイル（AAC 256 kbps）"""
    import soundfile as sf
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "p.wav"
        sf.write(tmp, np.clip(x, -1.0, 1.0), SR, subtype="PCM_16")
        part = dst.with_name(f".{dst.stem}.part.m4a")
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(tmp), "-c:a", "aac", "-b:a", "256k",
                            "-movflags", "+faststart", str(part)], capture_output=True, text=True)
        if r.returncode != 0:
            part.unlink(missing_ok=True)
            return False
        part.replace(dst)
    return True


# ---------------------------------------------------------------------------
# 測る
def _kweight(x: np.ndarray) -> np.ndarray:
    """人の耳の感じ方に合わせる重み付け（ITU-R BS.1770 の K 特性。44.1 kHz 用に係数を計算）"""
    from scipy.signal import lfilter

    def shelf(f0, gain_db, q):
        a = 10 ** (gain_db / 40)
        w = 2 * np.pi * f0 / SR
        al = np.sin(w) / (2 * q)
        c = np.cos(w)
        b0 = a * ((a + 1) + (a - 1) * c + 2 * np.sqrt(a) * al)
        b1 = -2 * a * ((a - 1) + (a + 1) * c)
        b2 = a * ((a + 1) + (a - 1) * c - 2 * np.sqrt(a) * al)
        a0 = (a + 1) - (a - 1) * c + 2 * np.sqrt(a) * al
        a1 = 2 * ((a - 1) - (a + 1) * c)
        a2 = (a + 1) - (a - 1) * c - 2 * np.sqrt(a) * al
        return np.array([b0, b1, b2]) / a0, np.array([1, a1 / a0, a2 / a0])

    def highpass(f0, q):
        w = 2 * np.pi * f0 / SR
        al = np.sin(w) / (2 * q)
        c = np.cos(w)
        a0 = 1 + al
        return np.array([(1 + c) / 2, -(1 + c), (1 + c) / 2]) / a0, np.array([1, -2 * c / a0, (1 - al) / a0])

    b, a = shelf(1681.974450955533, 3.999843853973347, 0.7071752369554196)
    y = lfilter(b, a, x, axis=0)
    b, a = highpass(38.13547087602444, 0.5003270373238773)
    return lfilter(b, a, y, axis=0)


def lufs(x: np.ndarray) -> float:
    """統合ラウドネス（LUFS）。400 ms の窓を 100 ms ずつずらし、静かすぎる窓を除いて平均する"""
    y = _kweight(x)
    win, hop = int(0.4 * SR), int(0.1 * SR)
    if len(y) < win:
        return -70.0
    p = np.sum(y * y, axis=1)                      # 左右を足した 2 乗
    c = np.concatenate([[0.0], np.cumsum(p)])
    idx = np.arange(0, len(y) - win + 1, hop)
    z = (c[idx + win] - c[idx]) / win              # 窓ごとの平均（左右の合計）
    l = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    keep = l > -70.0
    if not keep.any():
        return -70.0
    rel = -0.691 + 10 * np.log10(np.mean(z[keep])) - 10.0
    keep &= l > rel
    return float(-0.691 + 10 * np.log10(np.mean(z[keep]))) if keep.any() else -70.0


def true_peak(x: np.ndarray, block: int = 1 << 18) -> float:
    """波形の本当の最大値（4 倍に細かくして測る。dBTP）"""
    from scipy.signal import resample_poly
    peak, pad = 0.0, 64
    for ch in range(x.shape[1]):
        for i in range(0, len(x), block):
            seg = x[max(0, i - pad): i + block + pad, ch].astype(np.float32)
            if len(seg) < 8:
                continue
            peak = max(peak, float(np.max(np.abs(resample_poly(seg, 4, 1)))))
    return float(20 * np.log10(max(peak, 1e-9)))


def spectrum(x: np.ndarray) -> np.ndarray:
    """曲全体の音の高さごとの量（1/3 オクターブ、dB。真ん中の成分で測る）"""
    from scipy.signal import welch
    mid = x.mean(axis=1)
    f, p = welch(mid, SR, nperseg=16384, noverlap=8192)
    out = []
    for fc in THIRD_OCT:
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        m = (f >= lo) & (f < hi)
        out.append(10 * np.log10(max(float(p[m].sum()) if m.any() else 0.0, 1e-20)))
    return np.array(out)


def band_levels(spec: np.ndarray) -> dict[str, float]:
    """7 つの帯の量（全体に対する dB）"""
    f = np.array(THIRD_OCT)
    lin = 10 ** (spec / 10)
    tot = lin.sum()
    return {n: round(float(10 * np.log10(max(lin[(f >= lo) & (f < hi)].sum(), 1e-20) / tot)), 1) for n, lo, hi in BANDS}


def edges(x: np.ndarray) -> tuple[int, int]:
    """音が始まる位置と終わる位置（-60 dB より大きい最初と最後のサンプル）"""
    env = np.max(np.abs(x), axis=1)
    idx = np.flatnonzero(env > 10 ** (-60 / 20))
    return (int(idx[0]), int(idx[-1]) + 1) if len(idx) else (0, len(x))


def analyze(x: np.ndarray) -> dict:
    mid, side = x.mean(axis=1), (x[:, 0] - x[:, 1]) / 2
    rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
    peak = float(np.max(np.abs(x)) + 1e-12)
    a, b = edges(x)
    spec = spectrum(x)
    f = np.array(THIRD_OCT)
    lin = 10 ** (spec / 10)
    harsh = float(10 * np.log10(lin[(f >= 5000) & (f <= 10000)].sum() / max(lin[(f >= 500) & (f <= 4000)].sum(), 1e-20)))
    lowside = _lowpass(side, 120.0)
    lowmid = _lowpass(mid, 120.0)
    corr = float(np.corrcoef(x[:, 0], x[:, 1])[0, 1]) if x[:, 0].std() > 0 and x[:, 1].std() > 0 else 1.0
    tail = x[max(a, b - int(0.05 * SR)): b]
    return {
        "lufs": round(lufs(x), 2), "true_peak_db": round(true_peak(x), 2),
        "peak_db": round(20 * np.log10(peak), 2), "rms_db": round(20 * np.log10(rms), 2),
        "crest_db": round(20 * np.log10(peak / rms), 1),              # 最大値と平均の差。小さいほど強くつぶれている
        "dc": round(float(np.max(np.abs(x.mean(axis=0)))), 5),
        "lead_silence_sec": round(a / SR, 3), "tail_silence_sec": round((len(x) - b) / SR, 3),
        "abrupt_end": bool(len(tail) and 20 * np.log10(float(np.sqrt(np.mean(tail * tail))) + 1e-12) > -40.0),
        "length_sec": round(len(x) / SR, 2),
        "width_db": round(float(20 * np.log10((np.sqrt(np.mean(side * side)) + 1e-12) / (np.sqrt(np.mean(mid * mid)) + 1e-12))), 1),
        "low_side_db": round(float(20 * np.log10((np.sqrt(np.mean(lowside ** 2)) + 1e-12) / (np.sqrt(np.mean(lowmid ** 2)) + 1e-12))), 1),
        "correlation": round(corr, 2), "harsh_db": round(harsh, 1),
        "clipped": int(np.sum(np.abs(x) >= 0.999)),
        "bands": band_levels(spec), "third_oct": [round(float(v), 2) for v in spec],
    }


# ---------------------------------------------------------------------------
# 手当ての部品
def _sos(kind: str, freq, order: int = 2):
    from scipy.signal import butter
    return butter(order, freq, btype=kind, fs=SR, output="sos")


def _lowpass(x: np.ndarray, fc: float, order: int = 2) -> np.ndarray:
    from scipy.signal import sosfiltfilt
    return sosfiltfilt(_sos("lowpass", fc, order), x, axis=0)


def _highpass(x: np.ndarray, fc: float, order: int = 2) -> np.ndarray:
    from scipy.signal import sosfiltfilt
    return sosfiltfilt(_sos("highpass", fc, order), x, axis=0)


def _bandpass(x: np.ndarray, lo: float, hi: float, order: int = 2) -> np.ndarray:
    from scipy.signal import sosfiltfilt
    return sosfiltfilt(_sos("bandpass", [lo, hi], order), x, axis=0)


def builtin_curve(style: str) -> np.ndarray:
    """組み込みの目標（1/3 オクターブごとの量、dB。形だけが意味を持つ）。
    市販のポップスの平均に近い形：80〜100 Hz が山、そこから 4 kHz まで 1 オクターブ上がるごとに 2 dB ほど下がり、4 kHz より上はもう少し急に下がる。
    （1/3 オクターブの帯で測ると、どの高さも同じ強さの雑音〔ピンクノイズ〕が平らになる。音楽はそれより少し右下がり）"""
    f = np.array(THIRD_OCT)
    octs = np.log2(f / 1000.0)
    mid_tilt = {"warm": -2.3, "bright": -1.7}.get(style, -2.0)
    top_tilt = {"warm": -5.5, "bright": -3.5}.get(style, -4.5)
    t = mid_tilt * np.clip(octs, np.log2(0.09), 2.0)                # 90 Hz〜4 kHz
    t += top_tilt * np.clip(octs - 2.0, 0, None)                    # 4 kHz より上
    t -= np.clip(np.log2(50.0 / f), 0, None) * 9.0                  # 50 Hz より下は落とす
    if style == "warm":
        t += 0.8 * np.exp(-0.5 * ((octs - np.log2(0.2)) / 0.9) ** 2)    # 200 Hz あたりを少し厚く
    if style == "bright":
        t += 1.0 * np.exp(-0.5 * ((octs - np.log2(9.0)) / 0.8) ** 2)    # 9 kHz あたりの空気感
    if style == "punch":
        t += 1.2 * np.exp(-0.5 * ((octs - np.log2(0.075)) / 0.7) ** 2)  # 75 Hz あたりの押し
        t += 0.6 * np.exp(-0.5 * ((octs - np.log2(3.0)) / 0.7) ** 2)    # 3 kHz あたりの芯
    return t


def _norm(spec: np.ndarray) -> np.ndarray:
    f = np.array(THIRD_OCT)
    return spec - spec[(f >= 200) & (f <= 2000)].mean()              # 中音の量を 0 にそろえる（形だけを比べる）


def learned(slug: str | None = None) -> dict | None:
    """市販曲（SSD の「既存楽曲」）から学んだ目標。その組が影響を受けた人の曲が 5 曲以上あればそれを、無ければ全体を使う"""
    if not TARGETS.exists():
        return None
    t = read_json(TARGETS)
    for key in ((f"act:{slug}",) if slug else ()) + ("all",):
        v = t.get(key)
        if v and v.get("n", 0) >= (5 if key != "all" else 12):
            return {"mean": np.array(v["mean"]), "lo": np.array(v["lo"]), "hi": np.array(v["hi"]), "n": v["n"],
                    "who": "この組が影響を受けた人の曲" if key != "all" else "手元の市販曲"}
    return None


def target_curve(style: str, ref_spec: np.ndarray | None = None, slug: str | None = None) -> tuple[np.ndarray, np.ndarray, str]:
    """目標の音のバランスと、許せる幅（この幅の中は「その曲の個性」として動かさない）。戻り値：(目標, 幅 dB, 何を目標にしたかの説明)"""
    f = np.array(THIRD_OCT)
    if style == "ref" and ref_spec is not None:
        return _norm(ref_spec), np.full(len(f), 1.0), "参考曲"
    base = builtin_curve("auto")
    lt = learned(slug) if style != "light" else None
    if lt is not None:
        tgt = lt["mean"] + (builtin_curve(style) - base)             # 学んだ形に、味付けの差だけ足す
        tol = np.clip((lt["hi"] - lt["lo"]) / 2, 1.0, 4.0)           # 市販曲のばらつきの半分
        return _norm(tgt), tol, f"{lt['who']} {lt['n']} 曲の平均"
    tol = np.where((f < 80) | (f > 8000), 3.0, 2.0)                  # 組み込みの目標は控えめに使う（端はとくに）
    return _norm(builtin_curve(style)), tol, "組み込みの目標（市販のポップスの平均に近い形）"


def eq_plan(spec: np.ndarray, target: np.ndarray, strength: str, tol: np.ndarray | None = None) -> np.ndarray:
    """EQ で動かす量（1/3 オクターブごとの dB）。目標との差のうち、許せる幅からはみ出た分を主に埋める。
    差はなめらかにし（細い山谷は追わない）、強さの分だけ埋め、動かす幅を超えないようにする"""
    f = np.array(THIRD_OCT)
    core = (f >= 200) & (f <= 2000)
    diff = _norm(target) - _norm(spec)
    if tol is not None:
        diff = np.sign(diff) * np.clip(np.abs(diff) - tol, 0, None) + 0.25 * np.clip(diff, -tol, tol)
    k = np.array([0.15, 0.2, 0.3, 0.2, 0.15])             # 前後 2/3 オクターブでならす
    sm = np.convolve(np.pad(diff, 2, mode="edge"), k, mode="valid")
    gain = np.clip(sm * EQ_AMOUNT[strength], -EQ_LIMIT[strength], EQ_LIMIT[strength])
    gain[f < 40] = np.minimum(gain[f < 40], 0.0)          # 聞こえにくい超低音は持ち上げない
    gain[f > 12000] = np.minimum(gain[f > 12000], 1.5)    # いちばん上は上げすぎない（シャリつきが増える）
    return gain - gain[core].mean() * 0.5                 # 全体の音量が動きすぎないように


def apply_eq(x: np.ndarray, gain_db: np.ndarray) -> np.ndarray:
    """位相を乱さない EQ（直線位相の FIR）"""
    from scipy.signal import firwin2, oaconvolve
    if np.max(np.abs(gain_db)) < 0.15:
        return x
    freqs = np.concatenate([[0.0], THIRD_OCT, [SR / 2]])
    gains = np.concatenate([[gain_db[0]], gain_db, [gain_db[-1]]])
    n = 8191
    h = firwin2(n, freqs, 10 ** (gains / 20), fs=SR)
    return np.stack([oaconvolve(x[:, ch], h, mode="same") for ch in range(x.shape[1])], axis=1)


def _follow(level_db: np.ndarray, attack_ms: float, release_ms: float, rate: float) -> np.ndarray:
    """音量の動きを追う（上がるときは attack、下がるときは release の速さ）"""
    ca, cr = np.exp(-1.0 / (attack_ms / 1000 * rate)), np.exp(-1.0 / (release_ms / 1000 * rate))
    out = np.empty_like(level_db)
    e = float(level_db[0])
    for i, v in enumerate(level_db.tolist()):
        e = ca * e + (1 - ca) * v if v > e else cr * e + (1 - cr) * v
        out[i] = e
    return out


def _level_blocks(sig: np.ndarray, hop: int) -> np.ndarray:
    """短い区間ごとの音量（dB。左右の大きいほう）"""
    n = len(sig) // hop
    if n == 0:
        return np.array([-120.0])
    p = (sig[: n * hop] ** 2).reshape(n, hop, -1).mean(axis=1).max(axis=1)
    return 10 * np.log10(np.maximum(p, 1e-12))


def compress(sig: np.ndarray, threshold_db: float, ratio: float, attack_ms: float, release_ms: float,
             knee_db: float = 6.0, max_gr_db: float = 6.0) -> tuple[np.ndarray, float]:
    """しきい値を超えた分だけ押さえる（左右は同じ量）。戻り値：(押さえたあとの波形, 押さえた量の平均 dB)"""
    hop = 32
    lv = _follow(_level_blocks(sig, hop), attack_ms, release_ms, SR / hop)
    over = lv - threshold_db
    gr = np.where(over <= -knee_db / 2, 0.0,
                  np.where(over >= knee_db / 2, over * (1 - 1 / ratio),
                           (over + knee_db / 2) ** 2 / (2 * knee_db) * (1 - 1 / ratio)))
    gr = np.minimum(gr, max_gr_db)
    g = np.interp(np.arange(len(sig)), np.arange(len(gr)) * hop + hop / 2, 10 ** (-gr / 20))
    return sig * g[:, None], float(gr.mean())


def deess(x: np.ndarray, strength: str, lo: float = 5000.0, hi: float = 11000.0) -> tuple[np.ndarray, float]:
    """耳に痛い高音が、その曲のふだんの揺れを超えて強く出た瞬間だけ下げる。戻り値：(波形, いちばん下げた量 dB)。
    シンバルやハイハットが鳴り続ける曲では「ふだんの揺れ」が大きくなるので、しきい値も上がる（楽器の高音は鈍らせない）"""
    band = _bandpass(x, lo, hi)
    rest = x - band
    hop = 128                                             # 約 3 ms ごとに見る（高音もそれ以外も同じ速さで追う）
    b = _follow(_level_blocks(band, hop), 3.0, 60.0, SR / hop)
    full = _follow(_level_blocks(x, hop), 3.0, 60.0, SR / hop)
    rel = b - full                                        # 高音の割合（dB）
    loud = full > (np.percentile(full, 60) - 12)          # 静かな所は数えない
    ref = rel[loud] if loud.any() else rel
    base = float(np.median(ref))
    spread = float(1.4826 * np.median(np.abs(ref - base)))   # ふだんの揺れの大きさ
    margin = max({"low": 5.0, "mid": 4.0, "high": 3.0}[strength], 2.5 * spread)
    depth = {"low": 3.0, "mid": 5.0, "high": 7.0}[strength]
    gr = np.minimum(np.clip(rel - (base + margin), 0, None) * 0.8, depth)
    gr[~loud] = 0.0
    if gr.max() < 1.0:
        return x, 0.0
    g = np.interp(np.arange(len(x)), np.arange(len(gr)) * hop + hop / 2, 10 ** (-gr / 20))
    return rest + band * g[:, None], float(gr.max())


def mono_bass(x: np.ndarray, fc: float = 120.0) -> np.ndarray:
    """fc より下を真ん中に寄せる（左右の差の成分から低音を抜く）"""
    mid, side = x.mean(axis=1), (x[:, 0] - x[:, 1]) / 2
    side = _highpass(side, fc)
    return np.stack([mid + side, mid - side], axis=1)


def three_band(x: np.ndarray, strength: str) -> tuple[np.ndarray, dict]:
    """低・中・高の 3 つの帯で、飛び出した瞬間だけ軽く押さえる"""
    low = _lowpass(x, 150.0)
    high = x - _lowpass(x, 4000.0)
    mid = x - low - high
    ratio = {"low": 1.3, "mid": 1.6, "high": 2.0}[strength]
    out, gr = np.zeros_like(x), {}
    for name, sig, att, rel in (("低音", low, 30.0, 220.0), ("中音", mid, 20.0, 160.0), ("高音", high, 8.0, 100.0)):
        lv = _level_blocks(sig, 32)
        thr = float(np.percentile(lv[lv > lv.max() - 40], 80))   # その帯の「大きい瞬間」だけに効かせる
        y, g = compress(sig, thr, ratio, att, rel, max_gr_db=4.0)
        out += y
        gr[name] = round(g, 2)
    return out, gr


def saturate(x: np.ndarray, drive_db: float) -> np.ndarray:
    """ごく軽い歪みで厚みを足す（2 倍に細かくしてから。折り返しの雑音を防ぐ）"""
    from scipy.signal import resample_poly
    d = 10 ** (drive_db / 20)
    up = resample_poly(x, 2, 1, axis=0)
    y = np.tanh(up * d) / np.tanh(d)
    y = resample_poly(y, 1, 2, axis=0)[: len(x)]
    return y * (np.sqrt(np.mean(x * x)) / (np.sqrt(np.mean(y * y)) + 1e-12))   # 音量は変えない


def limit(x: np.ndarray, ceiling_db: float) -> tuple[np.ndarray, float]:
    """先読みのリミッター：本当の最大値が天井を超える所だけ、先回りして音量を下げる。戻り値：(波形, いちばん下げた量 dB)"""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    from scipy.signal import resample_poly
    ceil = 10 ** (ceiling_db / 20)
    peak = np.zeros(len(x))
    block, pad = 1 << 18, 64
    for ch in range(x.shape[1]):
        for i in range(0, len(x), block):
            a = max(0, i - pad)
            seg = x[a: i + block + pad, ch]
            up = np.abs(resample_poly(seg, 4, 1))
            pk = up[: len(seg) * 4].reshape(len(seg), 4).max(axis=1)[i - a: i - a + block]
            peak[i: i + len(pk)] = np.maximum(peak[i: i + len(pk)], pk)
    need = np.minimum(1.0, ceil / np.maximum(peak, 1e-9))
    if need.min() >= 1.0:
        return x, 0.0
    w = int(0.005 * SR) | 1                                # 5 ms 先まで見る
    g = uniform_filter1d(minimum_filter1d(need, w, mode="nearest"), w, mode="nearest")   # 必ず need 以下になる
    hop = 32                                               # 戻りはゆっくり（120 ms）。ここも need 以下のまま
    n = len(g) // hop
    blk = g[: n * hop].reshape(n, hop).min(axis=1) if n else g[:1]
    cr = np.exp(-1.0 / (0.12 * SR / hop))
    rel = np.empty_like(blk)
    e = 1.0
    for i, v in enumerate(blk.tolist()):
        e = v if v < e else cr * e + (1 - cr) * v
        rel[i] = min(e, v)
    slow = np.interp(np.arange(len(g)), np.arange(len(rel)) * hop, np.minimum(rel, np.roll(rel, -1)))
    g = np.minimum(g, slow)
    return x * g[:, None], float(-20 * np.log10(max(g.min(), 1e-9)))


def tidy_edges(x: np.ndarray, info: dict) -> tuple[np.ndarray, list[str]]:
    """前後の無音を整える。途中で切れた終わりにはフェードを付ける"""
    notes = []
    a, b = edges(x)
    start = max(0, a - int(0.02 * SR))                     # 音の 0.02 秒前から
    end = min(len(x), b + int(0.6 * SR))                   # 音が消えてから 0.6 秒まで
    if start > int(0.05 * SR):
        notes.append(f"始まりの無音 {start / SR:.2f} 秒を詰めた")
    if len(x) - end > int(0.3 * SR):
        notes.append(f"終わりの無音 {(len(x) - end) / SR:.1f} 秒を詰めた")
    y = x[start:end].copy()
    n_in = min(len(y), int(0.005 * SR))
    y[:n_in] *= np.linspace(0, 1, n_in)[:, None]
    if info.get("abrupt_end"):
        n_out = min(len(y), int(0.5 * SR))
        notes.append("終わりが途中で切れているので 0.5 秒のフェードを付けた（Suno で作り直すのが確実）")
    else:
        n_out = min(len(y), int(0.03 * SR))
    y[-n_out:] *= (np.cos(np.linspace(0, np.pi, n_out)) * 0.5 + 0.5)[:, None]
    return y, notes


# ---------------------------------------------------------------------------
# 本体
def process(x: np.ndarray, style: str = "auto", strength: str = "mid", ref_spec: np.ndarray | None = None,
            slug: str | None = None) -> tuple[np.ndarray, list[dict], dict]:
    """波形を仕上げる。戻り値：(仕上げた波形, した手当ての一覧, 仕上げ前の測定値)"""
    before = analyze(x)
    moves: list[dict] = []

    def did(what: str, why: str, amount: str = "") -> None:
        moves.append({"what": what, "why": why, "amount": amount})

    # 1 掃除
    y, notes = tidy_edges(x, before)
    for n in notes:
        did("前後を整えた", n)
    y = _highpass(y - y.mean(axis=0), 25.0)
    did("超低音の掃除", "25 Hz より下（聞こえないが音量を食う成分）と直流のずれを取った")

    if style != "light":
        # 2 音のバランス
        spec = spectrum(y)
        tgt, tol, tgt_name = target_curve(style, ref_spec, slug)
        gain = eq_plan(spec, tgt, strength, tol)
        if np.max(np.abs(gain)) >= 0.3:
            f = np.array(THIRD_OCT)
            parts = []
            for name, lo, hi in BANDS:
                g = float(gain[(f >= lo) & (f < hi)].mean())
                if abs(g) >= 0.4:
                    parts.append(f"{name} {g:+.1f} dB")
            y = apply_eq(y, gain)
            did("音のバランス（EQ）", f"目標＝{tgt_name}（味付け：{STYLES[style]}）。許せる幅からはみ出た所を中心に寄せた",
                "・".join(parts) or "ごくわずか")
        else:
            did("音のバランス（EQ）", f"目標＝{tgt_name}。すでに近いので動かしていない")
        # 3 耳に痛い高音
        y, gr = deess(y, strength)
        if gr:
            did("耳に痛い高音", "5〜11 kHz が強く出た瞬間だけ下げた（サ行の刺さり・シャリつき）", f"最大 {gr:.1f} dB")
        # 4 低音の整理
        if before["low_side_db"] > -30.0:
            y = mono_bass(y)
            did("低音を真ん中に", "120 Hz より下が左右に散っていたので、真ん中に寄せた（スピーカーやクラブでぼやけない）",
                f"左右に散った低音 {before['low_side_db']:+.0f} dB")
        if before["correlation"] < 0.1 or before["width_db"] > -3.0:
            mid, side = y.mean(axis=1), (y[:, 0] - y[:, 1]) / 2 * 10 ** (-2.0 / 20)
            y = np.stack([mid + side, mid - side], axis=1)
            did("広がりを少し抑えた", "左右の差が大きすぎて、スマホのスピーカー（モノラル）で音が消えやすいため", "-2 dB")
        # 5 強弱
        crest = analyze_crest(y)
        if crest >= 11.0:
            y, gr3 = three_band(y, strength)
            if any(v >= 0.1 for v in gr3.values()):
                did("強弱をならした", "飛び出した瞬間だけ、低・中・高の帯ごとに軽く押さえた",
                    "・".join(f"{k} {v:.1f} dB" for k, v in gr3.items()))
        else:
            did("強弱はそのまま", f"すでに強くそろっている（最大値と平均の差 {crest:.1f} dB）ので、これ以上は押さえない")
        if style == "punch":
            y = saturate(y, {"low": 1.5, "mid": 3.0, "high": 4.5}[strength])
            did("厚みを足した", "ごく軽い歪みで倍音を足した（太く・力強く）")

    # 6 音量と天井
    cur = lufs(y)
    y = y * 10 ** ((TARGET_LUFS - cur) / 20)
    did("音量をそろえた", f"{cur:.1f} LUFS → {TARGET_LUFS:.0f} LUFS（配信サービスの基準）", f"{TARGET_LUFS - cur:+.1f} dB")
    ceiling = CEILING_DBTP - 0.1
    for _ in range(4):
        z, gr = limit(y, ceiling)
        tp = true_peak(z)
        if tp <= CEILING_DBTP + 0.05:
            break
        ceiling -= max(0.3, tp - CEILING_DBTP)
    if gr >= 0.1:
        did("天井で止めた", f"本当の最大値が {CEILING_DBTP:.0f} dBTP を超える所だけ、先回りして下げた", f"最大 {gr:.1f} dB")
    drift = TARGET_LUFS - lufs(z)                          # リミッターで下がった分を少し戻す（天井は守る）
    if 0.15 < drift < 1.5 and gr < 3.0:
        z2, _ = limit(z * 10 ** (drift / 20), ceiling)
        if true_peak(z2) <= CEILING_DBTP + 0.05:
            z = z2
    return z, moves, before


def analyze_crest(x: np.ndarray) -> float:
    return float(20 * np.log10((np.max(np.abs(x)) + 1e-12) / (np.sqrt(np.mean(x * x)) + 1e-12)))


def master(src: Path, dst: Path, style: str = "auto", strength: str = "mid", ref: Path | None = None, previews: bool = True,
           slug: str | None = None) -> dict:
    """1 曲を仕上げて書き出す。master_track.master と同じ形の記録（ok・before・after）に、した手当てを足して返す"""
    why = need_tools()
    if why:
        raise RuntimeError(why)
    style = style if style in STYLES else "auto"
    strength = strength if strength in STRENGTHS else "mid"
    step(f"1/5 読み込んで測っています：{Path(src).name}")
    x = load(Path(src))
    ref_spec = None
    if style == "ref":
        if ref and Path(ref).exists():
            step(f"   参考曲の音のバランスを測っています：{Path(ref).name}")
            ref_spec = spectrum(load(Path(ref)))
        else:
            style = "auto"
    step(f"2/5 仕上げています（味付け：{STYLES[style]}・強さ：{STRENGTHS[strength]}）")
    y, moves, before = process(x, style, strength, ref_spec, slug)
    for m in moves:
        print(f"   ・{m['what']}：{m['why']}" + (f"（{m['amount']}）" if m["amount"] else ""))
    step("3/5 44.1 kHz / 24 bit の WAV に書き出しています")
    save(y, Path(dst))
    step("4/5 書き出したファイルを測り直しています")
    after = analyze(y)
    try:   # ffmpeg でも測る（配信サービスと同じ測り方）
        from select_takes import ebur128
        ff = ebur128(Path(dst))
    except Exception:  # noqa: BLE001
        ff = {}
    if ff.get("lufs") is not None:
        after["lufs_ffmpeg"], after["true_peak_ffmpeg"], after["lra"] = ff.get("lufs"), ff.get("true_peak_db"), ff.get("lra")
    l = after.get("lufs_ffmpeg", after["lufs"])
    tp = max(after["true_peak_db"], after.get("true_peak_ffmpeg", -99.0))
    ok = abs(l - TARGET_LUFS) <= 1.0 and tp <= CEILING_DBTP + 0.3
    rep = {"version": VERSION, "at": datetime.now().isoformat(timespec="seconds"), "source": str(src), "master": str(dst),
           "style": style, "strength": strength, "ref": str(ref) if ref_spec is not None else "",
           "before": before, "after": {**after, "lufs": l, "true_peak_db": tp},
           "target": {"lufs": TARGET_LUFS, "true_peak_db": CEILING_DBTP}, "moves": moves, "ok": bool(ok)}
    if previews:
        step("5/5 聴き比べ用の小さいファイルを作っています")
        a, b = edges(x)
        raw = x[max(0, a - int(0.02 * SR)): min(len(x), b + int(0.6 * SR))]
        g = 10 ** ((lufs(y) - lufs(raw)) / 20)             # 仕上げ前を、仕上げ後と同じ音量にそろえる
        pk = float(np.max(np.abs(raw * g)))
        if pk > 0.98:                                      # そろえると割れるなら、両方を同じだけ下げる
            g, y_prev = g * 0.98 / pk, y * 0.98 / pk
        else:
            y_prev = y
        rep["previews"] = {"after": preview(y_prev, Path(dst).with_name("preview_after.m4a")),
                           "before": preview(raw * g, Path(dst).with_name("preview_before.m4a"))}
    print(f"   {'○' if ok else '✕'} 仕上がり {l} LUFS / ピーク {tp} dBTP"
          + ("" if ok else "（目標から外れました。元の音源の音割れ・極端な音量差を確かめてください）"))
    return rep


# ---------------------------------------------------------------------------
# 既定の味付け（全体・組ごと）と、曲ごとの仕上げ直し
def defaults(slug: str | None = None) -> dict:
    s = read_json(SETTINGS) if SETTINGS.exists() else {}
    d = {"style": "auto", "strength": "mid", **(s.get("default") or {})}
    if slug:
        d.update((s.get("artists") or {}).get(slug) or {})
    return d


def set_default(slug: str, style: str, strength: str) -> None:
    s = read_json(SETTINGS) if SETTINGS.exists() else {}
    v = {"style": style if style in STYLES and style != "ref" else "auto", "strength": strength if strength in STRENGTHS else "mid"}
    if slug == "all":
        s["default"] = v
    else:
        s.setdefault("artists", {})[slug] = v
    write_json(SETTINGS, s)


def report_of(song: dict) -> dict:
    p = OUT / "masters" / song["key"] / "master.report.json"
    return read_json(p) if p.exists() else {}


def source_of(song: dict) -> Path | None:
    """その曲の、仕上げる前の音源（選んだテイク）"""
    tdir = OUT / "takes" / song["key"]
    sel = tdir / "selection.json"
    if not sel.exists():
        return None
    name = (read_json(sel).get("decision") or {}).get("selected")
    return (tdir / name) if name and (tdir / name).exists() else None


def outdated(song: dict) -> bool:
    """古い作り方（音量だけ）の仕上げのままか"""
    rep = report_of(song)
    return bool(rep) and int(rep.get("version", 1)) < VERSION


def remaster(song: dict, style: str | None = None, strength: str | None = None, ref: Path | None = None) -> dict:
    """1 曲を仕上げ直して、選んだテイクの記録（selection.json）も新しくする"""
    src = source_of(song)
    if src is None:
        raise RuntimeError("仕上げる前の音源（選んだテイク）が見つかりません。曲を取り込み直してください")
    d = defaults(song.get("slug"))
    dst = OUT / "masters" / song["key"] / "master.wav"
    rep = master(src, dst, style or d["style"], strength or d["strength"], ref, slug=song.get("slug"))
    write_json(dst.with_suffix(".report.json"), rep)
    sel_p = OUT / "takes" / song["key"] / "selection.json"
    sel = read_json(sel_p)
    sel["master"] = {"path": str(dst), "ok": rep["ok"], "lufs": rep["after"].get("lufs"), "true_peak_db": rep["after"].get("true_peak_db"),
                     "style": rep["style"], "strength": rep["strength"], "version": VERSION}
    write_json(sel_p, sel)
    return rep


def learn(limit: int = 0, quiet: bool = False) -> dict:
    """市販曲（SSD の「既存楽曲」）の音のバランスを測って、目標にする。1 曲 数秒。測った曲は覚えておき、次は新しい曲だけ測る"""
    import hashlib
    import reference_db
    files = [f for d in reference_db.audio_dirs() for f in sorted(d.rglob("*"))
             if f.is_file() and f.suffix.lower() in reference_db.AUDIO_TAKE and not f.name.startswith(".")
             and not any(x in str(f) for x in reference_db.DERIVED_DIRS)]
    if not files:
        return {"n": 0, "why": "「既存楽曲」の音源が見つかりません（SSD をつないでください）"}
    SPEC_CACHE.mkdir(parents=True, exist_ok=True)
    try:   # 組ごと：その組が影響を受けた人の曲（決定 91）
        import influences
        picks = {slug: influences.names_for(slug) for slug in influences.load()}
        akey = reference_db.akey
    except Exception:  # noqa: BLE001
        picks, akey = {}, reference_db.norm
    rows, done = [], 0
    for i, f in enumerate(files, 1):
        st = f.stat()
        c = SPEC_CACHE / (hashlib.sha1(f"{f}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:20] + ".json")
        if c.exists():
            d = read_json(c)
        elif limit and done >= limit:
            continue
        else:
            if not quiet:
                print(f"【進み具合】{i}/{len(files)} 曲目：{f.name}")
            try:
                d = {"spec": [round(float(v), 2) for v in _norm(spectrum(load(f)))]}
            except Exception as e:  # noqa: BLE001
                d = {"error": str(e)[:120]}
            try:
                d["who"] = akey(reference_db._who(f, next(t for t in reference_db.audio_dirs() if f.is_relative_to(t)))[1])
            except Exception:  # noqa: BLE001
                d["who"] = ""
            write_json(c, d)
            done += 1
        if d.get("spec"):
            rows.append((d.get("who", ""), np.array(d["spec"])))
    out = {}

    def put(key, specs):
        a = np.stack(specs)
        out[key] = {"n": len(specs), "mean": [round(float(v), 2) for v in a.mean(axis=0)],
                    "lo": [round(float(v), 2) for v in np.percentile(a, 25, axis=0)],
                    "hi": [round(float(v), 2) for v in np.percentile(a, 75, axis=0)]}
    if rows:
        put("all", [s for _, s in rows])
    for slug, names in picks.items():
        mine = [s for w, s in rows if w and w in names]
        if len(mine) >= 5:
            put(f"act:{slug}", mine)
    out["at"] = datetime.now().isoformat(timespec="seconds")
    write_json(TARGETS, out)
    return {"n": len(rows), "new": done, "acts": [k[4:] for k in out if k.startswith("act:")], "left": len(files) - len(rows)}


def learn_status() -> dict:
    t = read_json(TARGETS) if TARGETS.exists() else {}
    return {"n": (t.get("all") or {}).get("n", 0), "acts": [k[4:] for k in t if k.startswith("act:")], "at": t.get("at", "")}


def main() -> None:
    ap = argparse.ArgumentParser(description="仕上げ（マスタリング）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("song", help="1 曲を仕上げ直す")
    s.add_argument("id")
    s.add_argument("--style", choices=list(STYLES))
    s.add_argument("--strength", choices=list(STRENGTHS))
    s.add_argument("--ref", type=Path)
    s.add_argument("--save-default", action="store_true", help="この味付けを、その組の既定にする")
    f = sub.add_parser("file", help="ファイルを直接仕上げる")
    f.add_argument("src", type=Path)
    f.add_argument("--out", type=Path)
    f.add_argument("--style", choices=list(STYLES), default="auto")
    f.add_argument("--strength", choices=list(STRENGTHS), default="mid")
    f.add_argument("--ref", type=Path)
    a = sub.add_parser("analyze", help="測るだけ")
    a.add_argument("src", type=Path)
    d = sub.add_parser("default", help="既定の味付け")
    d.add_argument("slug")
    d.add_argument("--style", choices=[k for k in STYLES if k != "ref"], required=True)
    d.add_argument("--strength", choices=list(STRENGTHS), default="mid")
    le = sub.add_parser("learn", help="市販曲（既存楽曲）の音のバランスを測って目標にする")
    le.add_argument("--limit", type=int, default=0, help="1 回に新しく測る曲の数（0 は全部）")
    le.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        if args.cmd == "learn":
            r = learn(args.limit, args.quiet)
            if not r["n"]:
                print(f"【結果】学べませんでした：{r.get('why', '測れる曲がありません')}")
                sys.exit(0 if args.quiet else 2)   # 定期実行では、SSD が無いだけなら失敗にしない
            print(f"【結果】市販曲 {r['n']} 曲の音のバランスを目標にしました（新しく測った {r['new']} 曲"
                  + (f"・組ごとの目標：{'、'.join(r['acts'])}" if r["acts"] else "") + (f"・残り {r['left']} 曲" if r["left"] else "") + "）")
            return
        if args.cmd == "analyze":
            print(json.dumps({k: v for k, v in analyze(load(args.src)).items() if k != "third_oct"}, ensure_ascii=False, indent=1))
            return
        if args.cmd == "default":
            set_default(args.slug, args.style, args.strength)
            print(f"【結果】既定の味付けを「{STYLES[args.style]}・{STRENGTHS[args.strength]}」にしました（{args.slug}）")
            return
        if args.cmd == "file":
            dst = args.out or args.src.with_name(args.src.stem + "_master.wav")
            rep = master(args.src, dst, args.style, args.strength, args.ref)
            write_json(dst.with_suffix(".report.json"), rep)
            print(f"【結果】仕上げました：{dst.name}（{rep['after']['lufs']} LUFS / {rep['after']['true_peak_db']} dBTP）")
            sys.exit(0 if rep["ok"] else 1)
        import _songs
        song = _songs.by_id(args.id)
        if song is None:
            print(f"【結果】曲が見つかりません：{args.id}")
            sys.exit(1)
        if _songs.state(song).get("status") in ("registered", "live"):
            print("【結果】この曲はもう DistroKid に登録済みなので、仕上げ直しません（登録した音と変わってしまうため）")
            sys.exit(2)
        rep = remaster(song, args.style, args.strength, args.ref)
        if args.save_default:
            set_default(song["slug"], rep["style"], rep["strength"])
        import publish
        publish.after_remaster(song)   # 公開セット・下書きを新しい音に合わせ、聴いて確かめる印を外す
        print(f"【結果】{song['artist']}「{song['title']}」を仕上げ直しました（{STYLES[rep['style']]}・{STRENGTHS[rep['strength']]}）。"
              "聴いて確かめてください")
        sys.exit(0 if rep["ok"] else 1)
    except RuntimeError as e:
        print(f"【結果】仕上げられませんでした：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
