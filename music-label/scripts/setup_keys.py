#!/usr/bin/env python3
"""
鍵の一括設定ウィザード。サービスごとに「やること」を表示し、ブラウザで設定画面を開き、
鍵を入力欄（画面に表示されない）で受け取って .env に保存し、実際に接続して確かめる。

使い方
  python3 scripts/setup_keys.py              # 上から順に（設定済みで接続できるものは飛ばす）
  python3 scripts/setup_keys.py --check      # 全部を実際に接続して確認するだけ（値は表示しない）
  python3 scripts/setup_keys.py --only supabase,r2
  python3 scripts/setup_keys.py --redo youtube   # 設定済みでもやり直す

順番：Claude → OpenAI → Supabase → Cloudflare R2 → YouTube → Instagram → TikTok
（DistroKid と Suno は鍵ではなくプランの契約。docs/11_plans_and_costs.md）

鍵はこのファイルにも画面にも表示しない。.env は Git に入らない。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_dotenv, ssl_context  # noqa: E402
from oauth_youtube import save_env  # noqa: E402
from set_key import PREFIX, repeated_unit  # noqa: E402

load_dotenv()
IMAGE_MODELS = ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-1.5", "gpt-image-1"]


def get(url: str, headers: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e)


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


# ---------------------------------------------------------------------------
# 接続チェック（戻り値：(結果 ok / ng / none, 説明)）
# ---------------------------------------------------------------------------
def check_anthropic():
    if not env("ANTHROPIC_API_KEY"):
        return "none", "未設定"
    try:
        import anthropic
    except ImportError:
        return "ng", "anthropic パッケージが無い（pip install -r requirements.txt）"
    try:
        m = anthropic.Anthropic().models.retrieve("claude-opus-5-5")
        return "ok", f"接続できました（{m.display_name} を使えます）"
    except anthropic.AuthenticationError:
        return "ng", "鍵が無効です（401）。sk-ant- で始まる『秘密の鍵』を貼ったか確認（apikey_… は鍵の ID で、鍵ではない）"
    except anthropic.PermissionDeniedError:
        return "ng", "この鍵ではモデルを使えません（403）。Console の Billing と Workspace を確認"
    except anthropic.APIConnectionError as e:
        return "ng", f"接続できません（ネットワーク／証明書）: {e}"
    except anthropic.APIStatusError as e:
        return "ng", f"API エラー {e.status_code}"


def check_openai():
    key = env("OPENAI_API_KEY")
    if not key:
        return "none", "未設定"
    h = {"Authorization": f"Bearer {key}"}
    code, body = get("https://api.openai.com/v1/models", h)
    if code == 401:
        return "ng", "鍵が無効です（401）。sk- で始まる鍵を 1 回だけ貼ったか確認（set_key.py --dedupe）"
    if code != 200:
        return "ng", f"接続できません（{code or body[:80]}）"
    for m in IMAGE_MODELS:
        c, _ = get(f"https://api.openai.com/v1/models/{m}", h)
        if c == 200:
            if env("OPENAI_IMAGE_MODEL") != m:
                save_env("OPENAI_IMAGE_MODEL", m)
                os.environ["OPENAI_IMAGE_MODEL"] = m
            return "ok", (f"接続できました。画像モデルは {m} を使います。"
                          "残高は API では確かめられないので Billing 画面で確認（画像 1 枚 数円〜20 円程度）")
    return "ng", "鍵は有効ですが、使える画像モデルがありません。Organization → Verification（本人確認）を確認"


def check_supabase():
    url, key = env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return "none", "未設定"
    if not url.startswith("https://") or ".supabase.co" not in url:
        return "ng", f"SUPABASE_URL の形が違います（https://xxxx.supabase.co の形。今は {url[:40]}）"
    h = {"apikey": key}
    if not key.startswith("sb_"):
        h["Authorization"] = f"Bearer {key}"
    code, body = get(f"{url}/rest/v1/labels?select=slug&limit=1", h)
    if code == 401:
        return "ng", "鍵が違います（401）。Settings → API Keys の秘密鍵（sb_secret_… か service_role）を貼る"
    if code == 404 or "PGRST" in body and "labels" in body:
        return "ng", "鍵は正しいですが、表がまだありません。SQL Editor で supabase/schema.sql と storage.sql を実行"
    if code != 200:
        return "ng", f"接続できません（{code or body[:80]}）"
    seeded = body.strip() not in ("[]", "")
    code, body = get(f"{url}/storage/v1/bucket", h)
    names = {b.get("name") for b in json.loads(body)} if code == 200 else set()
    missing = {"masters", "generations", "covers", "references", "social"} - names
    if missing:
        return "ng", f"表はありますが、保管庫（バケット）が足りません：{', '.join(sorted(missing))}。storage.sql を実行"
    public = [b.get("name") for b in json.loads(body) if b.get("public")]
    if public:
        return "ng", f"公開になっている保管庫があります：{', '.join(public)}。非公開に戻す"
    return "ok", "接続できました（表・非公開の保管庫 5 つ）" + ("" if seeded else "。アーティストの登録はまだ（このあと自動で登録します）")


def check_r2():
    acc, ak, sk, bucket = env("R2_ACCOUNT_ID"), env("R2_ACCESS_KEY_ID"), env("R2_SECRET_ACCESS_KEY"), env("R2_BUCKET") or "label-backup"
    if not (acc and ak and sk):
        return "none", "未設定"
    from backup_r2 import sign_v4
    import hashlib
    host = f"{acc}.r2.cloudflarestorage.com"
    amz = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    empty = hashlib.sha256(b"").hexdigest()
    query = "list-type=2&max-keys=1"
    hdr = {"x-amz-content-sha256": empty, "x-amz-date": amz}
    auth = sign_v4("GET", host, f"/{bucket}", query, hdr, empty, ak, sk, "auto", amz)
    code, body = get(f"https://{host}/{bucket}?{query}", {**hdr, "Authorization": auth})
    if code == 200:
        return "ok", f"接続できました（バケット {bucket}）"
    if code == 404:
        return "ng", f"バケット {bucket} が見つかりません。R2_BUCKET の名前か R2_ACCOUNT_ID を確認"
    if code == 403:
        return "ng", "鍵が違うか、このバケットへの権限がありません（Object Read & Write で作り直す）"
    return "ng", f"接続できません（{code or body[:80]}）"


def check_youtube():
    from post_social import cred, http
    if not cred("YOUTUBE_REFRESH_TOKEN", "-", "-"):
        return "none", "未設定" if not env("YOUTUBE_CLIENT_ID") else "ID とシークレットはあり。合鍵の取得（許可）がまだ"
    try:
        tok = http("POST", "https://oauth2.googleapis.com/token", form={
            "client_id": env("YOUTUBE_CLIENT_ID"), "client_secret": env("YOUTUBE_CLIENT_SECRET"),
            "refresh_token": env("YOUTUBE_REFRESH_TOKEN"), "grant_type": "refresh_token"})["access_token"]
        d = http("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                 headers={"Authorization": f"Bearer {tok}"})
    except RuntimeError as e:
        s = str(e)
        if "invalid_grant" in s:
            return "ng", "合鍵が無効です（同意画面が『テスト』のままだと 7 日で切れる）。本番環境にして oauth_youtube.py をやり直す"
        if "insufficient" in s.lower() or "403" in s:
            return "ng", "権限が足りません。oauth_youtube.py をやり直す（投稿と読み取りの 2 つの権限を許可）"
        return "ng", s[:120]
    items = d.get("items") or []
    if not items:
        return "ng", "このアカウントに YouTube チャンネルがありません。チャンネルを作ってから許可し直す"
    return "ok", f"接続できました（チャンネル：{items[0]['snippet']['title']}）"


def check_instagram():
    tok, uid = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    if not tok:
        return "none", "未設定"
    host = env("IG_GRAPH_HOST") or "graph.instagram.com"
    code, body = get(f"https://{host}/v21.0/me?fields=user_id,username&access_token={urllib.parse.quote(tok)}")
    if code != 200:
        return "ng", "トークンが無効か期限切れです。アプリの画面で生成し直す" if code in (400, 401) else f"接続できません（{code}）"
    d = json.loads(body)
    if uid and d.get("user_id") and str(d["user_id"]) != uid:
        return "ng", f"IG_USER_ID が違います。このトークンのアカウント（@{d.get('username')}）の ID を入れる"
    if not uid and d.get("user_id"):
        save_env("IG_USER_ID", str(d["user_id"]))
        os.environ["IG_USER_ID"] = str(d["user_id"])
    return "ok", f"接続できました（@{d.get('username')}）"


def check_tiktok():
    from post_social import http, tiktok_access
    if not env("TIKTOK_REFRESH_TOKEN"):
        return "none", "未設定" if not env("TIKTOK_CLIENT_KEY") else "Client Key はあり。許可（oauth_tiktok.py）がまだ"
    try:
        tok = tiktok_access("-", "-")
        d = http("GET", "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name",
                 headers={"Authorization": f"Bearer {tok}"})
    except RuntimeError as e:
        return "ng", f"接続できません：{str(e)[:120]}"
    return "ok", f"接続できました（{(d.get('data') or {}).get('user', {}).get('display_name', '名前不明')}）"


# ---------------------------------------------------------------------------
# サービスごとの手順
# ---------------------------------------------------------------------------
SERVICES = {
    "anthropic": {
        "title": "Claude（ブリーフ・話題曲・画像の採点・新人の下書き）",
        "url": "https://console.anthropic.com/settings/keys",
        "steps": ["Create Key を押す → 名前を付ける（例 etherarchi-label）",
                  "表示された sk-ant- で始まる鍵をコピー（画面を閉じると二度と見られない）",
                  "Settings → Billing に残高があるか確認（月 2,000〜3,000 円が目安）"],
        "keys": [("ANTHROPIC_API_KEY", True)], "check": check_anthropic},
    "openai": {
        "title": "OpenAI（ロゴ・写真・ジャケットの画像）",
        "url": "https://platform.openai.com/settings/organization/billing/overview",
        "steps": ["Billing で残高を追加（$10〜20 が目安）。鍵が既にあれば鍵の入力は飛ばしてよい",
                  "鍵が無ければ API keys → Create new secret key",
                  "Organization → General で本人確認（Verification）を済ませる（画像モデルに必要なことがある）"],
        "keys": [("OPENAI_API_KEY", True)], "check": check_openai},
    "supabase": {
        "title": "Supabase（データベースと非公開の保管庫）",
        "url": "https://supabase.com/dashboard/new",
        "steps": ["New project：名前 etherarchi-label、リージョンは Northeast Asia (Tokyo)、DB のパスワードは控えておく",
                  "作成後、左の SQL Editor を開く（この後、貼る SQL をクリップボードに入れます）",
                  "Settings → API Keys：Project URL と秘密鍵（sb_secret_… または service_role）をコピー"],
        "keys": [("SUPABASE_URL", False), ("SUPABASE_SERVICE_ROLE_KEY", True)], "check": check_supabase},
    "r2": {
        "title": "Cloudflare R2（予備の保管庫）",
        "url": "https://dash.cloudflare.com/?to=/:account/r2/overview",
        "steps": ["R2 を有効にする → Create bucket：名前 label-backup、公開アクセスはオフのまま",
                  "Manage R2 API Tokens → Create API token：権限 Object Read & Write、対象 label-backup だけ",
                  "Account ID（右側）、Access Key ID、Secret Access Key をコピー"],
        "keys": [("R2_ACCOUNT_ID", False), ("R2_ACCESS_KEY_ID", True), ("R2_SECRET_ACCESS_KEY", True), ("R2_BUCKET", False)],
        "check": check_r2},
    "youtube": {
        "title": "YouTube（ショート動画の投稿・再生数）",
        "url": "https://console.cloud.google.com/projectcreate",
        "steps": ["プロジェクト作成（例 etherarchi-label）→ API とサービス → ライブラリ → YouTube Data API v3 を有効化",
                  "OAuth 同意画面：外部、アプリ名 EtherArchi Label、テストユーザーに自分 → 公開ステータスを『本番環境』に",
                  "認証情報 → OAuth クライアント ID → 種類『デスクトップ アプリ』→ ID とシークレットをコピー"],
        "keys": [("YOUTUBE_CLIENT_ID", False), ("YOUTUBE_CLIENT_SECRET", True)],
        "after": ["oauth_youtube.py"], "check": check_youtube},
    "instagram": {
        "title": "Instagram（リールの投稿・再生数）",
        "url": "https://developers.facebook.com/apps/creation/",
        "steps": ["先にスマホの Instagram をプロアカウント（クリエイター）に切り替える",
                  "アプリ作成 → ユースケース『Instagram でメッセージとコンテンツを管理』",
                  "権限 instagram_business_basic・instagram_business_content_publish・instagram_business_manage_insights を追加",
                  "アプリの役割で自分の Instagram をテスターに追加し、Instagram 側で承認 → 『トークンを生成』"],
        "keys": [("IG_ACCESS_TOKEN", True)], "check": check_instagram},
    "tiktok": {
        "title": "TikTok（動画の投稿・再生数）",
        "url": "https://developers.tiktok.com/apps/",
        "steps": ["開発者登録 → Create app → 製品に Login Kit と Content Posting API を追加（Direct Post を有効）",
                  "スコープ user.info.basic・video.publish・video.list を追加",
                  "Login Kit の Redirect URI に http://localhost:8765/callback/ を登録（だめなら https の自分のサイトの URL）",
                  "Client Key と Client Secret をコピー"],
        "keys": [("TIKTOK_CLIENT_KEY", False), ("TIKTOK_CLIENT_SECRET", True), ("TIKTOK_REDIRECT_URI", False)],
        "after": ["oauth_tiktok.py"], "check": check_tiktok},
}
MARK = {"ok": "○", "ng": "✕", "none": "－"}


def ask_key(name: str, secret: bool) -> str | None:
    current = env(name)
    note = "（設定済み。Enter だけで今の値のまま）" if current else ""
    if name == "R2_BUCKET" and not current:
        note = "（Enter だけで label-backup）"
    if name == "TIKTOK_REDIRECT_URI" and not current:
        note = "（Enter だけで http://localhost:8765/callback/）"
    prompt = f"   {name}{note}: "
    v = (getpass.getpass(prompt) if secret else input(prompt)).strip().strip('"').strip("'")
    if not v:
        return {"R2_BUCKET": "label-backup", "TIKTOK_REDIRECT_URI": "http://localhost:8765/callback/"}.get(name) if not current else None
    unit = repeated_unit(v)
    if unit:
        print(f"   （同じ値が {len(v) // len(unit)} 回貼られていたので 1 回分にしました）")
        v = unit
    want = PREFIX.get(name)
    if want and not v.startswith(want):
        print(f"   ⚠ 先頭が {want} ではありません（{len(v)} 文字、先頭 {v[:3]}…）。鍵の ID や別の文字列を貼っていないか確認")
        if input("   このまま保存しますか？ (y/N): ").strip().lower() != "y":
            return None
    return v


def copy_to_clipboard(path: Path) -> bool:
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=path.read_bytes(), check=False)
        return True
    return False


def supabase_sql_step() -> None:
    for f in ("schema.sql", "storage.sql"):
        p = ROOT / "supabase" / f
        if copy_to_clipboard(p):
            input(f"   ▶ {f} をクリップボードに入れました。SQL Editor に貼って Run → 終わったら Enter: ")
        else:
            input(f"   ▶ {p} の中身を SQL Editor に貼って Run → 終わったら Enter: ")


def run_service(key: str, redo: bool) -> tuple[str, str]:
    s = SERVICES[key]
    print(f"\n━━ {s['title']} ━━")
    state, msg = s["check"]()
    if state == "ok" and not redo:
        print(f"   ○ {msg}（設定済みなので飛ばします。やり直すときは --redo {key}）")
        return state, msg
    for i, st in enumerate(s["steps"], 1):
        print(f"   {i}. {st}")
    if input(f"   ブラウザで {s['url']} を開きますか？ [Y/n]: ").strip().lower() != "n":
        webbrowser.open(s["url"])
    if key == "supabase":
        supabase_sql_step()
    if input("   準備ができたら Enter（このサービスを今日は飛ばすなら s）: ").strip().lower() == "s":
        return "none", "飛ばしました"
    for name, secret in s["keys"]:
        v = ask_key(name, secret)
        if v:
            save_env(name, v)
            os.environ[name] = v
    for script in s.get("after", []):
        print(f"   ▶ {script} で許可を取ります（ブラウザで『許可』を押してください）")
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=False)
        load_dotenv()
        for k, v in _read_env().items():
            os.environ[k] = v
    for attempt in range(2):
        state, msg = s["check"]()
        print(f"   {MARK[state]} {msg}")
        if state == "ok" or input("   もう一度確かめますか？（直したら y）[y/N]: ").strip().lower() != "y":
            break
    if key == "supabase" and state == "ok":
        print("   ▶ アーティストとレーベルをデータベースに登録しています")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "supabase_sync.py"), "seed", "--apply"], check=False)
    return state, msg


def _read_env() -> dict:
    p = ROOT / ".env"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_status(results: dict) -> Path:
    lines = [f"# 鍵の設定状況（{date.today()}）", "", "値は書いていません。", "", "| サービス | 状態 | 説明 |", "|---|---|---|"]
    for k, (st, msg) in results.items():
        lines.append(f"| {SERVICES[k]['title']} | {MARK[st]} | {msg} |")
    lines += ["", "鍵ではなく契約が必要なもの：Suno（Premier）・DistroKid（Ultimate）→ docs/11_plans_and_costs.md", ""]
    p = OUT / "setup" / "keys_status.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description="鍵の一括設定ウィザード")
    ap.add_argument("--check", action="store_true", help="接続の確認だけ")
    ap.add_argument("--only", help="カンマ区切り（anthropic,openai,supabase,r2,youtube,instagram,tiktok）")
    ap.add_argument("--redo", default="", help="設定済みでもやり直すサービス（カンマ区切り）")
    a = ap.parse_args()
    keys = [k for k in (a.only.split(",") if a.only else SERVICES) if k in SERVICES]
    redo = set(a.redo.split(",")) if a.redo else set()
    results = {}
    if a.check:
        print("=== 鍵の接続確認（値は表示しません）===")
        for k in keys:
            try:
                results[k] = SERVICES[k]["check"]()
            except Exception as e:  # noqa: BLE001
                results[k] = ("ng", f"確認中にエラー：{str(e)[:100]}")
            st, msg = results[k]
            print(f"  {MARK[st]} {SERVICES[k]['title']}：{msg}")
    else:
        print("=== 鍵の一括設定を始めます ===")
        print("  鍵は画面に表示されない入力欄で受け取り、music-label/.env にだけ保存します。途中でやめても、次回は続きから進みます")
        for k in keys:
            try:
                results[k] = run_service(k, k in redo)
            except KeyboardInterrupt:
                print("\n  中断しました。続きは同じコマンドで再開できます")
                break
    p = write_status(results)
    done = sum(1 for st, _ in results.values() if st == "ok")
    print(f"\n=== {done}/{len(results)} 件が接続できました → {p.relative_to(ROOT)} ===")
    if done == len(SERVICES):
        print("  次：python3 scripts/trial.py --artist light（1 組の通し運転）")


if __name__ == "__main__":
    main()
