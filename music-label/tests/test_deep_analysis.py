"""深い分析（決定 180）：コード進行・ドラムの型・楽器の出入り・楽譜・再現カード・枠ごとの受け渡し"""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import unicodedata
import unittest
import xml.dom.minidom
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import deep_analysis as D  # noqa: E402

try:
    import librosa  # noqa: F401
    import soundfile  # noqa: F401
    HAVE_AUDIO = True
except Exception:  # noqa: BLE001
    HAVE_AUDIO = False


def _var(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def _midi(tracks: list[tuple[str, int, list[tuple[int, int, int]]]], div: int = 480, tempo_us: int = 500000) -> bytes:
    """作り物の MIDI（形式 1）。tracks：[(名前, チャンネル, [(始まりの刻み, 長さ, 高さ)])]。2 つ目の音からは「前と同じ種類」の省略書きを使う"""
    chunks = [b"\x00\xff\x51\x03" + tempo_us.to_bytes(3, "big") + b"\x00\xff\x2f\x00"]
    for name, ch, notes in tracks:
        ev = []
        for t, ln, p in notes:
            ev += [(t, 0x90 | ch, p, 90), (t + ln, 0x80 | ch, p, 0)]
        ev.sort()
        body = b"\x00\xff\x03" + _var(len(name)) + name.encode()
        last, status = 0, None
        for t, st, p, v in ev:
            body += _var(t - last)
            if st != status:
                body += bytes([st])
                status = st
            body += bytes([p, v])
            last = t
        chunks.append(body + b"\x00\xff\x2f\x00")
    out = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), div)
    for c in chunks:
        out += b"MTrk" + struct.pack(">I", len(c)) + c
    return out


