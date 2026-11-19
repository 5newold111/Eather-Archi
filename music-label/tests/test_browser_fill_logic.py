"""DistroKid の自動入力（browser_fill.py）：ブラウザの要らない決め方と、似せた画面での入れ方（決定 61・129・140・149・150・157・163〜165）

  前半（Logic…）：ブラウザを使わない。どの環境でも動く
  後半（FormTest）：Python の Playwright があるときだけ。tests/fixtures/distrokid_form.html（本物の欄の名前をまねた小さな画面）で、
                    入れ方・選び直し・確認の小窓・「押さないもの」を確かめる。無ければ飛ばす
"""
from __future__ import annotations

import contextlib
import glob
import io
import os
import time
import types
import unicodedata
import unittest
from datetime import date, timedelta
from pathlib import Path

from _fixture import Project

FORM = Path(__file__).resolve().parent / "fixtures" / "distrokid_form.html"


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


class FakePage:
    """page.evaluate の返事だけを決めておく、にせのページ"""
    def __init__(self, answers: dict):
        self.answers, self.calls = answers, []

    def evaluate(self, js, arg=None):
        self.calls.append((js, arg))
        for key, val in self.answers.items():
            if key in js:
                return val(arg) if callable(val) else val
        return None


def fake_dk(bf, song, fields: dict, answers: dict):
    c = bf.DK(types.SimpleNamespace(page=FakePage(answers)), song, [dict(key=k, **v) for k, v in fields.items()])
    return c


class LogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = p = Project(ssd=True, debuted=("declan_fay", "hollowplan"))
        cls.k_ready = p.weekly("hollowplan", p.week(-3), "After Nights", stage="ready")              # 配信日はもう過ぎている週
        cls.k_future = p.weekly("hollowplan", p.week(2), "Next One", stage="ready")
        cls.k_inst = p.weekly("declan_fay", p.week(-1), "No Words", stage="brief", language="none", suno_lyrics="")
        cls.k_new = p.weekly("tidewool", p.week(-1), "First Wave", stage="brief")                    # まだ Spotify にいない組の毎週の曲
        cls.k_debut = p.debut("tidewool", "The Low Part", "ready")
        cls.bf = p.mod("browser_fill")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    # ---- 項目の一覧
    def test_sections_are_22_in_screen_order(self):
        secs = self.bf.dk_sections(self.p.song(self.k_ready))
        keys = [s["key"] for s in secs]
        self.assertEqual(len(secs), 22)
        self.assertEqual(len(set(keys)), 22, "同じ項目を 2 回入れない（決定 148）")
        self.assertEqual(keys[:4], ["previous", "artist", "ids", "release"])
        self.assertLess(keys.index("cover"), keys.index("artwork_ai"))
        self.assertLess(keys.index("title"), keys.index("audio"))
        self.assertEqual(keys[-1], "credits")
        for s in secs:
            self.assertTrue(callable(s["do"]) and callable(s["ok"]), s["key"])
        again = {s["key"]: s["again"] for s in secs}
        self.assertEqual([k for k, v in again.items() if not v], ["artist", "cover", "audio"], "見張りで入れ直さない項目")

    def test_instrumental_section_follows_song(self):
        name = lambda k: next(s["name"] for s in self.bf.dk_sections(self.p.song(k)) if s["key"] == "instrumental")  # noqa: E731
        self.assertIn("歌詞あり", name(self.k_ready))
        self.assertIn("歌詞なし", name(self.k_inst))

    # ---- AI の申告
    def test_ai_parts(self):
        ap = self.bf.ai_parts
        self.assertEqual(ap(self.p.song(self.k_ready)), ["lyrics", "music", "all_audio"])
        self.assertEqual(ap(self.p.song(self.k_inst)), ["music", "all_audio"])
        self.assertEqual(ap({"ai_parts": ["lyrics", "part_audio"], "lyrics": "x"}), ["lyrics", "part_audio"])
        sec = next(s for s in self.bf.dk_sections(self.p.song(self.k_ready)) if s["key"] == "artwork_ai")
        self.assertIn("Yes", sec["name"], "ジャケットは AI で作っているので、正直に Yes（決定 164）")

    def test_role_names(self):
        r = self.bf.role_is
        self.assertTrue(r("Singing and Vocals", "Singing & vocals"))
        self.assertTrue(r("producer", "Producer"))
        self.assertFalse(r("Sampled artist", "Singing & vocals"), "ほかの役割には逃げない（決定 71）")
        self.assertFalse(r("", "Producer"))

    # ---- Artist already in
    def test_radios_for_new_act_debut_are_all_no(self):
        items, texts = self.bf.artist_id_radios(self.p.song(self.k_debut))
        self.assertEqual(len(items), 5)
        self.assertEqual(texts, [])
        for label, css in items:
            self.assertIn("No", label)
            self.assertTrue(css.endswith('[value="new"]'), css)

    def test_radios_for_new_act_second_song_only_when_zero_matches(self):
        items, _ = self.bf.artist_id_radios(self.p.song(self.k_new))
        self.assertTrue(all('[class*="ZeroMatches"]' in css for _, css in items))

    def test_radios_for_debuted_act_use_recorded_spotify_page(self):
        items, texts = self.bf.artist_id_radios(self.p.song(self.k_ready))
        self.assertIn("Yes", items[0][0])
        self.assertEqual(texts[0][1:], ("input.myCustomSpotifyUri", "spotify:artist:2222222222222222222222"))
        self.assertTrue(self.bf._debuted(self.p.song(self.k_ready)))
        self.assertFalse(self.bf._debuted(self.p.song(self.k_new)))

    def test_meta_no_until_pages_recorded(self):
        """Instagram・Facebook：組のページを記録していなければ No（決定 150・157）"""
        song = self.p.song(self.k_ready)
        self.assertEqual(self.bf.meta_no(song), ["instagramProfileArtistID", "facebookProfileArtistID"])
        self.p.edit_artist("hollowplan", lambda a: a["distribution"]["artist_pages"].update(instagram="https://instagram.com/hollowplan"))
        try:
            self.assertEqual(self.bf.meta_no(song), ["facebookProfileArtistID"])
        finally:
            self.p.edit_artist("hollowplan", lambda a: a["distribution"]["artist_pages"].pop("instagram"))

    def test_page_ids(self):
        """Yes の下に入れる ID・住所：YouTube Music はチャンネル ID、Apple Music はアーティストの住所（決定 165）"""
        ids = self.bf.page_ids(self.p.song(self.k_ready))
        self.assertEqual(ids["googleArtistID"], "UC2222222222222222222222")
        self.assertEqual(ids["appleArtistID"], "https://music.apple.com/jp/artist/hollowplan/123456789")
        self.assertEqual(self.bf.page_ids(self.p.song(self.k_new)), {})

    # ---- 入れる値
    def fields(self, key: str, **env):
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            (fs, _f), out = quiet(self.bf.distrokid_fields, self.p.song(key))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return {x["key"]: x for x in fs}, out

    def test_release_date_past_becomes_today(self):
        f, out = self.fields(self.k_ready)
        self.assertEqual(f["release"]["value"], date.today().isoformat(), "過ぎた配信日は選べないので、いちばん早い日（決定 165）")
        self.assertIn("過ぎている", out)

    def test_release_date_future_is_kept_and_debut_is_asap(self):
        f, _ = self.fields(self.k_future)
        self.assertEqual(f["release"]["value"], (date.fromisoformat(self.p.week(2)) + timedelta(days=16)).isoformat())
        f, _ = self.fields(self.k_debut)
        self.assertEqual(f["release"]["value"], date.today().isoformat())

    def test_songwriter_name_split(self):
        f, _ = self.fields(self.k_ready, OWNER_LEGAL_NAME="Taro Jiro Yamada")
        self.assertEqual((f["songwriter_first"]["value"], f["songwriter_middle"]["value"], f["songwriter_last"]["value"]), ("Taro", "Jiro", "Yamada"))
        self.assertEqual(f["songwriter_role"]["value"], "Music and lyrics")
        f, _ = self.fields(self.k_ready)
        self.assertEqual(f["songwriter_first"]["value"], "", "本名は .env にだけ。無ければ空のまま")

    def test_basic_field_values(self):
        f, _ = self.fields(self.k_ready)
        self.assertEqual(f["artist"]["value"], "HOLLOWPLAN")
        self.assertEqual(f["title"]["value"], "After Nights")
        self.assertEqual(f["language"]["value"], "English")
        self.assertEqual(f["release"]["css"], "input[name=releaseDate]")

    # ---- 音源とジャケットの渡し方
    def test_uploads_are_staged_inside_mac(self):
        """Chrome には、いつも Mac の中の写し（out/distrokid_upload/<曲>/）を渡す（決定 163）"""
        song = self.p.song(self.k_ready)
        (audio, cover, how), _ = quiet(self.bf.upload_files, song)
        stage = self.p.out / "distrokid_upload" / self.k_ready
        self.assertEqual(audio.parent, stage)
        self.assertEqual(cover.parent, stage)
        self.assertEqual(audio.name, f"After Nights_HOLLOWPLAN_{date.today().isoformat()}.wav")
        self.assertTrue(cover.name.startswith("HOLLOWPLAN_After Nights_") and cover.name.endswith(".jpg"))
        self.assertNotIn("Suno元データ", audio.name, "音量を整える前の元データは渡さない")
        self.assertEqual(audio.read_bytes(), (self.p.out / "masters" / self.k_ready / "master.wav").read_bytes())
        self.assertIn("曲のフォルダ", how, "ローカル保存の 04_楽曲/<曲名>/（公開セット）も曲のフォルダとして見る")
        f, _ = self.fields(self.k_ready)
        self.assertEqual(f["audio"]["value"], str(audio))
        self.assertEqual(f["cover"]["value"], str(cover))

    def test_uploads_prefer_song_folder_on_ssd(self):
        """まず曲のフォルダ（SSD の 02_アーティスト/<組>/04_楽曲/<曲名>/）を見る（決定 151・158）"""
        song = self.p.song(self.k_future)
        publish = self.p.mod("publish")
        _folder, aname, cname = publish.publish_folder(song)
        d = self.p.ssd / "02_アーティスト" / "HOLLOWPLAN" / "04_楽曲" / "Next One"
        self.p.wav(d / aname, sec=0.2, freq=777)
        self.p.jpg(d / cname, color=(7, 7, 7))
        (audio, cover, how), _ = quiet(self.bf.upload_files, song)
        self.assertIn("曲のフォルダ", how)
        self.assertEqual(audio.read_bytes(), (d / aname).read_bytes())
        self.assertEqual(audio.parent, self.p.out / "distrokid_upload" / self.k_future, "SSD の上のファイルは渡さない")

    def test_upload_copy_is_idempotent(self):
        song = self.p.song(self.k_ready)
        src = self.p.out / "masters" / self.k_ready / "master.wav"
        a = self.bf._upload_copy(song, src, "x.wav")
        t = a.stat().st_mtime_ns
        b = self.bf._upload_copy(song, src, "x.wav")
        self.assertEqual((a, t), (b, b.stat().st_mtime_ns), "同じ中身なら写し直さない（決定 129）")

    def test_nfc_name_for_staging(self):
        nfd = unicodedata.normalize("NFD", "デモ.wav")
        src = self.p.wav(self.p.tmp / nfd, sec=0.1)
        dst = self.bf._upload_copy(self.p.song(self.k_ready), src, unicodedata.normalize("NFC", src.name))
        self.assertEqual(dst.name, unicodedata.normalize("NFC", dst.name))

    def test_old_staging_is_tidied(self):
        d = self.p.out / "distrokid_upload" / "old_song"
        d.mkdir(parents=True)
        t = time.time() - 40 * 86400
        os.utime(d, (t, t))
        keep = self.p.out / "distrokid_upload" / "new_song"
        keep.mkdir()
        self.bf._tidy_uploads()
        self.assertFalse(d.exists())
        self.assertTrue(keep.exists())

    def test_missing_audio_gives_reason(self):
        """音源がまだ無い：理由と、手で選ぶときのファイルを出す（決定 164）"""
        song = self.p.song(self.k_inst)
        c = fake_dk(self.bf, song, {"audio": {"value": "", "css": "input.trackupload_1", "type": "file", "label": "音源"}}, {})
        _do, ok = self.bf._file_field("audio", "音源")
        self.assertIs(ok(c), False)
        self.assertIn("まだできていません", c.notes["audio"])
        self.assertIn("04_楽曲/No Words/", c.notes["audio"])

    def test_wrong_file_is_detected(self):
        """別の曲のファイルが入っていれば、その名前（決定 149）"""
        song = self.p.song(self.k_ready)
        mk = lambda got: fake_dk(self.bf, song, {"audio": {"value": "/x/After Nights_HOLLOWPLAN_2026-11-01.wav", "css": "input.trackupload_1"}},  # noqa: E731
                                 {"x.files": got})
        self.assertEqual(self.bf.wrong_file(mk("Side B_DECLAN FAY_2026-10-20.wav"), "audio"), "Side B_DECLAN FAY_2026-10-20.wav")
        self.assertEqual(self.bf.wrong_file(mk("After Nights_HOLLOWPLAN_2026-11-01.wav"), "audio"), "")
        self.assertEqual(self.bf.wrong_file(mk("after-nights (1).wav"), "audio"), "", "曲名が入っていれば別の曲とはみない")
        self.assertEqual(self.bf.wrong_file(mk(""), "audio"), "")

    def test_release_is_clamped_to_first_selectable_day(self):
        """画面の日付の欄に選べる最初の日（min）があり、入れる日がそれより前なら、その日にする（決定 165）"""
        song = self.p.song(self.k_ready)
        filled = []
        old = self.bf.fill_fields
        self.bf.fill_fields = lambda sess, site, fs, fresh=True: (filled.append(dict(fs[0])), ([], []))[1]
        try:
            c = fake_dk(self.bf, song, {"release": {"value": "2026-11-01", "type": "date"}}, {"releaseDate": "2026-11-20"})
            quiet(self.bf._sec_release_do, c)
            self.assertEqual(filled[-1]["value"], "2026-11-20")
            c = fake_dk(self.bf, song, {"release": {"value": "2026-12-24", "type": "date"}}, {"releaseDate": "2026-11-20"})
            quiet(self.bf._sec_release_do, c)
            self.assertEqual(filled[-1]["value"], "2026-12-24", "選べる日ならそのまま")
            c = fake_dk(self.bf, song, {"release": {"value": "2026-11-01", "type": "date"}}, {"releaseDate": ""})
            quiet(self.bf._sec_release_do, c)
            self.assertEqual(filled[-1]["value"], "2026-11-01")
        finally:
            self.bf.fill_fields = old

    def test_never_words(self):
        """システムが絶対に押さないボタンの言葉"""
        for w in ("Create", "Generate", "Submit", "Upload", "Done", "Publish", "Buy now", "Delete", "Upgrade", "作成", "送信", "購入", "削除"):
            self.assertTrue(self.bf.NEVER.search(w), w)
        for w in ("Lyrics", "Instrumental", "Advanced Options", "男性"):
            self.assertFalse(self.bf.NEVER.search(w), w)


