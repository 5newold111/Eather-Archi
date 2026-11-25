"""試験用：R2（S3 互換）の代わりになる手元の偽サーバー。署名（SigV4）を本物と同じ計算で確かめる（決定 174）"""
from __future__ import annotations

import hashlib
import re
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
sys.path.insert(0, SCRIPTS)
from backup_r2 import sign_v4  # noqa: E402


def pin(*mods) -> None:
    """ほかの試験が、読み込んだ部品を捨てたり探す場所を変えたりしていても、この試験の部品が使われるようにする"""
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    for m in mods:
        sys.modules[m.__name__] = m


class MockS3:
    def __init__(self, port: int, bucket: str = "label-backup", ak: str = "AKTEST", sk: str = "SKTEST"):
        self.port, self.bucket, self.ak, self.sk = port, bucket, ak, sk
        self.objects: dict[str, dict] = {}   # key → {"body": bytes, "modified": datetime}
        self.requests: list[tuple[str, str]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _fail(self, code: int, msg: str):
                b = f"<Error><Code>{code}</Code><Message>{msg}</Message></Error>".encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _signed(self, body: bytes) -> bool:
                auth = self.headers.get("Authorization", "")
                m = re.match(r"AWS4-HMAC-SHA256 Credential=([^/]+)/.*SignedHeaders=([^,]+), Signature=([0-9a-f]+)", auth)
                if not m or m.group(1) != outer.ak:
                    return False
                u = urllib.parse.urlsplit(self.path)
                hdr = {h: self.headers.get(h, "") for h in m.group(2).split(";") if h != "host"}
                if hdr.get("x-amz-content-sha256") != hashlib.sha256(body).hexdigest():
                    return False
                want = sign_v4(self.command, self.headers.get("Host", ""), urllib.parse.unquote(u.path), u.query, hdr,
                               hdr["x-amz-content-sha256"], outer.ak, outer.sk, "auto", hdr.get("x-amz-date", ""))
                return want == auth

            def _key(self) -> str | None:
                path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
                if not path.startswith(f"/{outer.bucket}"):
                    return None
                return path[len(outer.bucket) + 2:]

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(("PUT", self.path))
                if not self._signed(body):
                    return self._fail(403, "SignatureDoesNotMatch")
                key = self._key()
                if key is None:
                    return self._fail(404, "NoSuchBucket")
                outer.put(key, body)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                if not self._signed(b""):
                    return self._fail(403, "SignatureDoesNotMatch")
                key = self._key()
                if key is None:
                    return self._fail(404, "NoSuchBucket")
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if key == "" and q.get("list-type") == ["2"]:
                    return self._list(q)
                o = outer.objects.get(key)
                if not o:
                    return self._fail(404, "NoSuchKey")
                self.send_response(200)
                self.send_header("Content-Length", str(len(o["body"])))
                self.end_headers()
                self.wfile.write(o["body"])

            def _list(self, q):
                prefix = (q.get("prefix") or [""])[0]
                limit = int((q.get("max-keys") or ["1000"])[0])
                start = int((q.get("continuation-token") or ["0"])[0])
                keys = sorted(k for k in outer.objects if k.startswith(prefix))
                page, rest = keys[start:start + limit], keys[start + limit:]
                items = "".join(
                    f"<Contents><Key>{escape(k)}</Key><LastModified>{outer.objects[k]['modified'].strftime('%Y-%m-%dT%H:%M:%S.000Z')}</LastModified>"
                    f"<ETag>&quot;{hashlib.md5(outer.objects[k]['body']).hexdigest()}&quot;</ETag><Size>{len(outer.objects[k]['body'])}</Size></Contents>"
                    for k in page)
                more = f"<IsTruncated>true</IsTruncated><NextContinuationToken>{start + limit}</NextContinuationToken>" if rest else "<IsTruncated>false</IsTruncated>"
                b = (f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                     f"<Name>{outer.bucket}</Name><Prefix>{escape(prefix)}</Prefix>{items}{more}</ListBucketResult>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def put(self, key: str, body: bytes, modified: datetime | None = None) -> None:
        self.objects[key] = {"body": body, "modified": modified or datetime.now(timezone.utc).replace(microsecond=0)}

    def start(self) -> "MockS3":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def env(self) -> dict:
        return {"R2_ENDPOINT": f"http://127.0.0.1:{self.port}", "R2_ACCOUNT_ID": "acc", "R2_BUCKET": self.bucket,
                "R2_ACCESS_KEY_ID": self.ak, "R2_SECRET_ACCESS_KEY": self.sk}
