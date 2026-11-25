"""Notion に写す（notion_sync.py。決定 167）：本物と同じ決まり（型・長さ・つながり・画像の受け取り）で動く偽サーバーで確かめる"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import time
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

from _fixture import Project
from _servers import serve

PARENT = "11111111-2222-3333-4444-555555555555"
TOKEN = "ntn_test"


class Notion:
    """偽の Notion の中身"""
    def __init__(self):
        self.db: dict = {}
        self.pages: dict = {}
        self.files: dict = {}
        self.log: list = []

    def check(self, dbid: str, k: str, v: dict) -> None:
        sch = self.db[dbid]["properties"]
        if k not in sch:
            raise ValueError(f"{k} is not a property that exists")
        kind, vk = next(iter(sch[k])), next(iter(v))
        if vk != kind:
            raise ValueError(f"{k} is expected to be {kind}, got {vk}")
        val = v[vk]
        if kind in ("title", "rich_text"):
            if len(val) > 100:
                raise ValueError("too many rich text items")
            if any(len(t["text"]["content"]) > 2000 for t in val):
                raise ValueError("content length > 2000")
        if kind == "select" and val is not None and "," in val["name"]:
            raise ValueError("select name has comma")
        if kind == "relation" and any(r["id"] not in self.pages for r in val):
            raise ValueError("relation page not found")
        if kind == "files":
            for f in val:
                fid = f["file_upload"]["id"]
                if self.files.get(fid, {}).get("status") != "uploaded":
                    raise ValueError("file upload not uploaded")
                self.files[fid]["status"] = "attached"
        if kind == "url" and val is not None and not str(val).startswith("http"):
            raise ValueError("bad url")
        if kind == "date" and val is not None:
            date.fromisoformat(val["start"])


def handler(n: Notion):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def out(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def err(self, code, msg):
            self.out(code, {"object": "error", "status": code, "message": msg})

        def ok_auth(self) -> bool:
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self.err(401, "API token is invalid.")
                return False
            if self.headers.get("Notion-Version") != "2022-06-28":
                self.err(400, "bad version")
                return False
            return True

        def body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def do_GET(self):
            if not self.ok_auth():
                return
            n.log.append(("GET", self.path))
            m = re.match(r"/v1/databases/([\w-]+)$", self.path)
            if m and m[1] in n.db:
                return self.out(200, {"object": "database", "id": m[1], "in_trash": n.db[m[1]].get("in_trash", False)})
            self.err(404, "Could not find object")

        def do_POST(self):
            if not self.ok_auth():
                return
            raw = self.body()
            n.log.append(("POST", self.path))
            if self.path == "/v1/databases":
                b = json.loads(raw)
                if b["parent"]["page_id"] != PARENT:
                    return self.err(404, "Could not find page with ID")
                if any("relation" in v and v["relation"]["database_id"] not in n.db for v in b["properties"].values()):
                    return self.err(400, "relation db missing")
                if sum(1 for v in b["properties"].values() if "title" in v) != 1:
                    return self.err(400, "exactly one title property")
                i = str(uuid.uuid4())
                n.db[i] = {"title": b["title"][0]["text"]["content"], "properties": b["properties"]}
                return self.out(200, {"object": "database", "id": i, "url": f"https://www.notion.so/{i.replace('-', '')}"})
            if self.path == "/v1/pages":
                b = json.loads(raw)
                dbid = b["parent"]["database_id"]
                if dbid not in n.db or n.db[dbid].get("in_trash"):
                    return self.err(404, "Could not find database")
                try:
                    for k, v in b["properties"].items():
                        n.check(dbid, k, v)
                except (ValueError, KeyError) as e:
                    return self.err(400, str(e))
                i = str(uuid.uuid4())
                n.pages[i] = {"parent": dbid, "properties": b["properties"]}
                return self.out(200, {"object": "page", "id": i})
            if self.path == "/v1/file_uploads":
                b = json.loads(raw)
                if b.get("mode") != "single_part":
                    return self.err(400, "mode")
                i = str(uuid.uuid4())
                n.files[i] = {"status": "pending", "name": b["filename"]}
                return self.out(200, {"object": "file_upload", "id": i, "status": "pending"})
            m = re.match(r"/v1/file_uploads/([\w-]+)/send$", self.path)
            if m:
                ct = self.headers.get("Content-Type", "")
                if not ct.startswith("multipart/form-data; boundary=") or b'name="file"' not in raw:
                    return self.err(400, "bad multipart")
                if len(raw) > 5 * 1024 * 1024 + 2000:
                    return self.err(400, "too large")
                n.files[m[1]].update(status="uploaded", size=len(raw))
                return self.out(200, {"object": "file_upload", "id": m[1], "status": "uploaded"})
            self.err(404, "nope")

        def do_PATCH(self):
            if not self.ok_auth():
                return
            b = json.loads(self.body())
            n.log.append(("PATCH", self.path))
            m = re.match(r"/v1/pages/([\w-]+)$", self.path)
            if m:
                pg = n.pages.get(m[1])
                if not pg:
                    return self.err(404, "Could not find page")
                if pg.get("archived"):
                    return self.err(400, "Can't edit block that is archived. You must unarchive the block before editing.")
                try:
                    for k, v in b["properties"].items():
                        n.check(pg["parent"], k, v)
                except (ValueError, KeyError) as e:
                    return self.err(400, str(e))
                pg["properties"].update(b["properties"])
                return self.out(200, {"object": "page", "id": m[1]})
            m = re.match(r"/v1/databases/([\w-]+)$", self.path)
            if m and m[1] in n.db:
                n.db[m[1]]["properties"].update(b["properties"])
                return self.out(200, {"object": "database", "id": m[1]})
            self.err(404, "nope")
    return H


class NotionSyncTest(unittest.TestCase):
    def setUp(self):
        self.n = Notion()
        self.srv, port = serve(handler(self.n))
        self.p = p = Project(ssd=False, debuted=("declan_fay", "hollowplan"))
        self.k1 = p.weekly("hollowplan", p.week(-1), "After Nights", stage="ready",
                           suno_lyrics="[Verse]\n" + "la la la, with a comma, and more\n" * 400)      # 2000 字を超える歌詞
        self.k2 = p.weekly("hollowplan", p.week(-2), "Handed Round", stage="brief")
        self.k3 = p.weekly("declan_fay", p.week(-1), "Side C", stage="live")
        os.environ.update({"NOTION_API_BASE": f"http://127.0.0.1:{port}/v1", "NOTION_TOKEN": TOKEN,
                           "NOTION_PARENT_PAGE": "https://www.notion.so/Postdog-Records-11111111222233334444555555555555?pvs=4"})
        p.reload()
        self.ns = p.mod("notion_sync")
        self.ns.WAIT = 0

    def tearDown(self):
        for k in ("NOTION_API_BASE", "NOTION_TOKEN", "NOTION_PARENT_PAGE", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
            os.environ.pop(k, None)
        self.p.close()
        self.srv.shutdown()
        self.srv.server_close()

    def sync(self, **kw) -> dict:
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ns.sync(**kw)

    def rows(self, title: str) -> list[dict]:
        ids = [k for k, d in self.n.db.items() if d["title"] == title and not d.get("in_trash")]
        return [pg for pg in self.n.pages.values() if pg["parent"] in ids and not pg.get("archived")]

    def song_row(self, key: str) -> dict:
        return next(pg["properties"] for pg in self.rows("楽曲") if pg["properties"]["曲 ID"]["rich_text"][0]["text"]["content"] == key)

    def test_parent_id_forms(self):
        f = self.ns.parent_id
        self.assertEqual(f("https://www.notion.so/Postdog-11111111222233334444555555555555?pvs=4"), PARENT)
        self.assertEqual(f("11111111222233334444555555555555"), PARENT)
        self.assertEqual(f(PARENT), PARENT)
        self.assertEqual(f("https://www.notion.so/no-id-here"), "")

    def test_first_sync_creates_two_tables_and_rows(self):
        c = self.sync()
        self.assertEqual(sorted(d["title"] for d in self.n.db.values()), ["アーティスト", "楽曲"])
        self.assertEqual(len(self.rows("アーティスト")), 3)
        self.assertEqual(len(self.rows("楽曲")), 3)
        self.assertEqual(c, {"created": 6, "updated": 0, "same": 0})
        r = self.song_row(self.k1)
        self.assertEqual(r["工程"]["select"]["name"], "6 登録の準備")
        self.assertEqual(r["流れ"]["select"]["name"], "定期配信")
        self.assertEqual(r["担当"]["select"]["name"], "あなた")
        self.assertEqual(r["制作週"]["date"]["start"], self.p.week(-1))
        self.assertGreater(len(r["歌詞"]["rich_text"]), 1, "長い歌詞は 2000 字ごとに分ける")
        art = {pg["properties"]["slug"]["rich_text"][0]["text"]["content"]: i for i, pg in self.n.pages.items() if "slug" in pg["properties"]}
        self.assertEqual(r["組のページ"]["relation"], [{"id": art["hollowplan"]}], "アーティストの表とつながる")
        self.assertEqual(self.song_row(self.k3)["工程"]["select"]["name"], "8 配信中")
        self.assertEqual(self.song_row(self.k2)["工程"]["select"]["name"], "2 Suno で作曲")
        hp = self.n.pages[art["hollowplan"]]["properties"]
        self.assertEqual(hp["状態"]["select"]["name"], "所属")
        self.assertTrue(hp["Spotify"]["url"].startswith("https://open.spotify.com/artist/"))
        m = json.loads(self.ns.MAP.read_text(encoding="utf-8"))
        self.assertEqual(m["parent"], PARENT)
        self.assertEqual(len(m["pages"]), 6)
        st = self.ns.status()
        self.assertTrue(st["ready"])
        self.assertTrue(st["songs_url"].startswith("https://www.notion.so/"))

    def test_second_sync_changes_nothing(self):
        self.sync()
        writes = lambda: [x for x in self.n.log if x[0] != "GET"]  # noqa: E731
        before = len(writes())
        c = self.sync()
        self.assertEqual(c, {"created": 0, "updated": 0, "same": 6})
        self.assertEqual(len(writes()), before, "変わっていない行は書き直さない")

    def test_cover_is_uploaded_and_reuploaded_only_on_change(self):
        self.sync()
        attached = [f for f in self.n.files.values() if f["status"] == "attached"]
        self.assertEqual(len(attached), 2, "ジャケットのある 2 曲（k1・k3）だけ")
        self.assertEqual(len(self.song_row(self.k1)["ジャケット"]["files"]), 1)
        cover = self.p.out / "visuals" / "hollowplan" / "cover" / self.p.week(-1) / "cover_final.jpg"
        time.sleep(1.1)
        self.p.jpg(cover, color=(5, 200, 5))
        c = self.sync()
        self.assertEqual(c, {"created": 0, "updated": 1, "same": 5})
        self.assertEqual(len([f for f in self.n.files.values() if f["status"] == "attached"]), 3, "変えた 1 枚だけ上げ直す")

    def test_image_over_5mb_is_not_uploaded(self):
        cover = self.p.out / "visuals" / "hollowplan" / "cover" / self.p.week(-1) / "cover_final.jpg"
        cover.write_bytes(os.urandom(6 * 1024 * 1024))     # 画像として読めない 6 MB：小さくできないので上げない
        self.sync()
        self.assertEqual(len(self.n.files), 1, "上がるのは k3 のジャケットだけ")
        self.assertEqual(self.song_row(self.k1)["ジャケット"]["files"], [])
        self.assertEqual(len(self.song_row(self.k3)["ジャケット"]["files"]), 1)
        c = self.sync()
        self.assertEqual(c["same"], 6, "上げられなかった画像を毎回試し直さない")

    def test_archived_row_is_recreated(self):
        self.sync()
        m = json.loads(self.ns.MAP.read_text(encoding="utf-8"))
        pid = m["pages"]["artist:hollowplan"]["id"]
        self.n.pages[pid]["archived"] = True                # Notion で行を消した
        m["pages"]["artist:hollowplan"]["sig"] = "x"        # 書き直しが要る状態にする
        self.ns.MAP.write_text(json.dumps(m), encoding="utf-8")
        c = self.sync()
        self.assertEqual(c["created"], 1)
        self.assertEqual(len(self.rows("アーティスト")), 3)
        new_id = json.loads(self.ns.MAP.read_text(encoding="utf-8"))["pages"]["artist:hollowplan"]["id"]
        self.assertNotEqual(new_id, pid)
        self.assertEqual(self.song_row(self.k1)["組のページ"]["relation"], [{"id": new_id}], "曲のつながりも新しい行に付け替える")

    def test_trashed_table_is_recreated(self):
        self.sync()
        old = next(k for k, d in self.n.db.items() if d["title"] == "楽曲")
        self.n.db[old]["in_trash"] = True                   # Notion で表をゴミ箱に入れた
        c = self.sync()
        self.assertEqual(c, {"created": 3, "updated": 0, "same": 3})
        self.assertEqual(len(self.rows("楽曲")), 3)
        self.assertEqual(len([d for d in self.n.db.values() if d["title"] == "楽曲"]), 2)

    def test_wrong_token_is_reported(self):
        os.environ["NOTION_TOKEN"] = "ntn_wrong"
        with self.assertRaises(self.ns.NotionError) as e:
            self.sync()
        self.assertEqual(e.exception.code, 401)

    def test_dry_run_touches_nothing(self):
        self.sync(dry=True)
        self.assertEqual(self.n.log, [])
        self.assertFalse(self.ns.MAP.exists())

    def test_if_ready_without_keys_is_silent(self):
        r = self.p.run("notion_sync.py", "--if-ready")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))
        r = self.p.run("notion_sync.py")
        self.assertEqual(r.returncode, 1)
        self.assertIn("setup_keys.py --only notion", r.stdout)

    def test_r2_link_shape_and_expiry(self):
        os.environ.update({"R2_ACCOUNT_ID": "acc", "R2_ACCESS_KEY_ID": "AKID", "R2_SECRET_ACCESS_KEY": "SECRET"})
        rel = f"out/masters/{self.k1}/master.wav"
        self.assertIsNone(self.ns.r2_link(rel), "まだ R2 に上がっていないものにはリンクを作らない")
        man = self.p.out / "backup" / "r2_manifest.json"
        man.parent.mkdir(parents=True, exist_ok=True)
        man.write_text(json.dumps({rel: [1, 2]}), encoding="utf-8")
        url, until = self.ns.r2_link(rel)
        self.assertTrue(url.startswith(f"https://acc.r2.cloudflarestorage.com/label-backup/label/{rel}?"))
        self.assertIn("X-Amz-Expires=604800", url)
        self.assertIn("X-Amz-SignedHeaders=host", url)
        self.assertRegex(url, r"X-Amz-Credential=AKID%2F\d{8}%2Fauto%2Fs3%2Faws4_request")
        self.assertRegex(url, r"&X-Amz-Signature=[0-9a-f]{64}$")
        self.assertNotIn("SECRET", url)
        self.assertEqual(until, (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat())
        self.sync()
        self.assertTrue(self.song_row(self.k1)["音源（R2）"]["url"].startswith("https://acc.r2.cloudflarestorage.com/"))
        self.assertIsNone(self.song_row(self.k2)["音源（R2）"]["url"])
        c = self.sync()
        self.assertEqual(c["same"], 6, "リンクは期限が近いときだけつなぎ直す（毎回書き直さない）")


if __name__ == "__main__":
    unittest.main()
