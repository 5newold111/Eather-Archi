"""
初期化した Mac を SSD から立て直す道具（scripts/recover.py・setup_mac.sh。決定 180）の試験

作り物のレーベルで曲をいくつかの段階まで進め、いつもの保管（archive_sync.py）で作り物の SSD に写す。
そのあと Mac の中の記録（out/ と、デビュー用の画像）を消して「初期化した Mac」にし、SSD から作り直せるかを確かめる。
  ・点検（check）が、SSD の中身を数えて「次にすること」を 1 つ示す。鍵の値は出さない
  ・見るだけ（from-ssd）は、どこにも書かない
  ・戻す（from-ssd --apply）と、配信中・登録済みの記録（ISRC・UPC・住所・配信日）と、登録前の曲のテイク・ジャケットが戻る
  ・Mac にもうある記録は上書きしない（空いている ISRC などだけ足す）
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import KIT_DAY, REPO, Project  # noqa: E402

SECRET = "zzSECRETzz-12345"
ISRC, UPC, URL = "QZTEST2600042", "000000000042", "https://open.spotify.com/track/4242424242424242424242"


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path: Path, sheet: str, rows: list, shared: bool = False) -> None:
    """表計算の部品（openpyxl）が無い環境でも台帳を作れる、最小の Excel。shared=True は文字を「共有の文字の表」に置く（Excel・openpyxl の書き方）"""
    strings: list[str] = []
    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, v in enumerate(row):
            if v is None or v == "":
                continue
            ref = f"{_col(c)}{r}"
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            elif shared:
                strings.append(str(v))
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '</Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?><worksheet {ns}><sheetData>{"".join(body)}</sheetData></worksheet>')
        if shared:
            z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst {ns}>' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")


def files_under(top: Path) -> dict:
    return {str(f.relative_to(top)): f.stat().st_size for f in sorted(top.rglob("*")) if f.is_file() and "__pycache__" not in f.parts}


class RecoverFromSsdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = p = Project(ssd=True)
        # ---- 1 ふだんの状態を作る（登録済みまで進めて保管し、そのあと配信中にする）
        cls.live = p.weekly("hollowplan", p.week(-6), "After Nights", stage="registered", days_ago=20)
        cls.kept = p.weekly("tidewool", p.week(-5), "Kept Song", stage="registered", days_ago=18)
        cls.reg = p.weekly("declan_fay", p.week(-2), "Side Street", stage="registered", days_ago=3)
        cls.ready = p.weekly("tidewool", p.week(-1), "Low Tide", stage="ready")
        cls.brief = p.weekly("hollowplan", p.week(0), "Not Yet", stage="brief")
        cls.debut = p.debut("declan_fay", "Side B", stage="registered", days_ago=30)
        p.jpg(p.root / "debut_kit" / KIT_DAY / "images" / "declan_fay_photo.jpg")
        cls.sync = p.run("archive_sync.py")   # ローカル保存 → SSD（いつもの保管）
        for key, day in ((cls.live, "2026-10-21"), (cls.kept, "2026-10-28"), (cls.debut, "2026-10-14")):
            p.set_state(key, status="live", released=day, release_date=day, isrc=ISRC, upc=UPC, url=URL)
        p.reload()
        sb = p.mod("song_book")
        rows = sb.merge(sb.debut_rows() + sb.weekly_rows(), {})
        book = p.ssd / "01_管理表" / "楽曲管理.xlsx"
        try:
            import openpyxl  # noqa: F401
            sb.write(book, rows)
        except ImportError:
            write_xlsx(book, "楽曲", [[h for h, _, _ in sb.COLUMNS]] + [[r.get(h, "") for h, _, _ in sb.COLUMNS] for r in rows])
        cls.rows = rows
        # 手で足した行（GitHub に無い曲）と、Mac が外付けのドライブに作る付き添いのファイル
        (p.ssd / "02_アーティスト" / "HOLLOWPLAN" / "04_楽曲" / "After Nights" / "._After Nights.wav").write_bytes(b"x")
        p.wav(p.ssd / "02_アーティスト" / "HOLLOWPLAN" / "04_楽曲" / "Loose One" / "Loose One_HOLLOWPLAN_2026-11-01.wav", sec=0.2)   # 台帳に行が無い曲
        # 既存楽曲（参考曲）：音源 1 本と、その解析シート・音源の無いシート・名前が二重になった MIDI のフォルダ
        drive = p.ssd.parent
        cls.ref_audio = p.wav(drive / "既存楽曲" / "Some Band" / "Some Song.wav", sec=0.3)
        (drive / "既存楽曲_メロディー（MIDI）_メロディー（MIDI）").mkdir()
        (drive / "既存楽曲_メロディー（MIDI）_メロディー（MIDI）" / "aud-x.mid").write_bytes(b"MThd")
        (drive / "既存楽曲_解析シート").mkdir()
        cls.rid = None
        try:
            rd = p.mod("reference_db")
            cls.rid = rd._rid(drive / "既存楽曲", cls.ref_audio)
            (drive / "既存楽曲_解析シート" / f"{cls.rid}.json").write_text(json.dumps({"reference": {"title": "Some Song"}}), encoding="utf-8")
        except Exception:  # noqa: BLE001  参考曲の部品が読めない環境では、その試験だけ飛ばす
            pass
        (drive / "既存楽曲_解析シート" / "aud-000000000000.json").write_text("{}", encoding="utf-8")
        # ---- 2 Mac を初期化する：out/ と、GitHub に入らない画像・ローカル保存が消える
        shutil.rmtree(p.out)
        shutil.rmtree(p.local)
        shutil.rmtree(p.root / "debut_kit" / KIT_DAY / "images")
        p.out.mkdir()
        p.local.mkdir()
        p.reload()
        # ---- 3 Mac にもう記録がある曲（あとから手で作った、という想定）
        (p.out / "publish" / "state").mkdir(parents=True)
        (p.out / "publish" / "state" / f"{cls.kept}.json").write_text(json.dumps({"status": "live", "released": "2030-02-02", "mine": True}), encoding="utf-8")
        (p.out / "briefs").mkdir()
        (p.out / "briefs" / f"{cls.kept}.json").write_text(json.dumps({"artist_slug": "tidewool", "week_start": p.week(-5), "title": "Kept Song", "mine": True}),
                                                           encoding="utf-8")
        cls.rec = p.mod("recover")
        cls.before = cls.rec.check(offline=True)
        mac0, ssd0 = files_under(p.root), files_under(p.tmp / "ssd")
        cls.dry = cls.rec.from_ssd(apply=False)
        cls.dry_untouched = (files_under(p.root) == mac0, files_under(p.tmp / "ssd") == ssd0)
        cls.res = cls.rec.from_ssd(apply=True)
        cls.ssd_untouched = files_under(p.tmp / "ssd") == ssd0
        p.reload()
        cls.rec = p.mod("recover")
        cls.after = cls.rec.check(offline=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def sec(self, res: dict, sid: str) -> dict:
        return next(s for s in res["sections"] if s["id"] == sid)

    def item(self, key: str) -> dict:
        return next(s for s in self.res["songs"] if s["key"] == key)

    # ---- 点検
    def test_ssd_was_written_by_the_real_archive(self):
        folder = self.p.ssd / "02_アーティスト" / "TIDEWOOL" / "04_楽曲" / "Low Tide"
        names = [f.name for f in folder.iterdir()]
        self.assertTrue(any("_Suno元データ" in n for n in names), (names, self.sync.stdout[-400:], self.sync.stderr[-400:]))
        self.assertTrue(any(n.endswith(".jpg") for n in names), names)

    def test_check_counts_the_ssd(self):
        inv = self.before["inventory"]
        self.assertEqual(inv["book_rows"], 6)
        self.assertEqual({a["name"]: a["songs"] for a in inv["artists"]}, {"DECLAN FAY": 2, "HOLLOWPLAN": 2, "TIDEWOOL": 2})
        self.assertEqual(sum(x["count"] for x in inv["refs"]["audio"]), 1)
        self.assertEqual(sum(x["count"] for x in inv["refs"]["melody"]), 1, "名前が二重になった MIDI のフォルダも数える")
        self.assertEqual(len(inv["refs"]["audio"]), 1, "解析シート・MIDI のフォルダを「既存楽曲」に数えない")
        self.assertIs(self.sec(self.before, "ssd")["ok"], True)

    def test_check_points_to_one_next_step(self):
        local = self.sec(self.before, "local")
        self.assertIs(local["ok"], False)
        self.assertIn("from-ssd", local["cmd"])
        self.assertTrue(self.before["next"]["what"])
        self.assertIs(self.sec(self.after, "local")["ok"], True, self.sec(self.after, "local")["lines"])

    def test_check_never_prints_key_values(self):
        env = self.p.root / ".env"
        env.write_text(f"NTFY_TOKEN={SECRET}\nSOME_FAKE_KEY={SECRET}\nR2_BUCKET=\n", encoding="utf-8")
        try:
            plain = self.p.run("recover.py", "check", "--offline")
            js = self.p.run("recover.py", "check", "--offline", "--json")
        finally:
            env.unlink()
        self.assertEqual(plain.returncode, 0, plain.stderr[-600:])
        self.assertIn("【次にすること】", plain.stdout)
        self.assertIn("NTFY_TOKEN", plain.stdout, "鍵の名前は出す")
        for out in (plain.stdout, plain.stderr, js.stdout, js.stderr):
            self.assertNotIn(SECRET, out)
        self.assertEqual(len(json.loads(js.stdout)["sections"]), 8)

    # ---- 見るだけ
    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.dry_untouched, (True, True))
        self.assertFalse(self.dry["apply"])
        self.assertGreater(self.dry["written"], 5)
        self.assertNotIn("log", self.dry)

    def test_dry_run_prints_a_plan(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.rec.print_from_ssd(self.dry)
        text = buf.getvalue()
        self.assertIn("まだ何も書いていません", text)
        self.assertIn("After Nights", text)
        self.assertIn("戻せなかったもの", text)

    # ---- 戻す
    def test_live_song_gets_release_record_back(self):
        st = self.p.state(self.live)
        self.assertEqual((st["status"], st["isrc"], st["upc"], st["url"], st["released"]), ("live", ISRC, UPC, URL, "2026-10-21"))
        self.assertEqual(st["restored"]["from"], "ssd")
        meta = self.p.out / "distrokid" / self.p.week(-6) / "hollowplan.metadata.json"
        self.assertTrue(meta.exists(), "登録データ（写し）も一緒にできる")
        self.assertEqual(json.loads(meta.read_text(encoding="utf-8"))["isrc"], ISRC)

    def test_debut_song_is_matched_to_the_kit(self):
        st = self.p.state(self.debut)
        self.assertEqual((st.get("status"), st.get("isrc")), ("live", ISRC))
        images = self.p.root / "debut_kit" / KIT_DAY / "images"
        self.assertTrue((images / "declan_fay_cover.jpg").exists(), "デビュー曲のジャケット（GitHub に入らない画像）")
        self.assertTrue((images / "declan_fay_photo.jpg").exists(), "デビュー用の写真")

    def test_registered_song_keeps_its_release_date(self):
        st = self.p.state(self.reg)
        self.assertEqual(st["status"], "registered")
        self.assertRegex(st["release_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue((self.p.out / "masters" / self.reg / "master.wav").exists(), "登録済みの曲は、配信用の音源も戻す")

    def test_unregistered_song_restarts_from_the_take(self):
        st = self.p.state(self.ready)
        self.assertEqual(st["status"], "imported", "登録前の曲は、Suno の元データから仕上げ直す")
        tdir = self.p.out / "takes" / self.ready
        sel = json.loads((tdir / "selection.json").read_text(encoding="utf-8"))
        self.assertTrue((tdir / sel["decision"]["selected"]).is_file())
        self.assertEqual(st["take"], sel["decision"]["selected"])
        self.assertNotIn("_Suno元データ", st["take"])
        self.assertTrue(Path(st["source"]).is_file(), "元データの場所（SSD）も記録する")
        self.assertFalse((self.p.out / "masters" / self.ready / "master.wav").exists())

    def test_cover_comes_back_marked_as_already_lettered(self):
        cover = self.p.out / "visuals" / "tidewool" / "cover" / self.p.week(-1) / "cover_final.jpg"
        self.assertTrue(cover.exists())
        ct = self.p.mod("cover_text")
        self.assertIs(ct.text_matches(cover, "TIDEWOOL", "Low Tide"), True, "印が無いと、次の取り込みで文字が二重に入る")

    def test_brief_is_rebuilt_from_the_book(self):
        b = json.loads((self.p.out / "briefs" / f"{self.brief}.json").read_text(encoding="utf-8"))
        self.assertEqual((b["artist_slug"], b["week_start"], b["title"]), ("hollowplan", self.p.week(0), "Not Yet"))
        self.assertIn("night pop", b["suno_style_prompt"])
        self.assertIn("[Chorus]", b["suno_lyrics"])
        self.assertEqual(b["restored"]["from"], "ssd")
        self.assertEqual(self.p.state(self.brief), {}, "作曲の前の曲には、記録を作らない")
        keys = {s["key"] for s in self.p.mod("_songs").all_songs()}
        self.assertLessEqual({self.live, self.kept, self.reg, self.ready, self.brief, self.debut}, keys)

    def test_existing_records_are_not_overwritten(self):
        st = self.p.state(self.kept)
        self.assertEqual(st["released"], "2030-02-02")
        self.assertIs(st.get("mine"), True)
        self.assertEqual((st["isrc"], st["upc"], st["url"]), (ISRC, UPC, URL), "空いていた配信の記録だけ足す")
        self.assertNotIn("restored", st)
        b = json.loads((self.p.out / "briefs" / f"{self.kept}.json").read_text(encoding="utf-8"))
        self.assertIs(b.get("mine"), True)
        self.assertNotIn("suno_lyrics", b)
        self.assertIn("そのまま残します", " ".join(self.item(self.kept)["notes"]))

    def test_folders_missing_from_the_book_are_reported(self):
        self.assertTrue(any("Loose One" in l for l in self.res["review"]), self.res["review"])
        self.assertFalse(any("After Nights" in l for l in self.res["review"]), "台帳にある曲は挙げない")
        self.assertEqual(list((self.p.out / "takes").glob("*Loose*")), [], "台帳に無い曲の記録は作らない")

    def test_apply_does_not_write_to_the_ssd(self):
        self.assertTrue(self.ssd_untouched)
        self.assertTrue(list((self.p.out / "restore" / "ssd_book").glob("楽曲管理_*.xlsx")), "台帳の写しを Mac に取る")
        self.assertTrue(Path(self.res["log"]).exists())
        self.assertNotIn("state", json.loads(Path(self.res["log"]).read_text(encoding="utf-8"))["songs"][0])

    def test_second_apply_changes_nothing(self):
        before = files_under(self.p.out / "publish")
        again = self.rec.from_ssd(apply=True)
        self.assertEqual(files_under(self.p.out / "publish"), before)
        self.assertEqual([s["got"] for s in again["songs"]], [[] for _ in again["songs"]])

    def test_reference_sheets_come_back_without_reanalysis(self):
        if self.rid is None:
            self.skipTest("参考曲の部品が読めない環境")
        self.assertEqual(self.res["sheets"].get("error"), "")
        self.assertTrue((self.p.out / "reference_db" / "audio" / f"{self.rid}.json").exists())
        index = json.loads((self.p.out / "reference_db" / "audio_index.json").read_text(encoding="utf-8"))
        self.assertTrue(index[self.rid]["restored"])
        self.assertEqual(self.res["sheets"]["orphans"], 1, "音源の無いシートは読み込まない")

    def test_health_check_accepts_the_restored_state(self):
        health = self.p.mod("health")
        with contextlib.redirect_stdout(io.StringIO()):
            found = health.check()
        bad = [f for f in found if f["id"].startswith(("take:", "title:", "reldate:"))]
        self.assertEqual(bad, [])

    # ---- クラウド
    def test_cloud_skips_what_has_no_key(self):
        r = self.p.run("recover.py", "cloud")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("R2 の予備：飛ばします", r.stdout)
        self.assertIn("Notion：飛ばします", r.stdout)


class RecoverPartsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project(ssd=False)
        cls.rec = cls.p.mod("recover")

    @classmethod
    def tearDownClass(cls):
        cls.p.close()

    def test_xlsx_reader_without_openpyxl(self):
        rows = [["曲 ID", "曲名", "BPM", "歌詞"], ["2026-11-02_tidewool", "A & B <C>", 96, "[Verse]\nline one\nline two"], ["PDR-001", "Side B", None, ""]]
        for shared in (False, True):
            f = self.p.tmp / f"book_{shared}.xlsx"
            write_xlsx(f, "楽曲", rows, shared=shared)
            got = self.rec._xlsx_rows(f, "楽曲")
            self.assertEqual(got[0], rows[0])
            self.assertEqual(got[1][:2], rows[1][:2])
            self.assertEqual(got[1][2], 96.0)
            self.assertEqual(got[1][3], rows[1][3])
            self.assertEqual(got[2][:2], ["PDR-001", "Side B"])

    def test_day_reads_every_date_shape(self):
        d = self.rec._day
        self.assertEqual(d("2026-11-04"), "2026-11-04")
        self.assertEqual(d("2026/11/4"), "2026-11-04")
        self.assertEqual(d(46330), "2026-11-04")   # Excel の日付の通し番号
        self.assertEqual(d(""), "")
        self.assertEqual(d("いちばん早い日"), "")

    def test_finds_the_drive_by_name_with_a_space(self):
        vols = self.p.tmp / "Volumes"
        (vols / "SP DS72" / "Postdog Records" / "01_管理表").mkdir(parents=True)
        (vols / "Macintosh HD").mkdir()
        old_vols, old_env = self.rec.VOLUMES, self.rec.os.environ.pop("ARCHIVE_ROOT", None)
        self.rec.VOLUMES = vols
        try:
            drive, base, _how = self.rec.find_ssd()
            self.assertEqual((drive.name, base.name), ("SP DS72", "Postdog Records"))
            shutil.rmtree(vols / "SP DS72" / "Postdog Records")
            drive, base, how = self.rec.find_ssd()
            self.assertIsNone(base)
            self.assertIn("Postdog Records", how)
        finally:
            self.rec.VOLUMES = old_vols
            if old_env is not None:
                self.rec.os.environ["ARCHIVE_ROOT"] = old_env

    def test_without_ssd_everything_explains_itself(self):
        res = self.rec.check(offline=True)
        ssd = next(s for s in res["sections"] if s["id"] == "ssd")
        self.assertIs(ssd["ok"], False)
        got = self.rec.from_ssd(apply=True)
        self.assertIn("error", got)
        self.assertFalse((self.p.out / "publish" / "state").exists())

    def test_dashboard_buttons_point_to_recover(self):
        webapp = self.p.mod("webapp")
        for action, arg in (("recover_check", "check"), ("recover_ssd", "from-ssd"), ("recover_ssd_apply", "--apply"), ("recover_cloud", "cloud")):
            cmd, label = webapp.command_for(action, {})
            self.assertTrue(str(cmd[1]).endswith("recover.py"))
            self.assertIn(arg, cmd)
            self.assertTrue(label)
        self.assertNotIn("--apply", webapp.command_for("recover_ssd", {})[0])
        self.assertIn('data-act="recover_ssd_apply" class="ghost" data-confirm=', webapp.PAGE)


class SetupScriptTest(unittest.TestCase):
    SCRIPT = REPO / "setup_mac.sh"

    def test_script_is_valid_bash(self):
        r = subprocess.run(["bash", "-n", str(self.SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_script_never_touches_keys_or_runs_as_admin(self):
        code = "\n".join(l for l in self.SCRIPT.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn(".env", code)
        self.assertNotRegex(code, r"(^|[\s;&|(])sudo\s")
        self.assertNotIn("setup_keys", code)

    def test_check_mode_installs_nothing(self):
        r = subprocess.run(["bash", str(self.SCRIPT), "--check"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("調べるだけ", r.stdout)
        self.assertIn("立て直しの点検", r.stdout)
        self.assertNotIn("入れています", r.stdout)


if __name__ == "__main__":
    unittest.main()
