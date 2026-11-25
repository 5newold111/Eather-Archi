"""作り物の曲（テスト用）：決まったテンポ・コード進行・ドラム・ベース・歌を、計算で作って WAV に書く。返り値は、入れた音の一覧（MIDI の代わり）"""
import numpy as np, soundfile as sf
SR = 22050
def tone(f, n, harm=(1, .5, .25, .12)):
    t = np.arange(n) / SR
    y = sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(harm))
    env = np.minimum(1, t / 0.01) * np.exp(-t * 1.5)
    return y * env
def midi_f(m): return 440 * 2 ** ((m - 69) / 12)
def song(path, bpm=120, prog=((0, 'maj'), (7, 'maj'), (9, 'min'), (5, 'maj')), bars=32, meter=4, drums=True, quiet_intro=4, stereo=True, seed=0):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm; nb = int(beat * SR); n = nb * meter * bars
    y = np.zeros(n + SR)
    tracks = {"bass": [], "keys": [], "drums": [], "vocals": []}
    for bar in range(bars):
        root, q = prog[bar % len(prog)]
        loud = 0.35 if bar < quiet_intro else (1.0 if (bar // 8) % 2 else 0.7)
        t0 = bar * meter * nb
        for iv in (0, 4 if q == 'maj' else 3, 7):
            m = 60 + root + iv
            y[t0:t0 + meter * nb] += 0.12 * loud * tone(midi_f(m), meter * nb)
            tracks["keys"].append((t0 / SR, (t0 + meter * nb) / SR, m, 80))
        for b in range(meter):
            s = t0 + b * nb
            if bar >= quiet_intro:
                y[s:s + nb] += 0.3 * loud * tone(midi_f(36 + root), nb, (1, .3))
                tracks["bass"].append((s / SR, (s + nb * 0.9) / SR, 36 + root, 90))
            if drums and bar >= quiet_intro:
                if b % 2 == 0:
                    t = np.arange(int(.12 * SR)) / SR
                    y[s:s + len(t)] += 0.9 * np.sin(2 * np.pi * (120 * np.exp(-t * 30) + 45) * t) * np.exp(-t * 28)
                    tracks["drums"].append((s / SR, s / SR + .1, 36, 110))
                else:
                    t = np.arange(int(.1 * SR)) / SR
                    y[s:s + len(t)] += 0.5 * rng.standard_normal(len(t)) * np.exp(-t * 35) + 0.3 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 30)
                    tracks["drums"].append((s / SR, s / SR + .1, 38, 100))
                for h in (0, nb // 2):
                    t = np.arange(int(.03 * SR)) / SR
                    hh = rng.standard_normal(len(t)) * np.exp(-t * 150)
                    hh = np.diff(np.concatenate([[0], hh]))
                    y[s + h:s + h + len(t)] += 0.25 * hh
                    tracks["drums"].append(((s + h) / SR, (s + h) / SR + .05, 42, 70))
        if bar >= 8:   # 歌：コードの音を 4 分で
            for b, iv in enumerate((0, 4 if q == 'maj' else 3, 7, 12)[:meter]):
                s = t0 + b * nb; m = 72 + root + iv
                y[s:s + nb] += 0.15 * tone(midi_f(m), nb, (1, .2))
                tracks["vocals"].append((s / SR, (s + nb * .8) / SR, m, 90))
    y = y / np.max(np.abs(y)) * 0.8
    out = np.stack([y, np.roll(y, 40) * 0.9], axis=1) if stereo else y
    sf.write(path, out, SR)
    return tracks
