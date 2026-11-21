"""仕上げ（決定 168）と、聴いて確かめる関門（決定 169）・長さの決まり（決定 170）の試験。合成した短い音で確かめる"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:
    import numpy as np
    import scipy  # noqa: F401
    import soundfile  # noqa: F401
    HAVE = bool(shutil.which("ffmpeg"))
except ImportError:
    HAVE = False


def noise(seconds: float, psd_slope_db_oct: float, seed: int = 1, wide: float = 0.3):
    """音楽のまねの雑音（傾きを決められる）。左右の差つき"""
    import mastering as M
    n = int(M.SR * seconds)
    f = np.fft.rfftfreq(n, 1 / M.SR)

    def col(s):
        w = np.random.default_rng(s).standard_normal(n)
        y = np.fft.irfft(np.fft.rfft(w) * (np.maximum(f, 30) / 1000.0) ** (psd_slope_db_oct / 6.02), n)
        return y / np.abs(y).max()
    m, s = col(seed), col(seed + 1) * wide
    t = np.arange(n) / M.SR
    x = np.stack([m + s, m - s], 1) * (0.6 + 0.4 * np.abs(np.sin(2 * np.pi * t)))[:, None]
    return x / np.abs(x).max() * 0.9


@unittest.skipUnless(HAVE, "numpy・scipy・soundfile・ffmpeg が要る")
class Parts(unittest.TestCase):
    def test_lufs_matches_ffmpeg(self):
        import mastering as M
        from select_takes import ebur128
        x = noise(8, -5.0) * 0.3
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.wav"
            M.save(x, p)
            self.assertAlmostEqual(M.lufs(x), ebur128(p)["lufs"], delta=0.3)

    def test_limiter_keeps_ceiling(self):
        import mastering as M
        x = noise(6, -5.0)
        for push in (6, 12):
            z, gr = M.limit(x * 10 ** (push / 20), -1.1)
            self.assertLessEqual(M.true_peak(z), -1.0)
            self.assertGreater(gr, push - 1.5)
        z, gr = M.limit(x * 0.1, -1.1)   # 小さい音には何もしない
        self.assertEqual(gr, 0.0)

    def test_eq_direction_and_limit(self):
        import mastering as M
        f = np.array(M.THIRD_OCT)
        hi = f >= 5000
        tgt, tol, _ = M.target_curve("auto")
        bright = M.eq_plan(M.spectrum(noise(8, -3.0)), tgt, "mid", tol)   # 明るすぎ → 高音を下げる
        dull = M.eq_plan(M.spectrum(noise(8, -7.0)), tgt, "mid", tol)     # こもり → 高音を上げる
        near = M.eq_plan(M.spectrum(noise(8, -5.0)), tgt, "mid", tol)     # 目標に近い → ほぼ動かさない
        self.assertLess(bright[hi].mean(), -1.5)
        self.assertGreater(dull[(f >= 2000) & (f <= 8000)].mean(), 1.0)
        self.assertLess(np.abs(near[(f >= 100) & (f <= 8000)]).max(), 1.5)
        for g in (bright, dull):
            self.assertLessEqual(np.abs(g).max(), M.EQ_LIMIT["mid"] + 1.0)
        self.assertLess(np.abs(M.eq_plan(M.spectrum(noise(8, -3.0)), tgt, "low", tol)).max(), np.abs(bright).max())

    def test_eq_is_applied_as_planned(self):
        import mastering as M
        f = np.array(M.THIRD_OCT)
        x = noise(8, -5.0)
        gain = np.where(f >= 4000, -3.0, 0.0) + np.where(f <= 100, 2.0, 0.0)
        real = M.spectrum(M.apply_eq(x, gain)) - M.spectrum(x)
        self.assertLess(np.abs(real - gain)[(f > 40) & (f < 12000) & ~((f > 80) & (f < 160)) & ~((f > 2500) & (f < 5000))].max(), 0.6)

    def test_deess_only_on_bursts(self):
        import mastering as M
        from scipy.signal import butter, sosfiltfilt
        x = noise(10, -5.0)
        _, gr = M.deess(x, "mid")
        self.assertLess(gr, 1.5)                                         # 一定の音にはほぼ効かない
        t = np.arange(len(x)) / M.SR
        on = np.sin(2 * np.pi * 0.5 * t) > 0.97
        burst = sosfiltfilt(butter(4, [6000, 9000], "bandpass", fs=M.SR, output="sos"),
                            np.random.default_rng(9).standard_normal(len(x))) * (on * 0.25)
        y, gr = M.deess(x + burst[:, None], "mid")
        self.assertGreater(gr, 3.0)
        b0, b1 = M._bandpass(x + burst[:, None], 5000, 11000), M._bandpass(y, 5000, 11000)
        self.assertLess(20 * np.log10(b1[on].std() / b0[on].std()), -2.5)       # 刺さる瞬間は下がる
        self.assertGreater(20 * np.log10(b1[~on].std() / b0[~on].std()), -0.5)  # ほかはそのまま

    def test_mono_bass_keeps_centre(self):
        import mastering as M
        x = noise(6, -5.0, wide=0.0)
        t = np.arange(len(x)) / M.SR
        x[:, 0] += 0.2 * np.sin(2 * np.pi * 70 * t)
        x[:, 1] -= 0.2 * np.sin(2 * np.pi * 70 * t)      # 左右で逆向きの低音
        y = M.mono_bass(x)
        self.assertLess(M.analyze(y)["low_side_db"], M.analyze(x)["low_side_db"] - 15)
        np.testing.assert_allclose(y.mean(axis=1), x.mean(axis=1), atol=1e-9)   # 真ん中の成分は変えない

    def test_edges(self):
        import mastering as M
        x = np.concatenate([np.zeros((M.SR, 2)), noise(4, -5.0), np.zeros((3 * M.SR, 2))])
        y, notes = M.tidy_edges(x, M.analyze(x))
        self.assertLess(len(y) / M.SR, 5.2)
        self.assertEqual(len(notes), 3)   # 前・後ろの無音と、途中で切れた終わり
        self.assertAlmostEqual(float(np.abs(y[0]).max()), 0.0, places=6)
        self.assertAlmostEqual(float(np.abs(y[-1]).max()), 0.0, places=4)


@unittest.skipUnless(HAVE, "numpy・scipy・soundfile・ffmpeg が要る")
class Whole(unittest.TestCase):
    def test_master_file(self):
        import mastering as M
        import soundfile as sf
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.wav", Path(td) / "master.wav"
            sf.write(src, np.concatenate([np.zeros((M.SR // 2, 2)), noise(12, -3.0)]), M.SR, subtype="PCM_16")
            for style in ("auto", "warm", "light"):
                rep = M.master(src, dst, style, "mid")
                self.assertTrue(rep["ok"], style)
                self.assertAlmostEqual(rep["after"]["lufs"], -14.0, delta=0.7)
                self.assertLessEqual(rep["after"]["true_peak_db"], -0.9)
                self.assertEqual(rep["version"], M.VERSION)
                info = sf.info(dst)
                self.assertEqual((info.samplerate, info.subtype, info.channels), (44100, "PCM_24", 2))
                self.assertTrue((Path(td) / "preview_after.m4a").exists() and (Path(td) / "preview_before.m4a").exists())
                whats = [m["what"] for m in rep["moves"]]
                self.assertIn("音量をそろえた", whats)
                self.assertEqual("音のバランス（EQ）" in whats, style != "light")   # そのまま＝掃除と音量だけ

    def test_learned_target_is_used(self):
        import mastering as M
        f = np.array(M.THIRD_OCT)
        with tempfile.TemporaryDirectory() as td:
            old = M.TARGETS
            M.TARGETS = Path(td) / "targets.json"
            try:
                base = M._norm(M.builtin_curve("auto")) + np.where(f >= 4000, 3.0, 0.0)   # 高音が多い市販曲ばかりだったとする
                M.write_json(M.TARGETS, {"all": {"n": 30, "mean": list(base), "lo": list(base - 1), "hi": list(base + 1)}})
                tgt, tol, name = M.target_curve("auto")
                self.assertIn("30 曲", name)
                self.assertGreater(tgt[f >= 4000].mean(), M._norm(M.builtin_curve("auto"))[f >= 4000].mean() + 2.0)
                M.write_json(M.TARGETS, {"all": {"n": 3, "mean": list(base), "lo": list(base), "hi": list(base)}})
                self.assertIn("組み込み", M.target_curve("auto")[2])   # 曲が少ないうちは使わない
            finally:
                M.TARGETS = old


class Rules(unittest.TestCase):
    def test_length_is_3_to_5_minutes(self):
        import _common
        spec = _common.length_spec({"sound": {"drive_spec": {"length_sec_min": 150, "length_sec_max": 210}}}, {})
        self.assertEqual((spec["length_sec_min"], spec["length_sec_max"]), (180, 300))
        for v in _common.SCENE_TYPES.values():
            self.assertEqual((v["spec"]["length_sec_min"], v["spec"]["length_sec_max"]), (180, 300))

    def test_listen_gate(self):
        import _songs
        import listen
        song = {"id": "T-1", "key": "t_key", "slug": "x", "artist": "A", "title": "B", "week": "2026-11-16", "kind": "weekly"}
        with tempfile.TemporaryDirectory() as td:
            old_state, old_out = _songs.STATE_DIR, listen.OUT
            _songs.STATE_DIR, listen.OUT = Path(td) / "state", Path(td)
            try:
                m = listen.master_path(song)
                m.parent.mkdir(parents=True)
                m.write_bytes(b"a" * 10)
                _songs.save_state(song, {"status": "ready"})
                self.assertTrue(listen.needed(song))
                listen.approve(song, "ok", ["voice", "bogus"])
                self.assertFalse(listen.needed(song))
                self.assertEqual(_songs.state(song)["listen"]["checks"], ["voice"])
                m.write_bytes(b"b" * 11)                      # 仕上げ直した（音が変わった）→ もう一度確かめる
                self.assertTrue(listen.needed(song))
                listen.approve(song)
                listen.revoke(song)
                self.assertTrue(listen.needed(song))
                _songs.save_state(song, {"status": "registered"})
                self.assertFalse(listen.needed(song))         # 登録済みの曲には求めない
            finally:
                _songs.STATE_DIR, listen.OUT = old_state, old_out


if __name__ == "__main__":
    unittest.main()