class Rules(unittest.TestCase):
    def test_roman_numerals(self):
        self.assertEqual(D.roman(7, "maj", 0), "V")
        self.assertEqual(D.roman(9, "min", 0), "vi")
        self.assertEqual(D.roman(10, "maj", 0, "7"), "bVII7")
        self.assertEqual(D.roman(3, "min", 0), "biii")
        self.assertEqual(D.roman(2, "min", 9), "iv")           # イ短調の Dm

    def test_key_reading(self):
        self.assertEqual(D.parse_key("F# minor"), (6, "minor"))
        self.assertEqual(D.parse_key("Bb major"), (10, "major"))
        self.assertIsNone(D.parse_key(None))
        self.assertEqual(D.key_fifths(0, "major"), 0)
        self.assertEqual(D.key_fifths(9, "minor"), 0)           # イ短調＝調号なし
        self.assertEqual(D.key_fifths(5, "major"), -1)
        self.assertEqual(D.key_fifths(4, "minor"), 1)

    def test_loop_is_written_from_the_bar_where_it_starts(self):
        bars = ["I", "V", "vi", "IV"] * 6
        lp = D.find_loop(bars)
        self.assertEqual(lp["pattern"], ["I", "V", "vi", "IV"])
        self.assertEqual(lp["bars"], 4)
        self.assertGreaterEqual(lp["coverage"], 0.95)

    def test_no_loop_when_one_chord_only(self):
        self.assertEqual(D.find_loop(["I"] * 16).get("bars", 2), 2)   # 4 小節の型としては数えない
        self.assertEqual(D.find_loop(["I", "V"]), {})

    def test_arc_shapes(self):
        self.assertTrue(D.arc_shape([50] * 40).startswith("ほぼ一定"))
        self.assertTrue(D.arc_shape([10 + i * 2 for i in range(40)]).startswith("右肩上がり"))
        two = ([20] * 8 + [80] * 8) * 2 + [20] * 8
        self.assertIn("山が 2 回", D.arc_shape(two))

    def test_drum_grid_labels(self):
        pos = lambda t: (int(t // 2), (t % 2) / 2)   # noqa: E731  1 小節 2 秒
        hits = []
        for bar in range(8):
            t0 = bar * 2
            hits += [(t0 + b * 0.5, 36) for b in range(4)]                 # キック：毎拍
            hits += [(t0 + 0.5, 38), (t0 + 1.5, 38)]                       # スネア：2・4 拍目
            hits += [(t0 + 0.25 + b * 0.5, 42) for b in range(4)]          # ハイハット：裏
        g = D.drum_grid(hits, pos)
        self.assertEqual(g["pattern"]["kick"], "x...x...x...x...")
        self.assertEqual(g["pattern"]["snare"], "....x.......x...")
        self.assertEqual(g["pattern"]["hat"], "..x...x...x...x.")
        self.assertTrue(any("4 つ打ち" in x for x in g["labels"]))
        self.assertTrue(any("バックビート" in x for x in g["labels"]))
        self.assertTrue(any("裏拍" in x for x in g["labels"]))

    def test_drum_grid_empty(self):
        self.assertNotIn("pattern", D.drum_grid([], lambda t: (0, 0.0)))


class Midi(unittest.TestCase):
    def test_reads_tracks_times_and_drums(self):
        raw = _midi([("Vocals", 0, [(0, 480, 60), (480, 240, 62), (960, 480, 64)]), ("Bass", 1, [(0, 960, 36)]), ("Drums", 9, [(0, 60, 36), (480, 60, 38)])])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.mid"
            p.write_bytes(raw)
            tr = D.read_midi(p)
        self.assertEqual(set(tr), {"vocals", "bass", "drums"})
        self.assertEqual(len(tr["vocals"]), 3)
        self.assertAlmostEqual(tr["vocals"][1][0], 0.5, places=3)      # 480 刻み＝1 拍＝0.5 秒（120 BPM）
        self.assertAlmostEqual(tr["vocals"][1][1], 0.75, places=3)
        self.assertEqual(tr["bass"][0][2], 36)
        self.assertEqual([p for _, _, p, _ in tr["drums"]], [36, 38])

    def test_rejects_non_midi(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.mid"
            p.write_bytes(b"not a midi file")
            with self.assertRaises(ValueError):
                D.read_midi(p)


def _fake_deep() -> dict:
    """音源なしで作った分析結果（120 BPM・4/4・ハ長調・C G Am F）"""
    beats = [i * 0.5 for i in range(64)]
    prog = [(0, "maj", ""), (7, "maj", ""), (9, "min", ""), (5, "maj", "maj7")]
    chords = [list(prog[(i // 4) % 4]) for i in range(64)]
    return {"version": 1, "groove": {"bpm": 120.0, "meter": 4, "time_signature": "4/4"},
            "harmony": {"key": "C major", "loop": {"bars": 4, "pattern": ["I", "V", "vi", "IV"], "coverage": 1.0},
                        "vocabulary": [{"chord": "I", "share": 0.25}, {"chord": "V", "share": 0.25}, {"chord": "vi", "share": 0.25}, {"chord": "IV", "share": 0.25}]},
            "density": {"onsets_per_bar": 8.0, "level": "ふつう"},
            "tension": {"per_bar": [20, 30, 40, 50, 60, 70, 80, 90] * 2, "shape": "山が 2 回", "average": 55},
            "structure": {"sections": [{"name": "intro", "bars": 4, "tension": 30}, {"name": "chorus 1", "bars": 8, "tension": 80}]},
            "drums": {"steps": 16, "pattern": {"kick": "x.......x.......", "snare": "....x.......x...", "hat": "x.x.x.x.x.x.x.x."},
                      "labels": ["キックは 1・3 拍目が軸", "スネアは 2・4 拍目（バックビート）", "ハイハットは 8 分で刻む"]},
            "instruments": {"listed": ["Guitar"], "parts": [{"key": "keys", "name": "キーボード", "en": "piano/keys", "active": 1.0},
                                                             {"key": "drums", "name": "ドラム", "en": "drums", "active": 0.8}],
                            "timeline": [{"section": "intro", "playing": ["キーボード"]}, {"section": "chorus 1", "playing": ["キーボード", "ドラム"], "added": ["ドラム"], "dropped": []}]},
            "bass": {"usable": False, "note": "x"},
            "vocal": {"usable": True, "active": 0.7, "layers": "ほぼ 1 本の声", "sex": "female"},
            "mix": {"bands": {"sub": 0.1, "bass": 0.5, "low_mid": 0.1, "mid": 0.2, "presence": 0.07, "air": 0.03}, "tone": "低音はふつう・高音はふつう"},
            "impression": {"words_ja": ["中くらいの速さ"], "words_en": ["mid-tempo"]},
            "_grid": {"beats": beats, "meter": 4, "down": 0, "chords": chords, "tonic": 0, "mode": "major"}}


class Card(unittest.TestCase):
    def test_card_has_no_names_lyrics_or_non_ascii(self):
        sheet = {"reference": {"artist_name": "Real Artist", "title": "Real Title", "tags": ["indie pop", "夜"]},
                 "vocal": {"sex": "female", "delivery": {"style": "ささやく"}}, "lyrics": {"transcript": "secret words here"}}
        d = _fake_deep()
        c = D.card(d, sheet)
        self.assertLessEqual(len(c["style"]), 1000)
        self.assertTrue(all(ord(ch) < 128 for ch in c["style"]))
        for banned in ("Real Artist", "Real Title", "secret words"):
            self.assertNotIn(banned, c["style"] + " ".join(c["structure"]))
        self.assertIn("120 BPM", c["style"])
        self.assertIn("C major", c["style"])
        self.assertIn("snare on 2 and 4", c["style"])
        self.assertIn("I-V-vi-IV", c["style"])
        self.assertEqual(c["structure"][0], "[Intro] 4 bars; piano/keys; energy 30/100")
        self.assertIn("adds drums", c["structure"][1])
        self.assertIn("配信する曲には使いません", c["note"])

    def test_no_drums_only_when_really_none(self):
        d = _fake_deep()
        d["drums"] = {"bars_with_drums": 0, "steps": 16}
        self.assertIn("no drums", D.card(d)["style"])
        d["drums"]["unknown"] = True                      # 拾う部品が動かなかったとき：無いとは言わない
        self.assertNotIn("no drums", D.card(d)["style"])


class Slots(unittest.TestCase):
    def test_each_slot_gets_only_its_own_part(self):
        d = _fake_deep()
        d["card"] = D.card(d)
        self.assertEqual(set(D.for_slot(d, "harmony")), {"harmony"})
        self.assertEqual(set(D.for_slot(d, "groove")), {"groove", "drums"})
        self.assertEqual(set(D.for_slot(d, "instruments")), {"instruments", "mix"})
        self.assertEqual(set(D.for_slot(d, "structure")), {"structure", "tension"})
        self.assertEqual(set(D.for_slot(d, "vocal_main")), {"vocal"})
        self.assertEqual(D.for_slot(d, "performance").get("bass"), None)     # 取れていないベースは渡さない
        for slot in ("lyrics", "worldview", "phrase", "vocal_sub1"):
            self.assertEqual(D.for_slot(d, slot), {})
        for slot in D.SLOT_PARTS:                                              # 再現カードは、どの枠にも渡さない
            self.assertNotIn("card", D.for_slot(d, slot))
            self.assertNotIn("_grid", D.for_slot(d, slot))
        self.assertEqual(len(D.for_slot(d, "structure")["tension"]["curve_16"]), 16)
        self.assertEqual(D.for_slot(None, "harmony"), {})

    def test_brief_excerpt_carries_measured_part(self):
        import write_brief
        with tempfile.TemporaryDirectory() as tmp:
            d = _fake_deep()
            sheet = {"reference": {"title": "T", "artist_name": "A"}, "harmony": {"progression_character": "ループ型"}, "groove": {"bpm": 120},
                     "deep": {k: v for k, v in d.items() if k != "_grid"}}
            (Path(tmp) / "db-aud-1.json").write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
            brief = {"slots": {"harmony": {"reference_id": "db-aud-1", "title": "T"}, "lyrics": {"reference_id": "db-aud-1", "title": "T"}}}
            ex = write_brief.slot_excerpts(brief, Path(tmp))
        self.assertEqual(set(ex["harmony"]["measured"]), {"harmony"})
        self.assertNotIn("measured", ex["lyrics"])
        self.assertNotIn("artist_name", json.dumps(ex["harmony"]["measured"]))

    def test_sheet_fields_are_not_overwritten(self):
        d = _fake_deep()
        d["bass"] = {"usable": True, "motion": "コードの根音を刻む", "kick_bass_relation": "キックと同じ所で鳴る（一体で動く）"}
        d["harmony"]["progression_character"] = "ループ型（測った）"
        sheet = {"harmony": {"progression_character": "人が書いた", "bass_motion": ""}, "groove": {"hihat_pattern": "人が書いた"}, "analyzed_by": "auto"}
        D.to_sheet(sheet, d)
        self.assertEqual(sheet["harmony"]["progression_character"], "人が書いた")
        self.assertEqual(sheet["groove"]["hihat_pattern"], "人が書いた")
        self.assertEqual(sheet["harmony"]["bass_motion"], "コードの根音を刻む")
        self.assertNotIn("_grid", sheet["deep"])
        self.assertIn("深い分析", sheet["analyzed_by"])
        D.to_sheet(sheet, d)
        self.assertEqual(sheet["analyzed_by"].count("深い分析"), 1)


class Compare(unittest.TestCase):
    def test_same_song_is_100_and_weak_side_is_named(self):
        a, b = _fake_deep(), _fake_deep()
        r = D.compare(a, b)
        self.assertEqual(r["overall"], 100)
        self.assertEqual(r["weakest"], [])
        b["groove"]["bpm"] = 150.0
        b["harmony"]["key"] = "F# minor"
        b["drums"]["pattern"]["kick"] = "x...x...x...x..."
        r = D.compare(a, b)
        self.assertLess(r["overall"], 85)
        self.assertIn("テンポ", r["weakest"])
        self.assertLess(r["dimensions"]["キー"], 50)

    def test_double_time_reading_is_half_credit(self):
        a, b = _fake_deep(), _fake_deep()
        b["groove"]["bpm"] = 240.0
        self.assertEqual(D.compare(a, b)["dimensions"]["テンポ"], 60)

    def test_unread_kick_is_not_compared(self):
        a, b = _fake_deep(), _fake_deep()
        b["drums"]["pattern"]["kick"] = "?" * 16
        self.assertEqual(D.compare(a, b)["dimensions"]["ドラムの型"], 100)


class Score(unittest.TestCase):
    def _write(self, tracks: dict, d: dict) -> xml.dom.minidom.Document:
        with tempfile.TemporaryDirectory() as tmp:
            p = D.write_leadsheet(Path(tmp) / "s.musicxml", "Test <&> Song", tracks, d)
            self.assertIsNotNone(p)
            return xml.dom.minidom.parse(str(p))

    def test_every_bar_is_full_and_chords_are_named(self):
        d = _fake_deep()
        vocals = [(4.0 + i * 0.5, 4.0 + i * 0.5 + 0.4, 60 + (i % 5), 90) for i in range(16)]
        vocals.append((12.25, 15.0, 67, 90))                       # 小節をまたぐ長い音（タイでつなぐ）
        bass = [(i * 2.0, i * 2.0 + 1.9, 36, 90) for i in range(8)]
        doc = self._write({"vocals": vocals, "bass": bass}, d)
        parts = doc.getElementsByTagName("part")
        self.assertEqual(len(parts), 2)
        for part in parts:
            for m in part.getElementsByTagName("measure"):
                total = sum(int(n.getElementsByTagName("duration")[0].firstChild.data) for n in m.getElementsByTagName("note"))
                self.assertEqual(total, 16, m.getAttribute("number"))
        kinds = [k.firstChild.data for k in doc.getElementsByTagName("kind")]
        self.assertIn("minor", kinds)
        self.assertIn("major-seventh", kinds)
        self.assertTrue(doc.getElementsByTagName("tie"))
        self.assertEqual(doc.getElementsByTagName("fifths")[0].firstChild.data, "0")
        self.assertEqual(len(parts[1].getElementsByTagName("harmony")), 0)        # コード名は旋律の段だけ

    def test_pickup_notes_before_bar_one_are_kept(self):
        d = _fake_deep()
        d["_grid"]["down"] = 2                                       # 3 拍目が小節の頭（その前に歌い出しがある）
        doc = self._write({"vocals": [(0.5, 0.9, 64, 90), (1.0, 1.9, 65, 90), (2.0, 2.9, 67, 90)]}, d)
        steps = [n.getElementsByTagName("step")[0].firstChild.data for n in doc.getElementsByTagName("note") if n.getElementsByTagName("pitch")
                 and not any(t.getAttribute("type") == "stop" for t in n.getElementsByTagName("tie"))]     # タイの続きは数えない
        self.assertEqual(steps[:3], ["E", "F", "G"])

    def test_nothing_to_write(self):
        self.assertIsNone(D.write_leadsheet(Path("/nonexistent/x.musicxml"), "t", {"drums": [(0, 0.1, 36, 90)]}, _fake_deep()))


class Folders(unittest.TestCase):
    """SSD の中のフォルダ名：濁点が 2 文字に分かれて入る書き方（NFD）と、二重になった名前"""

    def test_midi_folder_is_not_taken_for_reference_songs(self):
        import reference_db as R
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Postdog Records"
            base.mkdir()
            top = Path(tmp) / "既存楽曲"
            top.mkdir()
            nfd = Path(tmp) / unicodedata.normalize("NFD", "既存楽曲_メロディー（MIDI）_メロディー（MIDI）")
            nfd.mkdir()
            (Path(tmp) / "既存楽曲_解析シート").mkdir()
            old_root, old_env = R.archive.root, R.os.environ.pop("REFERENCE_AUDIO", None)
            R.archive.root = lambda: (base, "test")
            try:
                dirs = R.audio_dirs()
                self.assertEqual([d.name for d in dirs], ["既存楽曲"])
                found = R.melody_dirs(top)
                self.assertEqual(len(found), 1)
                song = found[0] / "A - B"
                song.mkdir()
                (song / "A - B.mid").write_bytes(b"x" * 300)
                f = top / "b.wav"
                f.write_bytes(b"")
                sheet = {"reference": {"artist_name": "A", "title": "B"}, "melody": {"midi_file": "/gone/after/reset.mid"}}
                self.assertEqual(R.find_midi(sheet, f), song / "A - B.mid")
                self.assertIsNone(R.find_midi({"reference": {"artist_name": "A", "title": "Other"}}, f))
            finally:
                R.archive.root = old_root
                if old_env is not None:
                    R.os.environ["REFERENCE_AUDIO"] = old_env


@unittest.skipUnless(HAVE_AUDIO, "librosa が入っていない所では、音を使う試験は飛ばします")
class Audio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import _synth
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wav = Path(cls.tmp.name) / "a.wav"
        cls.tracks = _synth.song(str(cls.wav))
        cls.deep = D.analyze(cls.wav, tracks=cls.tracks)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tempo_is_fine_grained(self):
        self.assertAlmostEqual(self.deep["groove"]["bpm"], 120.0, delta=0.6)      # 前の測り方だと 117.5 になっていた
        self.assertEqual(self.deep["groove"]["meter"], 4)
        self.assertTrue(self.deep["groove"]["tempo_drift"].startswith("ほぼ一定"))

    def test_key_and_progression(self):
        h = self.deep["harmony"]
        self.assertEqual(h["key"], "C major")
        self.assertEqual(h["loop"]["pattern"], ["I", "V", "vi", "IV"])
        self.assertEqual(h["modulation_count"], 0)
        self.assertEqual(h["outside_key"], [])

    def test_drums_bass_and_parts_from_tracks(self):
        p = self.deep["drums"]["pattern"]
        self.assertEqual((p["kick"], p["snare"], p["hat"]), ("x.......x.......", "....x.......x...", "x.x.x.x.x.x.x.x."))
        self.assertEqual(self.deep["bass"]["motion"], "コードの根音を刻む")
        tl = self.deep["instruments"]["timeline"]
        self.assertEqual(tl[0]["playing"], ["キーボード"])                          # 最初の 4 小節はキーボードだけ
        self.assertIn("ドラム", tl[1]["added"])
        self.assertEqual(self.deep["vocal"]["layers"], "ほぼ 1 本の声")

    def test_tension_rises_after_quiet_intro(self):
        t = self.deep["tension"]
        self.assertLess(t["sections"][0]["tension"], 30)
        self.assertGreater(t["sections"][-1]["tension"], 60)
        self.assertEqual(t["biggest_rise"]["at_sec"], 8.0)

    def test_without_midi_kick_is_marked_unread(self):
        d = D.analyze(self.wav)
        self.assertEqual(d["drums"]["pattern"]["kick"], "?" * 16)
        self.assertFalse(any("キック" in x for x in d["drums"].get("labels") or []))
        self.assertNotIn("parts", d["instruments"])
        self.assertNotIn("four-on-the-floor", d["card"]["style"])

    def test_three_four_time(self):
        import _synth
        w = Path(self.tmp.name) / "b.wav"
        tr = _synth.song(str(w), bpm=96, meter=3, bars=40)
        d = D.analyze(w, tracks=tr)
        self.assertEqual(d["groove"]["meter"], 3)
        self.assertAlmostEqual(d["groove"]["bpm"], 96.0, delta=0.6)
        self.assertEqual(len(d["drums"]["pattern"]["kick"]), 12)

    def test_reference_db_run_updates_sheet_and_writes_score(self):
        import subprocess
        import melody
        import reference_db as R
        try:
            import pretty_midi  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("pretty_midi が無い")
        root = Path(self.tmp.name) / "ssd"
        top = root / "既存楽曲"
        top.mkdir(parents=True)
        f = top / "song.wav"
        f.write_bytes(self.wav.read_bytes())
        folder = root / "既存楽曲_メロディー（MIDI）" / "Someone - Song"
        folder.mkdir(parents=True)
        melody.write_full_midi(self.tracks, 120, folder / "Someone - Song.mid")
        cache = root / "cache"
        cache.mkdir()
        sheet = {"reference": {"artist_name": "Someone", "title": "Song"}, "groove": {"bpm": 117.5}, "harmony": {"progression_character": "不明"},
                 "melody": {"usable": False}}
        (cache / "aud-1.json").write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
        (root / "index.json").write_text(json.dumps({"aud-1": {"file": str(f)}}), encoding="utf-8")
        keep = (R.AUDIO_CACHE, R.AUDIO_INDEX, R.audio_dirs, subprocess.run)
        R.AUDIO_CACHE, R.AUDIO_INDEX, R.audio_dirs = cache, root / "index.json", lambda: [top]
        subprocess.run = lambda *a, **k: None                       # 「参考曲に登録し直す」は、ここでは動かさない
        try:
            self.assertEqual(len(R.deep_pending()), 1)
            R.audio_deep(0, quiet=True)
            self.assertEqual(R.deep_pending(), [])
        finally:
            R.AUDIO_CACHE, R.AUDIO_INDEX, R.audio_dirs, subprocess.run = keep
        d = json.loads((cache / "aud-1.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(d["groove"]["bpm"], 120.0, delta=0.6)
        self.assertEqual(d["groove"]["bpm_coarse"], 117.5)
        self.assertTrue(d["harmony"]["progression_character"].startswith("ループ型"))
        self.assertTrue((folder / "Someone - Song_楽譜.musicxml").is_file())
        self.assertIn("MIDI", d["deep"]["source"])


if __name__ == "__main__":
    unittest.main()
