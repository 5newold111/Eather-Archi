#!/usr/bin/env python3
"""
配信に合わせて SNS（YouTube ショート / Instagram リール / TikTok）へ縦動画を投稿する。

  plan  その週の曲ごとに「縦動画（サビ 30 秒）」「説明文」「投稿時刻」を作る（鍵は要らない）
  daily 毎日の投稿（1 日 1〜3 本。決定 94）：配信日の曲のほかに、配信中の曲から選んで投稿の案を作る（数日先まで）
  post  投稿時刻を過ぎたものを投稿する（鍵が無い所は飛ばして、理由を表示する）

  ・背景の映像（決定 94）：Gon さんの Blender の映像を、次の順に探して使う（無ければジャケットをぼかした背景）
      out/social_bg/<組>/<曲名>/*.mp4（その曲専用）→ out/social_bg/<組>/*.mp4（その組の映像を順に）→
      out/social_bg/_label/*.mp4（レーベル共通）→ Blender のルームツアー（render_roomtour.py）
    置き場所は .env の SOCIAL_BG_DIR で変えられる（SSD のフォルダなど）。.mov・.m4v・.webm も使える
  ・説明文「この曲が生まれた日」（決定 94）：曲のエピソード（管理表の人生の年表・近況から Claude が書いたもの）から作る

  ・動画：ジャケットをぼかした背景＋中央にジャケット＋曲名とアーティスト名。音はサビから 30 秒
  ・説明文：曲名・アーティスト名・bio の一文・ハッシュタグ・AI 使用の明記（実在アーティスト名は入れない）
  ・時刻：配信時刻（例 水曜 17:00 ET）。YouTube は先にアップロードして公開予約できる
  ・AI 申告：YouTube は「合成コンテンツ」欄、TikTok は「AI 生成」欄を API で必ず立てる。Instagram は説明文で明記

使い方
  python scripts/post_social.py plan --week 2026-10-05
  python scripts/post_social.py daily --on --per-day 2      # 毎日の投稿を始める（1 日 2 本）。--off で止める
  python scripts/post_social.py daily                       # 3 日先までの投稿の案を作る（定期実行は --if-on）
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
from _common import OUT, ROOT, load_artist, load_dotenv, notify, read_json, ssl_context, step, write_json  # noqa: E402

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
def cred_key(name: str, artist_slug: str, label_slug: str) -> str | None:
    """実際に使う鍵の名前（組専用 → レーベル専用 → 共通の順）"""
    for k in (f"{name}__{artist_slug.upper()}", f"{name}__{label_slug.upper()}", name):
        if os.environ.get(k):
            return k
    return None


def cred(name: str, artist_slug: str, label_slug: str) -> str | None:
    k = cred_key(name, artist_slug, label_slug)
    return os.environ[k] if k else None


def tiktok_access(artist_slug: str, label_slug: str) -> str | None:
    """TikTok の一時トークンを取る。更新用の鍵が新しくなって返ってきたら .env に保存し直す"""
    ck, cs = cred("TIKTOK_CLIENT_KEY", artist_slug, label_slug), cred("TIKTOK_CLIENT_SECRET", artist_slug, label_slug)
    rk = cred_key("TIKTOK_REFRESH_TOKEN", artist_slug, label_slug)
    if not (ck and cs and rk):
        return None
    res = http("POST", "https://open.tiktokapis.com/v2/oauth/token/",
               form={"client_key": ck, "client_secret": cs, "grant_type": "refresh_token", "refresh_token": os.environ[rk]})
    new = res.get("refresh_token")
    if new and new != os.environ[rk]:
        from oauth_youtube import save_env
        save_env(rk, new)
        os.environ[rk] = new
    return res["access_token"]


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


def roomtour_loop(slug: str, artist: dict) -> Path | None:
    """render_roomtour.py が作った Blender のルームツアー（その組の今の世代）があれば使う"""
    hist = artist.get("concept_history") or {}
    ver = hist.get("concept_version", 1) if isinstance(hist, dict) else 1
    p = OUT / "roomtour" / slug / f"v{ver}" / "loop.mp4"
    return p if p.exists() else None


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}


def bg_root() -> Path:
    return Path(os.environ["SOCIAL_BG_DIR"]).expanduser() if os.environ.get("SOCIAL_BG_DIR") else OUT / "social_bg"


def _videos(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")) if d.is_dir() else []


def background_for(slug: str, song_folder: str | None, artist: dict, n: int = 0) -> Path | None:
    """縦動画の背景にする Blender の映像（決定 94）。その曲専用 → その組 → レーベル共通 → ルームツアー。n で順に回す"""
    root = bg_root()
    for d in ([root / slug / song_folder] if song_folder else []) + [root / slug, root / "_label"]:
        vids = _videos(d)
        if vids:
            return vids[n % len(vids)]
    return roomtour_loop(slug, artist)


def bg_folders(artists: list[dict]) -> None:
    """背景の映像を置くフォルダを作っておく（Gon さんが Blender の書き出しを入れるだけでよいように）"""
    root = bg_root()
    for d in [root / "_label"] + [root / a["slug"] for a in artists if a.get("slug")]:
        d.mkdir(parents=True, exist_ok=True)
    readme = root / "はじめに読む.txt"
    if not readme.exists():
        readme.write_text("SNS の縦動画の背景にする映像（Blender の書き出し）を入れるフォルダです。\n\n"
                          "  <組の slug>/<曲名>/  … その曲だけに使う映像\n  <組の slug>/       … その組の曲に順に使う映像\n"
                          "  _label/            … どの組にも使う映像\n\n"
                          "縦長（1080×1920）がいちばんきれいです。横長でも中央を切り抜いて使います。30 秒以上あると途中で繰り返しません。\n"
                          "形式：mp4・mov・m4v・webm\n", encoding="utf-8")


def make_clip(master: Path, cover: Path, start: float, title: str, artist: str, dst: Path, bg_video: Path | None = None) -> None:
    if not shutil.which("ffmpeg"):
        sys.exit("[エラー] ffmpeg が見つかりません（Mac：brew install ffmpeg）")
    dst.parent.mkdir(parents=True, exist_ok=True)
    font = find_font()
    t, a = esc_drawtext(title), esc_drawtext(artist)
    if bg_video:
        # Blender のルームツアーを背景に、左下にジャケットと曲名
        vf = ("[2:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30[bg];"
              "[1:v]scale=360:360[fg];[bg][fg]overlay=64:H-h-300")
        if font:
            vf += (f",drawtext=fontfile='{font}':text='{t}':fontcolor=white:fontsize=56:x=64:y=h-250:shadowcolor=black@0.4:shadowx=2:shadowy=2"
                   f",drawtext=fontfile='{font}':text='{a}':fontcolor=white@0.85:fontsize=40:x=64:y=h-180:shadowcolor=black@0.4:shadowx=2:shadowy=2")
    else:
        vf = ("[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40,eq=brightness=-0.08[bg];"
              "[1:v]scale=960:960[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80")
        if font:
            vf += (f",drawtext=fontfile='{font}':text='{t}':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=h/2+480"
                   f",drawtext=fontfile='{font}':text='{a}':fontcolor=white@0.8:fontsize=44:x=(w-text_w)/2:y=h/2+570")
    vf += ",format=yuv420p[v]"
    af = f"afade=t=in:st=0:d=1,afade=t=out:st={CLIP_SEC - 2}:d=2"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", str(CLIP_SEC), "-i", str(master),
           "-loop", "1", "-framerate", "30", "-t", str(CLIP_SEC), "-i", str(cover),
           *(["-stream_loop", "-1", "-t", str(CLIP_SEC), "-i", str(bg_video)] if bg_video else []),
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


LEADS = {"release": "Out now on all platforms.", "born": "The day this song was born.", "listen": "Listen on all platforms."}


def captions(artist: dict, title: str, feat: str | None, story: str = "", kind: str = "release") -> dict:
    name = artist["name"] + (f" feat. {feat}" if feat else "")    # 説明文では feat. 表記で可（タイトル欄ではない）
    bio = ((artist.get("distribution") or {}).get("bio_en") or "").split(". ")
    line = story or (bio[1] if len(bio) > 1 else (bio[0] if bio else ""))   # 曲のエピソード（決定 33）があればそれを
    tags = " ".join(hashtags(artist))
    disclosure = "Made with AI at EtherArchi — a label where AI-generated music meets spatial design."
    lead = LEADS.get(kind, LEADS["release"])
    base = f"{title} — {name}\n{lead}\n\n{line.strip()}\n\n{disclosure}"
    short = {"release": "out now", "born": "the day this song was born", "listen": "listen now"}.get(kind, "out now")
    return {
        "youtube_title": f"{title} - {name} #Shorts"[:100],
        "youtube": f"{base}\n\n{tags} #Shorts",
        "instagram": f"{base}\n\n{tags}",
        "tiktok": (f"{title} — {name} ✦ {short}. " + (f"{line.strip()[:400]} " if kind == "born" else "") + f"{disclosure} {tags}")[:2200],
    }


def born_story(brief: dict, artist: dict) -> tuple[str, str]:
    """「この曲が生まれた日」（決定 94）：曲のエピソード（管理表の人生の年表・近況から Claude が書いたもの。決定 33）から。
    戻り値：(英語の説明文の一節, Gon さんが読む日本語)。実在の名前が入っていれば使わない"""
    ep = brief.get("episode") or {}
    lm = brief.get("life_moment") or {}
    en = str(ep.get("story_en") or "").strip()
    sent = re.split(r"(?<=[.!?])\s+", en)
    text = ""
    for s in sent:   # 2〜3 文・350 文字まで
        if len(text) + len(s) > 350:
            break
        text = f"{text} {s}".strip()
    ja = " / ".join(x for x in (ep.get("when_where"), ep.get("realized"), lm.get("event") or lm.get("idea")) if x)
    try:
        import write_brief
        if write_brief.real_name_hits(artist, text + " " + ja):
            return "", ""
    except Exception:  # noqa: BLE001
        pass
    return text, ja


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
    if p.get("force_private"):
        future = False                                  # 試運転：公開予約もせず、非公開のまま置く
    status = {"privacyStatus": "private" if (future or p.get("force_private")) else "public",
              "selfDeclaredMadeForKids": False,
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
    key = p.get("store_key") or f"{p['week']}/{p['artist_slug']}/clip_vertical.mp4"
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
    if not cred("TIKTOK_REFRESH_TOKEN", a_slug, l_slug):
        return {"skipped": "TikTok の鍵（TIKTOK_CLIENT_KEY / SECRET / REFRESH_TOKEN）がありません"}
    api = "https://open.tiktokapis.com/v2"
    step("TikTok：アクセス用の一時トークンを取得しています")
    tok = tiktok_access(a_slug, l_slug)
    if not tok:
        return {"skipped": "TikTok の鍵（TIKTOK_CLIENT_KEY / SECRET）がありません"}
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
    if daily_settings().get("on"):   # 毎日の投稿をしているときは、配信日の投稿も毎日の投稿の案に入る（同じ曲を 2 回出さない）
        print("   SNS は毎日の投稿（post_social.py daily）で出しているので、週の投稿の案は作りません")
        return
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
        loop = background_for(slug, None, artist)   # Gon さんの Blender の映像 → ルームツアー（決定 94）
        sources = [master, cover, mp] + ([loop] if loop else [])
        if video.exists() and video.stat().st_mtime > max(f.stat().st_mtime for f in sources):
            print(f"   － {artist['name']}：縦動画は最新なので作り直しません")
        else:
            step(f"{artist['name']}：縦動画（サビ {start:.0f} 秒地点から {CLIP_SEC} 秒）を書き出しています")
            if loop:
                print(f"     （背景に Blender の映像を使います：{loop.name}）")
            make_clip(master, cover, start, m["title"], artist["name"], video, loop)
        feat = load_artist(m["featured_artist_slug"])["name"] if m.get("featured_artist_slug") else None
        bp = OUT / "briefs" / f"{week}_{slug}.json"
        story = ((read_json(bp).get("episode") or {}).get("story_en") or "") if bp.exists() else ""
        cap = captions(artist, m["title"], feat, story)
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


STALE_DAYS = 14   # 配信時刻から 2 週間以上たった投稿は出さない（古い計画の出し忘れや、試運転の過去の日付を誤って公開しない）


def cmd_post(week: str, dry: bool, only: set[str] | None = None, private: bool = False, trial: bool = False) -> None:
    # week="all"：まだ投稿していない計画をすべて見る（定期実行はこれ）
    plans = sorted((OUT / "social").rglob("plan.json")) if week == "all" else sorted((OUT / "social" / week).rglob("plan.json"))
    if not plans:
        if week == "all":
            print("   投稿の計画はまだありません")
            return
        sys.exit(f"[エラー] out/social/{week}/ に計画がありません。先に  post_social.py plan --week {week}")
    now = datetime.now(timezone.utc)
    for pp in plans:
        plan = read_json(pp)
        if plan.get("trial") and not trial:
            continue          # 試運転の計画は定期実行では出さない
        if all(p["status"] == "done" for p in plan["posts"]):
            continue
        for p in plan["posts"]:
            if p["status"] == "done":
                continue
            if only and p["platform"] not in only:
                continue
            when = datetime.fromisoformat(p["scheduled_at"].replace("Z", "+00:00"))
            if (now - when).days > STALE_DAYS and not trial:
                print(f"   － {plan['artist']} / {p['platform']}：配信時刻から {STALE_DAYS} 日以上たっているので出しません")
                continue
            # YouTube は公開予約ができるので前もって上げる。他は時刻を過ぎてから投稿する
            if p["platform"] != "youtube" and when > now:
                print(f"   … {plan['artist']} / {p['platform']}：投稿時刻 {p['scheduled_at']} を待っています")
                continue
            if dry:
                print(f"   [ドライラン] {plan['artist']} / {p['platform']} に投稿します：{p['caption'][:60].replace(chr(10), ' ')}…")
                continue
            try:
                res = POSTERS[p["platform"]]({**p, "force_private": private or bool(plan.get("trial"))},
                                             p["artist_slug"], p.get("label_slug") or "drive")
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)}
            if res.get("skipped"):
                print(f"   － {plan['artist']} / {p['platform']}：{res['skipped']}（飛ばしました）")
                continue
            p["result"] = res
            p["status"] = "error" if res.get("error") else "done"
            mark = "✕" if res.get("error") else "○"
            if res.get("error"):
                notify("EtherArchi：SNS 投稿に失敗", f"{plan['artist']} / {p['platform']}：{str(res['error'])[:80]}")
            print(f"   {mark} {plan['artist']} / {p['platform']}：{res.get('error') or res}")
        write_json(pp, plan)


# ---------------------------------------------------------------------------
# 毎日の投稿（決定 94）
# ---------------------------------------------------------------------------
DAILY_DIR = OUT / "social" / "daily"
DAILY_SETTINGS = OUT / "social" / "daily_settings.json"
DAILY_DEFAULT = {"on": False, "per_day": 2, "times_et": [12, 17, 20], "days_ahead": 3}


def daily_settings() -> dict:
    try:
        return {**DAILY_DEFAULT, **(read_json(DAILY_SETTINGS) if DAILY_SETTINGS.exists() else {})}
    except (OSError, ValueError):
        return dict(DAILY_DEFAULT)


def _at_et(day: str, hour: int) -> str:
    from datetime import date as _date, time as _time
    from zoneinfo import ZoneInfo
    t = datetime.combine(_date.fromisoformat(day), _time(hour, 0), tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration(f: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def social_songs() -> list[dict]:
    """投稿に使える曲：DistroKid に登録済みか配信中で、音源とジャケットがあるもの。{song, release, audio, cover}"""
    import _songs
    import publish
    out = []
    for s in _songs.all_songs():
        st = _songs.state(s)
        if st.get("status") not in ("registered", "live") or not s.get("title"):
            continue
        rel = str(st.get("released") or st.get("release_date") or (s.get("daily") or {}).get("date") or s.get("release_at") or "")[:10]
        audio = OUT / "masters" / s["key"] / "master.wav"
        if not audio.exists():
            try:
                folder, a, _ = publish.publish_folder(s)
                audio = folder / a
            except Exception:  # noqa: BLE001
                continue
        cover = _songs.cover_source(s)
        if rel and audio.exists() and cover:
            out.append({"song": s, "release": rel, "audio": audio, "cover": Path(cover)})
    return out


def clip_start_for(item: dict, n: int) -> float:
    """同じ曲を何度か出すときは、切り出す場所を変える（サビ → 2 回目のサビのあたり → 曲の頭）"""
    s = item["song"]
    ch = chorus_start(s["week"], s["slug"]) if s["kind"] != "debut" else 45.0
    dur = _duration(item["audio"]) or 180.0
    opts = [ch, ch + CLIP_SEC + 20, 8.0]
    return max(0.0, min(opts[n % len(opts)], max(0.0, dur - CLIP_SEC - 1)))


def cmd_daily(days: int | None, on: bool, off: bool, per_day: int | None, if_on: bool, dry: bool) -> None:
    from datetime import date as _date, timedelta
    import _songs
    st = daily_settings()
    if on or off or per_day:
        st.update({"on": bool(on or (st.get("on") and not off))} if (on or off) else {})
        if per_day:
            st["per_day"] = max(1, min(3, per_day))
        write_json(DAILY_SETTINGS, st)
        print(f"【結果】SNS の毎日の投稿を{'始めました' if st['on'] else '止めました'}（1 日 {st['per_day']} 本。"
              f"投稿時刻：配信日の曲は 17:00、ほかは {'・'.join(f'{h}:00' for h in [x for x in st['times_et'] if x != 17][:st['per_day']])} ET）")
        if off or (not on and not st["on"]):
            return
    if not st.get("on"):
        if if_on:
            return
        print("   （SNS の毎日の投稿は止めています。始めるときは --on。いまは案だけ作ります）")
    from _common import all_artists
    bg_folders(all_artists())
    days = days or int(st.get("days_ahead") or 3)
    items = social_songs()
    hist_p = DAILY_DIR / "history.json"
    hist = read_json(hist_p) if hist_p.exists() else {}
    made = 0
    for k in range(days):
        day = (_date.today() + timedelta(days=k)).isoformat()
        ddir = DAILY_DIR / day
        if ddir.exists() and any(ddir.rglob("plan.json")):
            continue   # その日の案はもうある
        rel = [x for x in items if x["release"] == day]
        pool = [x for x in items if x["release"] < day]
        picks = [(x, "release") for x in rel]
        used_slugs = {x["song"]["slug"] for x in rel}
        while len(picks) < st["per_day"] and pool:
            pool.sort(key=lambda x: (len(hist.get(x["song"]["key"], [])), (hist.get(x["song"]["key"]) or [""])[-1],
                                     x["song"]["slug"] in used_slugs, x["release"]))
            x = pool.pop(0)
            n = len(hist.get(x["song"]["key"], []))
            picks.append((x, "born" if n % 2 == 0 else "listen"))
            used_slugs.add(x["song"]["slug"])
        if not picks:
            print(f"   － {day}：投稿に使える曲がまだありません（DistroKid に登録した曲から使います）")
            continue
        times = [h for h in st["times_et"] if h != 17] or [12]
        for i, (x, kind) in enumerate(picks, 1):
            s = x["song"]
            artist = load_artist(s["slug"], s.get("label_slug"))
            n = len(hist.get(s["key"], []))
            hour = 17 if kind == "release" else times[(i - 1 - len(rel)) % len(times)]
            folder = ddir / f"{i}_{s['slug']}"
            video = folder / "clip_vertical.mp4"
            brief = read_json(Path(s["brief"])) if s.get("brief") and Path(s["brief"]).exists() else {}
            story_en, story_ja = born_story(brief, artist)
            if kind == "born" and not story_en:
                kind = "listen"
            cap = captions(artist, s["title"], None, story_en, kind)
            for v in cap.values():
                scrub_check(v, artist)
            bg = background_for(s["slug"], _songs.folder_name(s), artist, n)
            start = clip_start_for(x, n)
            step(f"{day} {hour}:00 ET｜{artist['name']}「{s['title']}」（{ {'release': '配信日', 'born': 'この曲が生まれた日', 'listen': '配信中の曲'}[kind]}・"
                 f"{start:.0f} 秒地点から・背景 {bg.name if bg else 'ジャケット'}）")
            if not dry:
                make_clip(x["audio"], x["cover"], start, s["title"], artist["name"], video, bg)
                posts = [{"platform": plat, "scheduled_at": _at_et(day, hour), "video": str(video), "caption": cap[plat],
                          "youtube_title": cap["youtube_title"], "tags": hashtags(artist), "week": s["week"],
                          "artist_slug": s["slug"], "label_slug": s.get("label_slug"), "kind": kind,
                          "store_key": f"daily/{day}/{i}_{s['slug']}/clip_vertical.mp4", "status": "pending", "result": None}
                         for plat in PLATFORMS]
                write_json(folder / "plan.json", {"artist": artist["name"], "title": s["title"], "song": s["key"], "kind": kind, "posts": posts})
                (folder / "captions.md").write_text(
                    f"# {day} {hour}:00 ET — {artist['name']}「{s['title']}」（{kind}）\n\n"
                    + (f"この曲が生まれた日（日本語）：{story_ja}\n\n" if story_ja else "")
                    + "\n\n".join(f"## {k}\n\n```\n{v}\n```" for k, v in cap.items()) + "\n", encoding="utf-8")
                hist.setdefault(s["key"], []).append(day)
                made += 1
    if not dry:
        write_json(hist_p, hist)
    if if_on and not made:
        return   # 定期実行（15 分ごと）：作る案が無ければ何も言わない
    print(f"【結果】SNS の投稿の案を {made} 本作りました（{days} 日先まで・out/social/daily/<日付>/）。"
          + ("投稿時刻を過ぎると、定期実行（15 分ごと）が投稿します" if st.get("on") else "毎日の投稿を止めているので、投稿はしません"))


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
    ap.add_argument("command", choices=["plan", "post", "daily", "refresh-ig"])
    ap.add_argument("--days", type=int, help="daily：何日先までの案を作るか（既定 3）")
    ap.add_argument("--on", action="store_true", help="daily：毎日の投稿を始める")
    ap.add_argument("--off", action="store_true", help="daily：毎日の投稿を止める")
    ap.add_argument("--per-day", type=int, help="daily：1 日の投稿の数（1〜3）")
    ap.add_argument("--if-on", action="store_true", help="daily：毎日の投稿をしていなければ何もしない（定期実行用）")
    ap.add_argument("--week", help="制作週の月曜（plan / post）")
    ap.add_argument("--for", dest="target", help="refresh-ig：組かレーベル（例 light / focus）。省略で共通")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="post：この SNS だけ（カンマ区切り。youtube,instagram,tiktok）")
    ap.add_argument("--private", action="store_true", help="post：YouTube を非公開のまま置く（公開予約もしない）")
    ap.add_argument("--trial", action="store_true", help="post：試運転の計画も対象にする（trial.py が使う。必ず非公開）")
    args = ap.parse_args()
    if args.command == "refresh-ig":
        cmd_refresh_ig(args.target)
        return
    if args.command == "daily":
        cmd_daily(args.days, args.on, args.off, args.per_day, args.if_on, args.dry_run)
        return
    if not args.week:
        sys.exit("[エラー] --week を指定してください（例：--week 2026-10-05）")
    if args.command == "plan":
        cmd_plan(args.week)
    else:
        cmd_post(args.week, args.dry_run, set(args.only.split(",")) if args.only else None, args.private, args.trial)


if __name__ == "__main__":
    main()
