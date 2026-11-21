"""
試験用の「使い捨てのレーベル」を作る部品（決定 173）

本物の out/・.env・SSD・ダウンロードには一切触らない。一時フォルダに次の並びを作り、scripts/ の写しをそこで動かす：

    <tmp>/project/scripts/    … いまの scripts/ の写し（写しなので ROOT＝<tmp>/project になる）
    <tmp>/project/templates/  … 設定書の写し（archive は除く）
    <tmp>/project/out/        … 空（曲を足すと増える）
    <tmp>/home/               … HOME（~/Downloads などはここ）
    <tmp>/ssd/Postdog Records … SSD の代わり（ssd=False なら「SSD が無い」状態）
    <tmp>/local/              … ローカル保存（一時保存）

使い方（どの試験も同じ形）：

    from _fixture import Project

    class T(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.p = Project(ssd=True)               # debuted=("hollowplan",) のように、デビュー済みの組を決められる
            cls.key = cls.p.weekly("hollowplan", cls.p.week(-2), "After Nights", stage="ready")
        @classmethod
        def tearDownClass(cls):
            cls.p.close()
        def test_x(self):
            publish = self.p.mod("publish")         # 写しの scripts から読み込む（本物の scripts は読まない）

  Project.mod(name)                 … 写しの scripts の部品を読み込む（同時に使える Project は 1 つ。切り替えると読み込み直す）
  Project.week(n)                   … 今週の月曜から n 週ずらした月曜（"YYYY-MM-DD"）
  Project.weekly(slug, week, title, stage, days_ago=0, **brief)   … 毎週の曲を 1 曲、その段階まで作る。戻り値は曲のキー
  Project.debut(slug, title, stage, days_ago=0)                   … デビュー曲を 1 曲（作業セット debut_kit/<日付>/）
  Project.irregular(slug, week, title, stage)                     … 曲の案から作曲へ進めた不定期の曲
  Project.song(key) / state(key) / set_state(key, **kw)           … 曲と進み具合
  Project.drop(name, where, age=120, sub="")                       … 受け口に音源を置く（where：ssd_inbox・dl_inbox・dl_top）
  Project.artist(slug) / edit_artist(slug, fn)                     … 設定書を読む・書き換える
  Project.run(script, *args)        … 写しの scripts を別のプロセスで動かす（CompletedProcess）
  Project.wav(path, sec, freq) / jpg(path, px)                     … 小さい音源・画像を作る

  段階（stage）：brief（ブリーフだけ）→ imported（取り込み済み）→ mastered（音量を整えた・ジャケット待ち）
                → ready（ジャケットと下書きと公開セット）→ registered（DistroKid 登録済み）→ live（配信中）
"""
from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent          # music-label/
STAGES = ["brief", "imported", "mastered", "ready", "registered", "live"]
KIT_DAY = "2026-10-11"
FAKE_SPOTIFY = {"declan_fay": "1111111111111111111111", "hollowplan": "2222222222222222222222", "tidewool": "3333333333333333333333",
                "nao_easterly": "4444444444444444444444", "soft_cantilever": "5555555555555555555555"}
# 試験の間は外す環境変数（本物の鍵・本物の置き場所を使わない）
_DROP_ENV = ("SUNO_INBOX", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
             "NOTION_TOKEN", "NOTION_PARENT_PAGE", "NOTION_API_BASE", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
             "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OWNER_LEGAL_NAME", "WEBAPP_KEY", "WEBAPP_LAN", "YOUTUBE_CLIENT_ID",
             "IG_ACCESS_TOKEN", "TIKTOK_CLIENT_KEY", "POSTDOG_AUTOCONTINUE")
_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720"
    "222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405"
    "060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282"
    "090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495"
    "969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008"
    "010100003f00fbfcffd9")


def script_names() -> set[str]:
    return {p.stem for p in (REPO / "scripts").glob("*.py")}