def _chromium():
    """(playwright の入口, 起動の引数) か None。Python の Playwright が無ければ None"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return None
    exe = os.environ.get("POSTDOG_TEST_CHROMIUM") or next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"))), None)
    return sync_playwright, ({"executable_path": exe} if exe else {})


@unittest.skipUnless(_chromium(), "Python の Playwright がありません（python3 -m pip install playwright && python3 -m playwright install chromium）")
class FormTest(unittest.TestCase):
    """似せた登録画面に、本物と同じ部品で入れる"""

    @classmethod
    def setUpClass(cls):
        cls.p = p = Project(ssd=False, debuted=("declan_fay", "hollowplan"))
        cls.key = p.weekly("hollowplan", p.week(-3), "After Nights", stage="ready")
        cls.bf = bf = p.mod("browser_fill")
        sp, opts = _chromium()
        cls.pw = sp().start()
        try:
            cls.browser = cls.pw.chromium.launch(headless=True, args=["--no-sandbox"], **opts)
        except Exception as e:  # noqa: BLE001  ブラウザ本体が入っていない
            cls.pw.stop()
            cls.p.close()
            raise unittest.SkipTest(f"Chromium を起動できません：{str(e).splitlines()[0][:120]}")
        cls.ctx = cls.browser.new_context()
        cls.page = cls.ctx.new_page()
        cls.page.goto(FORM.as_uri())
        cls.min = (date.today() + timedelta(days=9)).isoformat()
        cls.page.evaluate("(m) => document.querySelector('input[name=releaseDate]').min = m", cls.min)
        sess = bf.Session.__new__(bf.Session)        # 専用の Chrome を開かずに、この画面だけを持たせる
        sess.page, sess.ctx, sess.msg, sess.overlay = cls.page, cls.ctx, None, None
        real_wait = cls.page.wait_for_timeout
        cls.page.wait_for_timeout = lambda ms: real_wait(min(ms, 120))   # 本物の待ち時間（0.3〜3 秒）は、この小さな画面では要らない
        sess.settle = lambda ms=0: real_wait(60)
        cls.sess = sess
        cls.song = p.song(cls.key)
        (fields, _f), _ = quiet(bf.distrokid_fields, cls.song)
        cls.c = bf.DK(sess, cls.song, fields)
        cls.secs = {s["key"]: s for s in bf.dk_sections(cls.song)}
        cls.log = []
        for key in ("previous", "artist", "ids", "release", "language", "cover", "artwork_ai", "title", "audio", "explicit", "instrumental"):
            _, out = quiet(cls.secs[key]["do"], cls.c)       # 本番と同じ順に 1 回ずつ入れる
            cls.log.append(out)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            cls.p.close()

    def js(self, expr, arg=None):
        return self.page.evaluate(expr, arg)

    def checked(self, name: str):
        return self.js("(n) => (document.querySelector(`input[type=radio][name=\"${n}\"]:checked`) || {}).value", name)

    def test_simple_radios(self):
        self.assertEqual(self.checked("previouslyReleased_1"), "0")
        self.assertEqual(self.checked("artworkAiGenerated"), "1")
        self.assertEqual(self.checked("explicit_1"), "0")
        self.assertEqual(self.checked("instrumental_1"), "0")
        for k in ("previous", "artwork_ai", "explicit", "instrumental"):
            self.assertIs(self.secs[k]["ok"](self.c), True, k)

    def test_debuted_act_is_picked_from_dropdown(self):
        self.assertEqual(self.js("() => document.querySelector('select[name=bandname]').selectedOptions[0].text"), "HOLLOWPLAN")
        self.assertIs(self.secs["artist"]["ok"](self.c), True)

    def test_instagram_facebook_are_no_and_confirmed(self):
        """Instagram・Facebook は No。取り消されたら文字を押して選び直し、確認の小窓に OK（決定 150・157）"""
        self.assertEqual(self.checked("instagramProfileArtistID"), "new")
        self.assertEqual(self.checked("facebookProfileArtistID"), "new")
        self.assertEqual(self.js("() => window.__reverted"), {"Instagram": True}, "1 回取り消されたあとに選び直している")
        self.assertEqual(sorted(self.js("() => window.__confirmed")), ["Facebook", "Instagram"])
        self.assertEqual(self.js("() => document.querySelectorAll('.swal2-popup').length"), 0, "小窓は閉じている")

    def test_existing_pages_are_yes_with_recorded_ids(self):
        """Spotify・Apple Music・YouTube Music は Yes。下の欄に記録した ID・住所（決定 140・165）"""
        for n in ("spotifyArtistID", "appleArtistID", "googleArtistID"):
            self.assertEqual(self.checked(n), "", n)
        self.assertEqual(self.js("() => document.querySelector('input.myCustomSpotifyUri').value"), "spotify:artist:2222222222222222222222")
        self.assertEqual(self.js("() => document.querySelector('input[name=ytChannel]').value"), "UC2222222222222222222222")
        self.assertEqual(self.js("() => document.querySelector('input[name=appleUrl]').value"), "https://music.apple.com/jp/artist/hollowplan/123456789")
        self.assertEqual(self.js("() => document.querySelector('input[name=igUrl]').value"), "", "No にしたサービスの欄には入れない")
        self.assertIs(self.secs["ids"]["ok"](self.c), True)

    def test_release_date_is_first_selectable_day(self):
        self.assertEqual(self.js("() => document.querySelector('input[name=releaseDate]').value"), self.min)
        self.assertIs(self.secs["release"]["ok"](self.c), True)

    def test_title_language_and_files(self):
        self.assertEqual(self.js("() => document.querySelector('input.uploadFileTitle').value"), "After Nights")
        self.assertEqual(self.js("() => document.querySelector('select[name=language]').value"), "English")
        audio = self.js("() => [...document.querySelector('input.trackupload_1').files].map(f => f.name).join(',')")
        cover = self.js("() => [...document.querySelector('input[name=artwork]').files].map(f => f.name).join(',')")
        self.assertEqual(audio, Path(self.c.f["audio"]["value"]).name)
        self.assertEqual(cover, Path(self.c.f["cover"]["value"]).name)
        self.assertIs(self.secs["audio"]["ok"](self.c), True)
        self.assertIs(self.secs["cover"]["ok"](self.c), True)

    def test_wrong_audio_is_flagged_then_checklist(self):
        other = self.p.wav(self.p.tmp / "Side B_DECLAN FAY_2026-10-20.wav", sec=0.1)
        self.page.set_input_files("input.trackupload_1", str(other))
        try:
            self.c.notes.pop("audio", None)
            self.assertIs(self.secs["audio"]["ok"](self.c), False)
            self.assertIn("別の曲の音源", self.c.notes["audio"])
            secs = list(self.secs.values())
            st, _ = quiet(self.bf.dk_check, self.c, secs)
            lines = self.bf.dk_checklist(secs, st, self.c)
            self.assertEqual(len(lines), 22)
            self.assertTrue(any(ln.startswith("✕") and "別の曲の音源" in ln for ln in lines), lines)
            self.assertTrue(any(ln.startswith("－") for ln in lines), "この画面に無い項目は「－」")
        finally:
            self.page.set_input_files("input.trackupload_1", self.c.f["audio"]["value"])
            self.c.notes.pop("audio", None)

    def test_zz_never_pressed(self):
        """システムは、最後のボタン（Done・Continue）・宣誓のチェック・Extras を押さない"""
        ok = self.bf.put(self.sess, self.page.locator("#done"), {"type": "click", "value": "x", "key": "x", "label": "x"})
        self.assertFalse(ok, "「Done」は押さない")
        self.assertEqual(self.js("() => window.__touched"), [])
        self.assertEqual(self.js("() => [...document.querySelectorAll('.areyousure, #extras input')].filter(e => e.checked).length"), 0)


if __name__ == "__main__":
    unittest.main()
