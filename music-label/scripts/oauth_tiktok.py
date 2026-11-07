#!/usr/bin/env python3
"""
TikTok に投稿するための「更新用トークン（refresh token）」を取得して .env に保存する（鍵の一括設定のときに 1 回だけ）。

  前提：TikTok 開発者サイトのアプリで、Login Kit と Content Posting API を有効にし、
        リダイレクト URI（許可のあとに戻ってくる URL）を登録してある

  1. .env に TIKTOK_CLIENT_KEY・TIKTOK_CLIENT_SECRET・TIKTOK_REDIRECT_URI を入れる（set_key.py で）
  2. このスクリプトを実行するとブラウザが開く → 投稿したいアカウントで「許可」
  3. 戻り先が http://localhost:<番号>/… なら自動で受け取る。
     それ以外（https の自分のサイトなど）は、許可のあとにブラウザのアドレス欄に出た URL を丸ごと貼る
  4. .env に TIKTOK_REFRESH_TOKEN が保存される（画面には表示しない）

使い方
  python scripts/oauth_tiktok.py                  # 共通のアカウント
  python scripts/oauth_tiktok.py --for focus      # 子レーベル用 → TIKTOK_REFRESH_TOKEN__FOCUS
  python scripts/oauth_tiktok.py --pkce           # デスクトップ用アプリとして登録した場合（PKCE という追加の確認を使う）

取得する権限：user.info.basic（アカウントの基本情報）、video.publish（投稿）、video.list（投稿した動画の再生数）
"""
from __future__ import annotations

import argparse
import hashlib
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
from _common import load_dotenv, ssl_context, step  # noqa: E402
from oauth_youtube import save_env  # noqa: E402

load_dotenv()
SCOPES = "user.info.basic,video.publish,video.list"


def env(name: str, suffix: str) -> str | None:
    return os.environ.get(name + suffix) or os.environ.get(name)


def catch_locally(redirect: str, state: str) -> dict:
    u = urllib.parse.urlparse(redirect)
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            got.update(code=q["code"][0]) if ok else got.update(error=q.get("error", ["不明"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<p style='font-family:sans-serif'>" + ("許可を受け取りました。このタブは閉じて構いません。" if ok
                              else "許可を受け取れませんでした。ターミナルを確認してください。") + "</p>").encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer((u.hostname or "127.0.0.1", u.port or 80), H)
    while not got:
        srv.handle_request()
    return got


def main() -> None:
    ap = argparse.ArgumentParser(description="TikTok の refresh token を取得して .env に保存")
    ap.add_argument("--for", dest="target", help="組かレーベル。省略で共通")
    ap.add_argument("--pkce", action="store_true", help="デスクトップ用アプリとして登録した場合")
    a = ap.parse_args()
    suffix = f"__{a.target.upper()}" if a.target else ""
    ck, cs, redirect = env("TIKTOK_CLIENT_KEY", suffix), env("TIKTOK_CLIENT_SECRET", suffix), env("TIKTOK_REDIRECT_URI", suffix)
    if not (ck and cs and redirect):
        sys.exit("[停止] .env に TIKTOK_CLIENT_KEY・TIKTOK_CLIENT_SECRET・TIKTOK_REDIRECT_URI が必要です。\n"
                 "       python scripts/set_key.py TIKTOK_CLIENT_KEY  などで入れてください（手順：docs/09_auth_batch.md）")
    state = secrets.token_urlsafe(16)
    params = {"client_key": ck, "scope": SCOPES, "response_type": "code", "redirect_uri": redirect, "state": state}
    verifier = None
    if a.pkce:
        verifier = secrets.token_urlsafe(48)
        # TikTok のデスクトップ用は、確認用の文字列を SHA-256 で 16 進数にしたものを送る
        params.update(code_challenge=hashlib.sha256(verifier.encode()).hexdigest(), code_challenge_method="S256")
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)
    step("ブラウザで TikTok の許可画面を開きます。投稿したいアカウントで「許可」を押してください")
    print(f"   開かない場合はこの URL をブラウザに貼ってください:\n   {url}")
    webbrowser.open(url)

    host = urllib.parse.urlparse(redirect).hostname
    if host in ("localhost", "127.0.0.1"):
        got = catch_locally(redirect, state)
    else:
        back = input("   許可のあと、ブラウザのアドレス欄の URL を丸ごと貼って Enter: ").strip()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
        got = {"code": q["code"][0]} if q.get("state", [""])[0] == state and "code" in q else {"error": "URL に許可コードがない"}
    if "code" not in got:
        sys.exit(f"[停止] 許可されませんでした（{got.get('error')}）")

    step("許可コードを更新用トークンに交換しています")
    form = {"client_key": ck, "client_secret": cs, "code": got["code"], "grant_type": "authorization_code",
            "redirect_uri": redirect}
    if verifier:
        form["code_verifier"] = verifier
    req = urllib.request.Request("https://open.tiktokapis.com/v2/oauth/token/", data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as r:
        tok = json.loads(r.read())
    if "refresh_token" not in tok:
        sys.exit(f"[停止] 更新用トークンが返ってきませんでした（{tok.get('error_description') or tok.get('error') or tok}）")
    save_env("TIKTOK_REFRESH_TOKEN" + suffix, tok["refresh_token"])
    days = int(tok.get("refresh_expires_in", 0)) // 86400
    step(f"TIKTOK_REFRESH_TOKEN{suffix} を .env に保存しました（値は表示しません。有効期限 約 {days} 日。投稿のたびに自動で延長されます）")


if __name__ == "__main__":
    main()