class Project:
    _active: "Project | None" = None

    def __init__(self, ssd: bool = True, debuted: tuple = ("declan_fay", "hollowplan"), keep: tuple = ("declan_fay", "hollowplan", "tidewool")):
        self.tmp = Path(tempfile.mkdtemp(prefix="postdog-test-"))
        self.root = self.tmp / "project"
        self.scripts = self.root / "scripts"
        self.out = self.root / "out"
        self.home = self.tmp / "home"
        self.local = self.tmp / "local"
        self.ssd = (self.tmp / "ssd" / "Postdog Records") if ssd else None
        shutil.copytree(REPO / "scripts", self.scripts, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "templates", self.root / "templates", ignore=shutil.ignore_patterns("archive"))
        # 写しの中だけ：Mac の通知・ブラウザの窓を出さない（本物の部品は書き換えない）
        with open(self.scripts / "_common.py", "a", encoding="utf-8") as f:
            f.write("\n\nif os.environ.get('POSTDOG_TEST'):\n    def notify(title, message):   # 試験の間は通知を出さない\n        return None\n")
        for d in (self.out, self.home / "Downloads", self.local, self.root / "debut_kit"):
            d.mkdir(parents=True, exist_ok=True)
        if self.ssd:
            self.ssd.mkdir(parents=True)
        for p in (self.root / "templates" / "artists").glob("*.json"):
            if p.stem.startswith("_"):
                continue
            if p.stem not in keep:
                p.unlink()
                continue
            a = json.loads(p.read_text(encoding="utf-8"))
            dist = a.setdefault("distribution", {})
            if p.stem in debuted:   # デビュー済み：Spotify のページがある
                dist["spotify_uri"] = f"spotify:artist:{FAKE_SPOTIFY[p.stem]}"
                dist["artist_pages"] = {"spotify": f"https://open.spotify.com/artist/{FAKE_SPOTIFY[p.stem]}",
                                        "apple_music": f"https://music.apple.com/jp/artist/{p.stem}/123456789",
                                        "youtube_music": "https://www.youtube.com/channel/UC" + FAKE_SPOTIFY[p.stem]}
            else:
                dist.pop("spotify_uri", None)
                dist.pop("artist_pages", None)
            a.pop("retire", None)
            a["name_status"] = "checked"
            p.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._env_before: dict[str, str | None] = {}
        self._mods_before: dict = {}
        self._path_before = list(sys.path)
        self.activate()

    # ------------------------------------------------------------------ 切り替え
    def env(self) -> dict[str, str]:
        e = {"HOME": str(self.home), "LOCAL_ARCHIVE": str(self.local), "POSTDOG_TEST": "1", "PYTHONDONTWRITEBYTECODE": "1",
             "POSTDOG_BROWSER_PROFILE": str(self.tmp / "browser-profile"),
             "ARCHIVE_ROOT": str(self.ssd) if self.ssd else str(self.tmp / "no-such-drive" / "x" / "Postdog Records")}
        return e

    def activate(self) -> "Project":
        """この Project の scripts を読み込み先にする。前の状態（読み込み済みの本物の部品・sys.path・環境変数）は覚えておき、close で戻す
        （ほかの試験が本物の scripts を読み込んでいても、互いに邪魔しないように）"""
        if Project._active is self:
            return self
        if Project._active is not None:
            Project._active.deactivate()
        names = script_names() | {"health"}
        self._mods_before = {n: sys.modules[n] for n in names if n in sys.modules}
        self._path_before = list(sys.path)
        _purge()
        sys.path[:] = [p for p in sys.path if not p.rstrip("/").endswith("/scripts")]
        sys.path.insert(0, str(self.scripts))
        for k in _DROP_ENV:
            self._env_before.setdefault(k, os.environ.get(k))
            os.environ.pop(k, None)
        for k, v in self.env().items():
            self._env_before.setdefault(k, os.environ.get(k))
            os.environ[k] = v
        Project._active = self
        return self

    def deactivate(self) -> None:
        if Project._active is not self:
            return
        for k, v in self._env_before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._env_before = {}
        _purge()
        sys.modules.update(self._mods_before)
        sys.path[:] = self._path_before
        Project._active = None

    def mod(self, name: str):
        self.activate()
        m = importlib.import_module(name)
        f = getattr(m, "__file__", "") or ""
        assert str(self.scripts) in f, f"{name} が写しではなく {f} から読まれました"
        return m

    def reload(self) -> None:
        """読み込んだ部品を捨てる（設定書・環境変数を変えたあと、読み込み時に決まる値をやり直したいとき）"""
        _purge()

    def close(self) -> None:
        self.deactivate()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run(self, script: str, *args: str, timeout: int = 120, env: dict | None = None) -> subprocess.CompletedProcess:
        e = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
        e.update(self.env())
        e.update(env or {})
        return subprocess.run([sys.executable, str(self.scripts / script), *args], cwd=self.root, capture_output=True, text=True,
                              timeout=timeout, env=e)

    # ------------------------------------------------------------------ 小道具
    @staticmethod
    def week(n: int = 0) -> str:
        t = date.today()
        return (t - timedelta(days=t.weekday()) + timedelta(weeks=n)).isoformat()

    @staticmethod
    def wav(path: Path, sec: float = 1.0, freq: float = 440.0, rate: int = 22050) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        n = int(sec * rate)
        data = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(data)
        return path

    @staticmethod
    def jpg(path: Path, px: int = 64, color=(120, 60, 200)) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
            Image.new("RGB", (px, px), color).save(path, "JPEG")
        except Exception:  # noqa: BLE001  画像の部品が無い・壊れているときは、最小の JPEG
            path.write_bytes(_TINY_JPEG + bytes(color))
        return path

    def artist(self, slug: str) -> dict:
        return json.loads((self.root / "templates" / "artists" / f"{slug}.json").read_text(encoding="utf-8"))

    def edit_artist(self, slug: str, fn) -> None:
        p = self.root / "templates" / "artists" / f"{slug}.json"
        a = json.loads(p.read_text(encoding="utf-8"))
        fn(a)
        p.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def song(self, key: str) -> dict:
        s = self.mod("_songs").by_id(key)
        assert s is not None, f"曲 {key} が見つかりません"
        return s

    def state(self, key: str) -> dict:
        p = self.out / "publish" / "state" / f"{key}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def set_state(self, key: str, **kw) -> dict:
        st = self.state(key)
        for k, v in kw.items():
            if v is None:
                st.pop(k, None)
            else:
                st[k] = v
        p = self.out / "publish" / "state" / f"{key}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        return st

    def drop(self, name: str, where: str = "dl_inbox", age: int = 120, sub: str = "", sec: float = 0.3, freq: float = 330.0) -> Path:
        """受け口に音源を置く。age 秒前に作ったことにする（15 秒以内のファイルは「ダウンロードの途中」として見ない作りのため）"""
        base = {"dl_inbox": self.home / "Downloads" / "SUNO楽曲", "dl_top": self.home / "Downloads",
                "ssd_inbox": (self.ssd / "楽曲情報") if self.ssd else None}[where]
        assert base is not None, "SSD の無い Project には ssd_inbox は使えません"
        f = base / sub / name if sub else base / name
        self.wav(f, sec=sec, freq=freq)
        t = time.time() - age
        os.utime(f, (t, t))
        return f

    # ------------------------------------------------------------------ 曲を作る
    def weekly(self, slug: str, week: str, title: str, stage: str = "brief", days_ago: int = 0, **brief) -> str:
        key = f"{week}_{slug}"
        rel = (date.fromisoformat(week) + timedelta(days=16)).isoformat()
        b = {"artist_slug": slug, "week_start": week, "title": title, "title_candidates": [title, f"{title} (Alt)"],
             "suno_style_prompt": "night pop, 100 BPM, warm analog synths", "suno_lyrics": "[Verse]\nOne line here\n[Chorus]\nSing it again\n",
             "language": "English", "release_at": f"{rel}T21:00:00+00:00", "bpm": 100}
        b.update(brief)
        if not b.get("suno_style_prompt"):
            b.pop("suno_style_prompt", None)
        p = self.out / "briefs" / f"{key}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._advance_to(key, stage, days_ago)
        return key

    def irregular(self, slug: str, week: str, title: str, stage: str = "brief", days_ago: int = 0) -> str:
        """曲の案（out/irregular/drafts/）から作曲へ進めた曲"""
        draft_id = f"{slug}-{datetime.now():%Y%m%d%H%M%S}"
        ir = {"id": draft_id, "status": "accepted", "accepted_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "draft": draft_id}
        key = self.weekly(slug, week, title, "brief", irregular=ir, sprint={"id": "irregular", "release": "asap"})
        d = json.loads((self.out / "briefs" / f"{key}.json").read_text(encoding="utf-8"))
        d["irregular"] = {**ir, "song_id": key}
        dp = self.out / "irregular" / "drafts" / f"{draft_id}.json"
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._advance_to(key, stage, days_ago)
        return key

    def debut(self, slug: str, title: str, stage: str = "brief", days_ago: int = 0, images: bool = True) -> str:
        kit = self.root / "debut_kit" / KIT_DAY
        kj = kit / "kit.json"
        d = json.loads(kj.read_text(encoding="utf-8")) if kj.exists() else {"label": "Postdog Records", "made": KIT_DAY, "songs": []}
        d["songs"] = [s for s in d["songs"] if s["slug"] != slug] + [{
            "slug": slug, "artist": self.artist(slug).get("name", slug), "title": title, "title_alt": [f"{title} II"], "bpm": 96,
            "language": "English", "instrumental": False, "genre": ["Electronic", "Pop"], "style": "synthwave, 96 BPM",
            "exclude": "EDM drop", "lyrics": "[Verse]\nA line\n[Chorus]\nAnother line\n"}]
        kit.mkdir(parents=True, exist_ok=True)
        kj.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if images:
            self.jpg(kit / "images" / f"{slug}_photo.jpg")
            self.jpg(kit / "images" / f"{slug}_cover.jpg")
        key = f"{KIT_DAY}_{slug}"
        self._advance_to(key, stage, days_ago)
        return key

    def _advance_to(self, key: str, stage: str, days_ago: int) -> None:
        assert stage in STAGES, stage
        n = STAGES.index(stage)
        if n == 0:
            return
        songs = self.mod("_songs")
        publish = self.mod("publish")
        song = self.song(key)
        when = datetime.now() - timedelta(days=days_ago)
        at = when.isoformat(timespec="seconds")
        day = when.date().isoformat()
        st: dict = {}
        # 取り込み済み：選んだテイク
        tdir = self.out / "takes" / key
        take = self.wav(tdir / f"{songs.audio_stem(song, day)}.wav", sec=1.0)
        sel = {"decision": {"selected": take.name, "by": "試験", "at": at}, "takes": [{"take": take.name, "path": str(take)}], "source": str(take)}
        st.update({"status": "imported", "take": take.name, "source": str(take), "imported_at": at, "generated_on": day})
        if n >= 2:   # 音量を整えた
            m = self.wav(self.out / "masters" / key / "master.wav", sec=1.0, freq=450)
            (m.with_suffix(".report.json")).write_text(json.dumps({"ok": True, "after": {"lufs": -14.0, "true_peak_db": -1.0}}), encoding="utf-8")
            sel["master"] = {"path": str(m), "ok": True, "lufs": -14.0, "true_peak_db": -1.0}
            st["status"] = "waiting_cover"
        (tdir / "selection.json").write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
        songs.save_state(song, st)
        if n >= 3:   # ジャケット・下書き・公開セット
            if song["kind"] == "debut":
                self.jpg(song["kit"] / "images" / f"{song['slug']}_cover.jpg")
            else:
                cd = self.out / "visuals" / song["slug"] / "cover" / song["week"]
                self.jpg(cd / "cover_final.jpg")
                (cd / "selection_cover.json").write_text(json.dumps({"chosen": 1, "by": "試験"}), encoding="utf-8")
            folder, audio, cover = publish.publish_folder(song)
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.out / "masters" / key / "master.wav", folder / audio)
            if cover:
                shutil.copy2(songs.cover_source(song), folder / cover)
            st.update({"status": "ready", "ready_at": at})
            try:
                publish.write_draft(song, st)
            except Exception:  # noqa: BLE001  下書きの部品が動かない環境でも、下書きのファイルだけは置く
                d = self.out / "publish" / key
                d.mkdir(parents=True, exist_ok=True)
                (d / "DistroKid下書き.md").write_text(f"# {song['title']}\n", encoding="utf-8")
                (d / "DistroKid下書き.json").write_text("{}", encoding="utf-8")
            songs.save_state(song, st)
        if n >= 4:
            rel = (when.date() + timedelta(days=14)).isoformat()
            st.update({"status": "registered", "registered_at": at, "release_date": rel})
            songs.save_state(song, st)
        if n >= 5:
            st.update({"status": "live", "released": day, "release_date": day, "isrc": "QZTEST2600001", "upc": "000000000001",
                       "url": "https://open.spotify.com/track/0000000000000000000000"})
            songs.save_state(song, st)


def _purge() -> None:
    for name in script_names() | {"health"}:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
