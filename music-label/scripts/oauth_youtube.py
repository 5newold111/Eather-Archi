#!/usr/bin/env python3
"""
YouTube に投稿するための「更新用トークン（refresh token）」を取得して .env に保存する（鍵の一括設定のときに 1 回だけ）。

  1. .env に YOUTUBE_CLIENT_ID と YOUTUBE_CLIENT_SECRET を入れておく（set_key.py で）
  2. このスクリプトを実行するとブラウザが開く → 投稿したい YouTube チャンネルの Google アカウントで「許可」
  3. 自動で .env に YOUTUBE_REFRESH_TOKEN が保存される（画面には表示しない）

使い方
  python scripts/oauth_youtube.py                 # 共通のチャンネル
  python scripts/oauth_youtube.py --for light     # LUMENLINE 専用チャンネル → YOUTUBE_REFRESH_TOKEN__LIGHT
  python scripts/oauth_youtube.py --for focus     # MONOTASK（子レーベル）用 → YOUTUBE_REFRESH_TOKEN__FOCUS

用語
  OAuth          … パスワードを渡さずに「投稿だけ許可する」ための仕組み
  refresh token  … 毎回ログインしなくても投稿用の一時トークンを取り直せる長期の合鍵。.env の外に出さない
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, load_dotenv, ssl_context  # noqa: E402

load_dotenv()
ENV = ROOT / ".env"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def save_env(name: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [l for l in lines if not l.startswith(name + "=")] + [f"{name}={value}"]
    ENV.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    try:
        ENV.chmod(0o600)
    except OSError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="YouTube の refresh token を取得して .env に保存")
    ap.add_argument("--for", dest="target", help="組（light など）かレーベル（focus など）。省略で共通")
    args = ap.parse_args()
    suffix = f"__{args.target.upper()}" if args.target else ""
    cid = os.environ.get("YOUTUBE_CLIENT_ID" + suffix) or os.environ.get("YOUTUBE_CLIENT_ID")
    csec = os.environ.get("YOUTUBE_CLIENT_SECRET" + suffix) or os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not (cid and csec):
        sys.exit("[停止] .env に YOUTUBE_CLIENT_ID と YOUTUBE_CLIENT_SECRET がありません。\n"
                 "       python scripts/set_key.py YOUTUBE_CLIENT_ID  と  python scripts/set_key.py YOUTUBE_CLIENT_SECRET  で入れてください\n"
                 "       （取得手順：docs/09_auth_batch.md の YouTube の節）")

    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                got["code"] = q["code"][0]
                msg = "許可を受け取りました。このタブは閉じて構いません。"
            else:
                got["error"] = q.get("error", ["不明"])[0]
                msg = "許可を受け取れませんでした。ターミナルを確認してください。"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<p style='font-family:sans-serif'>{msg}</p>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("▶ ブラウザで Google の許可画面を開きます。投稿したいチャンネルのアカウントで「許可」を押してください")
    print(f"   開かない場合はこの URL をブラウザに貼ってください:\n   {url}")
    webbrowser.open(url)
    while not got:
        srv.handle_request()
    if "code" not in got:
        sys.exit(f"[停止] 許可されませんでした（{got.get('error')}）")

    print("▶ 許可コードを更新用トークンに交換しています")
    body = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": csec,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body),
                                timeout=60, context=ssl_context()) as r:
        tok = json.loads(r.read())
    if "refresh_token" not in tok:
        sys.exit("[停止] 更新用トークンが返ってきませんでした。Google アカウントの「サードパーティのアクセス」から"
                 "このアプリを一度削除して、もう一度実行してください")
    name = "YOUTUBE_REFRESH_TOKEN" + suffix
    save_env(name, tok["refresh_token"])
    print(f"▶ {name} を .env に保存しました（値は表示しません）")


if __name__ == "__main__":
    main()
