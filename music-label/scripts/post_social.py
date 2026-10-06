#!/usr/bin/env python3
"""
配信に合わせて SNS（YouTube ショート / Instagram リール / TikTok）へ縦動画を投稿する。

  plan  その週の曲ごとに「縦動画（サビ 30 秒）」「説明文」「投稿時刻」を作る（鍵は要らない）
  post  投稿時刻を過ぎたものを投稿する（鍵が無い所は飛ばして、理由を表示する）

  ・動画：ジャケットをぼかした背景＋中央にジャケット＋曲名とアーティスト名。音はサビから 30 秒
  ・説明文：曲名・アーティスト名・bio の一文・ハッシュタグ・AI 使用の明記（実在アーティスト名は入れない）
  ・時刻：配信時刻（例 水曜 17:00 ET）。YouTube は先にアップロードして公開予約できる
  ・AI 申告：YouTube は「合成コンテンツ」欄、TikTok は「AI 生成」欄を API で必ず立てる。Instagram は説明文で明記

使い方
  python scripts/post_social.py plan --week 2026-10-05
  python scripts/post_social.py post --week 2026-10-05            # 鍵の一括設定の後。cron で 15 分ごとに回す想定
  python scripts/post_social.py post --week 2026-10-05 --dry-run  # 何が投稿されるかだけ表示
  python scripts/post_social.py refresh-ig                         # Instagram の鍵を延長（月 1 回）

鍵（.env）。組ごと・レーベルごとに別アカウントにするときは  名前__組  や  名前__レーベル  を付ける
  （例 YOUTUBE_REFRESH_TOKEN__LIGHT、IG_USER_ID__FOCUS。無ければ付いていない名前を使う）
  YouTube  : YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN
  Instagram: IG_USER_ID / IG_ACCESS_TOKEN（IG_GRAPH_HOST で接続先を変更可。動画は Supabase の期限付き URL で渡すので SUPABASE_* も必要）
  TikTok   : TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REFRESH_TOKEN
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_artist, load_dotenv, read_json, ssl_context, step, write_json  # noqa: E402

load_dotenv()
PLATFORMS = ["youtube", "instagram", "tiktok"]
CLIP_SEC = 30
FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


# ---------------------------------------------------------------------------
# 鍵
# ---------------------------------------------------------------------------
def cred(name: str, artist_slug: str, label_slug: str) -> str | None:
    for k in (f"{name}__{artist_slug.upper()}", f"{name}__{label_slug.upper()}", name):
        if os.environ.get(k):
            return os.environ[k]
    return None


def http(method: str, url: str, *, data: bytes | None = None, headers: dict | None = None, form: dict | None = None,
         body: dict | None = None, want_headers: bool = False):
    h = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json; charset=UTF-8")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=600, context=ssl_context()) as r:
            raw = r.read().decode() or "{}"
            parsed = json.loads(raw) if raw.strip().startswith(("{", "[")) else {"raw": raw}
            return (parsed, dict(r.headers)) if want_headers else parsed
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:500]
        host = urllib.parse.urlparse(url).netloc
        raise RuntimeError(f"{host} → HTTP {e.code}: {msg}") from None


# ---------------------------------------------------------------------------
# 動画と説明文
# ---------------------------------------------------------------------------
def find_font() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).exists()), None)


def esc_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def make_clip(master: Path, cover: Path, start: float, title: str, artist: str, dst: Path) -> None:
    if not shutil.which("ffmpeg"):
        sys.exit("[エラー] ffmpeg が見つかりません（Mac：brew install ffmpeg）")
    dst.parent.mkdir(parents=True, exist_ok=True)
    font = find_font()
    vf = ("[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40,eq=brightness=-0.08[bg];"
          "[1:v]scale=960:960[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80")
    if font:
        t, a = esc_drawtext(title), esc_drawtext(artist)
        vf += (f",drawtext=fontfile='{font}':text='{t}':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=h/2+480"
               f",drawtext=fontfile='{font}':text='{a}':fontcolor=white@0.8:fontsize=44:x=(w-text_w)/2:y=h/2+570")
    vf += ",format=yuv420p[v]"
    af = f"afade=t=in:st=0:d=1,afade=t=out:st={CLIP_SEC - 2}:d=2"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", str(CLIP_SEC), "-i", str(master),
           "-loop", "1", "-framerate", "30", "-t", str(CLIP_SEC), "-i", str(cover),
           "-filter_complex", vf, "-map", "[v]", "-map", "0:a", "-af", af,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("[エラー] 動画の書き出しに失敗しました:\n" + r.stderr[-800:])


def chorus_start(week: str, slug: str, duration_guess: float = 180) -> float:
    """選んだテイクの「最初の山（サビ）」の 1 秒前から始める。分からなければ 45 秒地点"""
    sel = OUT / "takes" / f"{week}_{slug}" / "selection.json"
    if sel.exists():
        d = read_json(sel)
        t = next((x for x in d.get("takes", []) if x["take"] == d.get("decision", {}).get("selected")), None)
        if t and t["measure"].get("first_peak_sec") is not None:
            dur = t["measure"].get("duration_sec", duration_guess)
            return max(0.0, min(t["measure"]["first_peak_sec"] - 1, dur - CLIP_SEC - 1))
    return 45.0


def hashtags(artist: dict) -> list[str]:
    tags = ["AIMusic", "NewMusic"]
    for t in (artist.get("sound") or {}).get("dna_tags", [])[:4]:
        tags.append(re.sub(r"[^A-Za-z0-9]", "", t.title()))
    return [f"#{t}" for t in dict.fromkeys(tags) if t]


def captions(artist: dict, title: str, feat: str | None) -> dict:
    name = artist["name"] + (f" feat. {feat}" if feat else "")    # 説明文では feat. 表記で可（タイトル欄ではない）
    bio = ((artist.get("distribution") or {}).get("bio_en") or "").split(". ")
    line = bio[1] if len(bio) > 1 else (bio[0] if bio else "")
    tags = " ".join(hashtags(artist))
    disclosure = "Made with AI at EtherArchi — a label where AI-generated music meets spatial design."
    base = f"{title} — {name}\nOut now on all platforms.\n\n{line.strip()}\n\n{disclosure}"
    return {
        "youtube_title": f"{title} - {name} #Shorts"[:100],
        "youtube": f"{base}\n\n{tags} #Shorts",
        "instagram": f"{base}\n\n{tags}",
        "tiktok": f"{title} — {name} ✦ out now. {disclosure} {tags}"[:2200],
    }


def scrub_check(text: str, artist: dict) -> None:
    """説明文に実在アーティスト名（プロフィールの好きなアーティスト）が混ざっていないか"""
    for n in (artist.get("profile") or {}).get("favorite_artists_real", []) or []:
        if n and n.lower() in text.lower():
            sys.exit(f"[停止] 説明文に実在アーティスト名「{n}」が入っています。設定書の bio_en を確認してください")


# ---------------------------------------------------------------------------
# 投稿（各サービスの公式 API）
# ---------------------------------------------------------------------------
def post_youtube(p: dict, a_slug: str, l_slug: str) -> dict:
    cid, csec, rtok = (cred(k, a_slug, l_slug) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
    if not (cid and csec and rtok):
        return {"skipped": "YouTube の鍵（YOUTUBE_CLIENT_ID / SECRET / REFRESH_TOKEN）がありません"}
    step("YouTube：アクセス用の一時トークンを取得しています")
    tok = http("POST", "https://oauth2.googleapis.com/token",
               form={"client_id": cid, "client_secret": csec, "refresh_token": rtok, "grant_type": "refresh_token"})["access_token"]
    when = datetime.fromisoformat(p["scheduled_at"].replace("Z", "+00:00"))
    future = when > datetime.now(timezone.utc)
    status = {"privacyStatus": "private" if future else "public", "selfDeclaredMadeForKids": False,
              "containsSyntheticMedia": True}       # AI で作った（合成）コンテンツであることを申告
    if future:
        status["publishAt"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")   # 配信時刻に自動で公開
    meta = {"snippet": {"title": p["youtube_title"], "description": p["caption"], "categoryId": "10",
                        "tags": [t.lstrip("#") for t in p.get("tags", [])]}, "status": status}
    video = Path(p["video"])
    step("YouTube：アップロードの受付をしています")
    _, hdr = http("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                  body=meta, headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": "video/mp4",
                                      "X-Upload-Content-Length": str(video.stat().st_size)}, want_headers=True)
    up = hdr.get("Location") or hdr.get("location")
    step(f"YouTube：動画を送っています（{video.stat().st_size // 1024} KB）")
    res = http("PUT", up, data=video.read_bytes(), headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"})
    return {"id": res.get("id"), "url": f"https://youtube.com/shorts/{res.get('id')}",
            "scheduled": status.get("publishAt")}


def post_instagram(p: dict, a_slug: str, l_slug: str) -> dict:
    uid, tok = cred("IG_USER_ID", a_slug, l_slug), cred("IG_ACCESS_TOKEN", a_slug, l_slug)
    if not (uid and tok):
        return {"skipped": "Instagram の鍵（IG_USER_ID / IG_ACCESS_TOKEN）がありません"}
    from _supabase import Client
    cl = Client()
    if not cl.ready:
        return {"skipped": "Instagram は動画を URL で受け取るため、Supabase の鍵（SUPABASE_URL / SERVICE_ROLE_KEY）も必要です"}
    step("Instagram：動画を非公開の保管庫に上げ、10 分だけ有効な URL を作っています")
    key = f"{p['week']}/{p['artist_slug']}/clip_vertical.mp4"
    cl.upload("social", key, Path(p["video"]), "video/mp4")
    url = cl.signed_url("social", key, 600)
    # Instagram ログイン方式（既定）は graph.instagram.com、Facebook ページ経由の方式は graph.facebook.com
    g = f"https://{os.environ.get('IG_GRAPH_HOST', 'graph.instagram.com')}/v21.0"
    step("Instagram：リールの下書きを作っています")
    c = http("POST", f"{g}/{uid}/media", form={"media_type": "REELS", "video_url": url, "caption": p["caption"],
                                                 "share_to_feed": "true", "access_token": tok})
    cid = c["id"]
    for i in range(30):                                   # 最大 5 分、処理が終わるのを待つ
        st = http("GET", f"{g}/{cid}?fields=status_code&access_token={urllib.parse.quote(tok)}").get("status_code")
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError("Instagram 側で動画の処理に失敗しました（形式・長さを確認）")
        step(f"Instagram：動画の処理を待っています（{(i + 1) * 10} 秒）")
        time.sleep(10)
    res = http("POST", f"{g}/{uid}/media_publish", form={"creation_id": cid, "access_token": tok})
    return {"id": res.get("id")}


def post_tiktok(p: dict, a_slug: str, l_slug: str) -> dict:
    ck, cs, rt = (cred(k, a_slug, l_slug) for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"))
    if not (ck and cs and rt):
        return {"skipped": "TikTok の鍵（TIKTOK_CLIENT_KEY / SECRET / REFRESH_TOKEN）がありません"}
    api = "https://open.tiktokapis.com/v2"
    step("TikTok：アクセス用の一時トークンを取得しています")
    tok = http("POST", f"{api}/oauth/token/", form={"client_key": ck, "client_secret": cs,
                                                     "grant_type": "refresh_token", "refresh_token": rt})["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    info = http("POST", f"{api}/post/publish/creator_info/query/", headers=auth, body={})["data"]
    options = info.get("privacy_level_options", [])
    privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else (options[0] if options else "SELF_ONLY")
    video = Path(p["video"])
    size = video.stat().st_size
    step(f"TikTok：投稿の受付をしています（公開範囲 {privacy}）")
    init = http("POST", f"{api}/post/publish/video/init/", headers=auth, body={
        "post_info": {"title": p["caption"], "privacy_level": privacy, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000,
                      "is_aigc": True},                    # AI 生成コンテンツの申告
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1},
    })["data"]
    step("TikTok：動画を送っています")
    http("PUT", init["upload_url"], data=video.read_bytes(),
         headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"})
    out = {"publish_id": init.get("publish_id"), "privacy": privacy}
    if privacy != "PUBLIC_TO_EVERYONE":
        out["note"] = "審査前のアプリは自分だけに公開。TikTok の審査（Content Posting API の監査）が通ると公開投稿できます"
    return out


POSTERS = {"youtube": post_youtube, "instagram": post_instagram, "tiktok": post_tiktok}


# ---------------------------------------------------------------------------
def cmd_plan(week: str) -> None:
    metas = sorted((OUT / "distrokid" / week).glob("*.metadata.json"))
    if not metas:
        sys.exit(f"[エラー] out/distrokid/{week}/ に登録データがありません。先に distrokid_sheet.py を実行してください")
    for mp in metas:
        m = read_json(mp)
        slug = m["artist_slug"]
        artist = load_artist(slug, m.get("label_slug"))
        master, cover = ROOT / m["audio"], ROOT / m["cover"] if m.get("cover") else None
        if not master.exists() or not cover or not cover.exists():
            print(f"   － {artist['name']}：音源かジャケットがまだ無いので飛ばします")
            continue
        if not m.get("title") or m["title"].startswith("（"):
            print(f"   － {artist['name']}：タイトルが未定なので飛ばします")
            continue
        folder = OUT / "social" / week / slug
        video = folder / "clip_vertical.mp4"
        start = chorus_start(week, slug)
        step(f"{artist['name']}：縦動画（サビ {start:.0f} 秒地点から {CLIP_SEC} 秒）を書き出しています")
        make_clip(master, cover, start, m["title"], artist["name"], video)
        feat = load_artist(m["featured_artist_slug"])["name"] if m.get("featured_artist_slug") else None
        cap = captions(artist, m["title"], feat)
        for v in cap.values():
            scrub_check(v, artist)
        old = read_json(folder / "plan.json") if (folder / "plan.json").exists() else {}
        posts = []
        for plat in PLATFORMS:
            prev = next((x for x in old.get("posts", []) if x["platform"] == plat), {})
            posts.append({"platform": plat, "scheduled_at": m["release_at_utc"], "video": str(video),
                          "caption": cap[plat], "youtube_title": cap["youtube_title"], "tags": hashtags(artist),
                          "week": week, "artist_slug": slug, "label_slug": m.get("label_slug"),
                          "status": prev.get("status", "pending"), "result": prev.get("result")})
        write_json(folder / "plan.json", {"artist": artist["name"], "title": m["title"], "posts": posts})
        (folder / "captions.md").write_text(
            f"# {artist['name']} — {m['title']}\n\n投稿時刻（UTC）: {m['release_at_utc']}\n\n" +
            "\n\n".join(f"## {k}\n\n```\n{v}\n```" for k, v in cap.items()) + "\n", encoding="utf-8")
        step(f"   → {folder.relative_to(ROOT)}/（clip_vertical.mp4・captions.md・plan.json）")


def cmd_post(week: str, dry: bool) -> None:
    plans = sorted((OUT / "social" / week).glob("*/plan.json"))
    if not plans:
        sys.exit(f"[エラー] out/social/{week}/ に計画がありません。先に  post_social.py plan --week {week}")
    now = datetime.now(timezone.utc)
    for pp in plans:
        plan = read_json(pp)
        for p in plan["posts"]:
            if p["status"] == "done":
                continue
            when = datetime.fromisoformat(p["scheduled_at"].replace("Z", "+00:00"))
            # YouTube は公開予約ができるので前もって上げる。他は時刻を過ぎてから投稿する
            if p["platform"] != "youtube" and when > now:
                print(f"   … {plan['artist']} / {p['platform']}：投稿時刻 {p['scheduled_at']} を待っています")
                continue
            if dry:
                print(f"   [ドライラン] {plan['artist']} / {p['platform']} に投稿します：{p['caption'][:60].replace(chr(10), ' ')}…")
                continue
            try:
                res = POSTERS[p["platform"]](p, p["artist_slug"], p.get("label_slug") or "drive")
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)}
            if res.get("skipped"):
                print(f"   － {plan['artist']} / {p['platform']}：{res['skipped']}（飛ばしました）")
                continue
            p["result"] = res
            p["status"] = "error" if res.get("error") else "done"
            mark = "✕" if res.get("error") else "○"
            print(f"   {mark} {plan['artist']} / {p['platform']}：{res.get('error') or res}")
        write_json(pp, plan)


def cmd_refresh_ig(target: str | None) -> None:
    """Instagram の長期トークン（60 日で切れる）を延長して .env に書き戻す。月 1 回の実行で切れない"""
    from oauth_youtube import save_env
    suffix = f"__{target.upper()}" if target else ""
    tok = os.environ.get("IG_ACCESS_TOKEN" + suffix)
    if not tok:
        sys.exit(f"[停止] .env に IG_ACCESS_TOKEN{suffix} がありません")
    host = os.environ.get("IG_GRAPH_HOST", "graph.instagram.com")
    step("Instagram：長期トークンを延長しています")
    res = http("GET", f"https://{host}/refresh_access_token?grant_type=ig_refresh_token&access_token={urllib.parse.quote(tok)}")
    save_env("IG_ACCESS_TOKEN" + suffix, res["access_token"])
    step(f"延長しました（あと約 {int(res.get('expires_in', 0)) // 86400} 日有効）。値は表示しません")


def main() -> None:
    ap = argparse.ArgumentParser(description="SNS に縦動画を投稿する（plan → post）")
    ap.add_argument("command", choices=["plan", "post", "refresh-ig"])
    ap.add_argument("--week", help="制作週の月曜（plan / post）")
    ap.add_argument("--for", dest="target", help="refresh-ig：組かレーベル（例 light / focus）。省略で共通")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.command == "refresh-ig":
        cmd_refresh_ig(args.target)
        return
    if not args.week:
        sys.exit("[エラー] --week を指定してください（例：--week 2026-10-05）")
    if args.command == "plan":
        cmd_plan(args.week)
    else:
        cmd_post(args.week, args.dry_run)


if __name__ == "__main__":
    main()
