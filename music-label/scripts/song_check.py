#!/usr/bin/env python3
"""
公開の準備の確認ページ（ダッシュボード webapp.py の /song/<曲 ID> と /check が使う）

Suno の曲を取り込んだあと、目で確かめられるようにする：
  1 取り込み     … Suno からダウンロードした曲（聴ける）
  2 音量         … 配信向けに整えた音源（-14 LUFS / -1 dBTP。聴ける）
  3 ジャケット   … 画像そのもの
  4 DistroKid の下書き … 入れる項目の一覧
  5 公開セット   … フォルダの中のファイル（Mac のローカル保存と SSD）
曲を取り込むと、このページがブラウザで自動で開く（publish.py の scan）。進み具合は 5 秒ごとに新しくなる。
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, read_json  # noqa: E402
import _songs  # noqa: E402

E = html.escape
PORT = int(os.environ.get("WEBAPP_PORT", "8765"))
IMG = {".jpg", ".jpeg", ".png", ".webp"}
AUD = {".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif"}


def allowed_roots() -> list[Path]:
    """ページから見せてよいファイルの置き場所（それ以外は出さない）"""
    import _archive as archive
    roots = [OUT, ROOT / "debut_kit"]
    try:
        roots.append(archive.local_root())
        base, _ = archive.root()
        if base is not None:
            roots.append(base)
    except Exception:  # noqa: BLE001
        pass
    return [r.resolve() for r in roots if r]


def file_ok(p: Path) -> bool:
    try:
        rp = p.resolve()
    except Exception:  # noqa: BLE001
        return False
    return rp.is_file() and rp.suffix.lower() in IMG | AUD and any(rp.is_relative_to(r) for r in allowed_roots())


def furl(p: Path | str) -> str:
    return "/file?p=" + quote(str(p))


def _size(p: Path) -> str:
    n = p.stat().st_size
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def steps(song: dict) -> list[dict]:
    """5 つの段階：{name, state(done/run/ng/wait), text, html（中身）, actions}"""
    import publish
    import _archive as archive
    st = _songs.state(song)
    status = st.get("status", "")
    out = []

    # 1 取り込み
    tdir = OUT / "takes" / song["key"]
    sel_p = tdir / "selection.json"
    take = None
    if sel_p.exists():
        sel = read_json(sel_p)
        take = tdir / ((sel.get("decision") or {}).get("selected") or "")
    if take and take.is_file():
        out.append({"name": "取り込み（Suno からダウンロードした曲）", "state": "done",
                    "text": f"{st.get('imported_at', '')[:16].replace('T', ' ')} に取り込みました。元のファイル：{st.get('source', take.name)}",
                    "html": f'<audio controls preload="none" src="{furl(take)}"></audio>'})
    else:
        out.append({"name": "取り込み（Suno からダウンロードした曲）", "state": "wait",
                    "text": "まだ取り込んでいません。Suno でいいねした 1 本を SSD の「楽曲情報」にダウンロードすると、数分で自動で取り込みます。",
                    "actions": [{"action": "scan", "label": "すぐに取り込む"}]})
        return out

    # 2 音量
    master = OUT / "masters" / song["key"] / "master.wav"
    if master.is_file():
        rep_p = master.with_suffix(".report.json")
        rep = read_json(rep_p) if rep_p.exists() else {}
        aft = rep.get("after") or {}
        txt = f"配信向けに音量を整えました（{aft.get('lufs', '？')} LUFS・ピーク {aft.get('true_peak_db', '？')} dBTP。目安は -14 LUFS・-1 dBTP 以下）"
        out.append({"name": "音量（配信向け）", "state": "done", "text": txt,
                    "html": f'<audio controls preload="none" src="{furl(master)}"></audio>'})
    elif status == "waiting_ffmpeg":
        out.append({"name": "音量（配信向け）", "state": "ng", "text": "音量を整える道具（ffmpeg）が Mac に入っていないので止まっています。",
                    "actions": [{"action": "terminal", "key": "ffmpeg", "label": "道具を入れる（ターミナルが開きます）"},
                                {"action": "scan", "label": "もう一度進める"}]})
    else:
        out.append({"name": "音量（配信向け）", "state": "run", "text": "音量を整えています（数分）。",
                    "actions": [{"action": "scan", "label": "もう一度進める"}]})

    # 3 ジャケット
    cover = _songs.cover_source(song)
    if cover and Path(cover).is_file():
        out.append({"name": "ジャケット", "state": "done", "text": f"{Path(cover).name}（3000×3000 で DistroKid に出します）",
                    "html": f'<img class="cover" src="{furl(cover)}" alt="ジャケット">'})
    else:
        act = {"action": "images", "slug": song["slug"], "label": "ジャケットを作る（OpenAI の画像の料金がかかります）",
               "confirm": "ジャケットを作ります（OpenAI の画像の料金がかかります）。よいですか？"} if song["kind"] == "debut" else \
            {"action": "cover", "slug": song["slug"], "label": "ジャケットを作る"}
        why = "OpenAI の鍵が Mac に無いので、自動で作れていません。" if not os.environ.get("OPENAI_API_KEY") else "作っているところか、作れずに止まっています。"
        out.append({"name": "ジャケット", "state": "ng" if status == "waiting_cover" else "run", "text": f"ジャケットがまだありません。{why}",
                    "actions": ([{"action": "terminal", "key": "openai", "label": "OpenAI の鍵を入れる（ターミナルが開きます）"}]
                                if not os.environ.get("OPENAI_API_KEY") else []) + [act, {"action": "scan", "label": "もう一度進める"}]})

    # 4 DistroKid の下書き
    dp = publish.PUB / song["key"] / "DistroKid下書き.json"
    if dp.exists():
        fields = read_json(dp)
        rows = "".join(f"<tr><th>{E(str(k))}</th><td>{E(str(v))}</td></tr>" for k, v in fields.items())
        out.append({"name": "DistroKid の下書き", "state": "done", "text": "DistroKid の登録画面に入れる内容です（「DistroKid に入力する」で自動で入ります）。",
                    "html": f'<table class="kv">{rows}</table>'})
    else:
        out.append({"name": "DistroKid の下書き", "state": "wait", "text": "音量とジャケットがそろうと作られます。"})

    # 5 公開セット
    folder, audio, cover_name = publish.publish_folder(song)
    places = [("Mac のローカル保存", folder)]
    try:
        sf, _ = archive.artist_folder(song["artist"], "music", _songs.folder_name(song))
        if sf is not None:
            places.append(("SSD", sf))
    except Exception:  # noqa: BLE001
        pass
    blocks, ok, has_cover = [], False, False
    for label, f in places:
        files = sorted(x for x in f.iterdir() if x.is_file() and not x.name.startswith(".")) if f.is_dir() else []
        ok = ok or (f / audio).is_file()
        has_cover = has_cover or bool(cover_name and (f / cover_name).is_file())
        lis = "".join(f"<li>{E(x.name)} <small>{_size(x)}</small>"
                      + (f' <a href="{furl(x)}" target="_blank">見る</a>' if x.suffix.lower() in IMG else "")
                      + (f' <a href="{furl(x)}" target="_blank">聴く</a>' if x.suffix.lower() in AUD else "") + "</li>" for x in files)
        blocks.append(f"<h4>{E(label)}：<code>{E(str(f))}</code></h4>" + (f"<ul class='files'>{lis}</ul>" if lis else "<p class='sub'>（まだ空です）</p>"))
    complete = ok and has_cover
    out.append({"name": "公開セット（DistroKid に出すファイル一式）", "state": "done" if complete else ("run" if status else "wait"),
                "text": (f"配信用の音源 {audio}" + (f"・ジャケット {cover_name}" if cover_name else "") + "・歌詞・下書きをまとめたフォルダです。")
                if complete else ("音源は入りました。ジャケットが入ると、そろいます。" if ok else "音量を整えた音源が入ると、ここにまとまります。"),
                "html": "".join(blocks), "actions": [{"action": "open_folder", "id": song["id"], "label": "フォルダを Finder で開く"}]})
    return out


PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>公開の準備</title><style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#22201c;--sub:#6f6a60;--line:#e2ddd2;--accent:#a07a3c;--good:#0c8a0c;--warn:#b07a00;--crit:#c03030}
@media (prefers-color-scheme:dark){:root{--bg:#181715;--card:#22211e;--ink:#ece8df;--sub:#a49e92;--line:#34322d;--accent:#d2ad6b;--good:#3ccf3c;--warn:#f0b429;--crit:#ff6b6b}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
main{max-width:900px;margin:auto;padding:16px} a{color:var(--accent)} .sub{color:var(--sub);font-size:13px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
h1{font-size:20px;margin:8px 0} h2{font-size:16px;margin:0 0 6px;display:flex;gap:8px;align-items:center} h4{margin:10px 0 4px;font-size:13px;font-weight:600}
.badge{font-size:12px;border-radius:99px;padding:1px 10px;border:1px solid}.done{color:var(--good)}.ng{color:var(--crit)}.run,.wait{color:var(--warn)}
img.cover{width:min(320px,100%);border-radius:8px;border:1px solid var(--line);display:block;margin-top:8px} audio{width:100%;margin-top:8px}
table.kv{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px} .kv th,.kv td{border-top:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
.kv th{white-space:nowrap;color:var(--sub);font-weight:500;width:30%} code{font-size:12px;word-break:break-all} ul.files{margin:4px 0;padding-left:1.2em;font-size:13px}
button{font:inherit;border-radius:8px;padding:6px 14px;cursor:pointer;border:1px solid var(--accent);background:var(--card);color:var(--ink);margin:8px 8px 0 0}
button.primary{background:var(--accent);color:#fff} #job{margin-top:8px;font-size:13px;color:var(--sub)}
</style></head><body><main>
<p><a href="/">← ダッシュボードへ</a>　<a href="/check">公開の準備の一覧</a></p>
__BODY__
<div id="job"></div></main>
<script>
const TOKEN="__TOKEN__";
async function act(b){const p=JSON.parse(b.dataset.p);if(p.confirm&&!confirm(p.confirm))return;b.disabled=true;
  try{const r=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json","X-Label-Token":TOKEN},body:JSON.stringify(p)});
    const j=await r.json();if(!r.ok){alert(j.error||"実行できませんでした");return}
    document.getElementById("job").textContent="始めました。終わると、このページが新しくなります。";
    for(;;){await new Promise(r=>setTimeout(r,2000));const s=await (await fetch("/api/jobs/"+j.job)).json();
      if(s.state!=="running"){location.reload();break}}}finally{b.disabled=false}}
document.addEventListener("click",e=>{const b=e.target.closest("button[data-p]");if(b)act(b)});
// 進行中は 5 秒ごとに新しくする（音を聴いている間は待つ）
if(__AUTO__){setInterval(()=>{if([...document.querySelectorAll("audio")].every(a=>a.paused))location.reload()},5000)}
</script></body></html>"""

LABEL = {"done": "✓ 済み", "run": "… 進行中", "ng": "！ 止まっています", "wait": "○ まだ"}


def _btn(a: dict, primary: bool = False) -> str:
    p = {k: v for k, v in a.items() if k != "label"}
    return f'<button class="{"primary" if primary else ""}" data-p="{E(json.dumps(p, ensure_ascii=False))}">{E(a["label"])}</button>'


def episode_html(ep: dict | None) -> str:
    """この曲のエピソード（決定 33）：テーマ・いつどこで・見た・聞いた・考えた・感じた・思った"""
    if not ep:
        return ""
    li = lambda xs: "、".join(xs) if isinstance(xs, list) else str(xs or "")  # noqa: E731
    rows = [("テーマ", ep.get("theme")), ("いつ・どこで", ep.get("when_where")), ("見たもの", li(ep.get("saw"))),
            ("聞いたもの", li(ep.get("heard"))), ("考えたこと", ep.get("thought")), ("感じたこと", ep.get("felt")),
            ("そして思ったこと", ep.get("realized")), ("生まれたもと", ep.get("source"))]
    tbl = "".join(f"<tr><th>{E(k)}</th><td>{E(str(v))}</td></tr>" for k, v in rows if v)
    return (f'<section><h2>この曲のエピソード</h2><p>{E(ep.get("story_ja", ""))}</p><table class="kv">{tbl}</table>'
            + (f'<p class="sub">英語の紹介：{E(ep["story_en"])}</p>' if ep.get("story_en") else "") + "</section>")


def song_page(song_id: str, token: str) -> str | None:
    song = _songs.by_id(song_id)
    if song is None:
        return None
    ss = steps(song)
    secs = []
    for i, s in enumerate(ss, 1):
        acts = "".join(_btn(a, j == 0 and s["state"] == "ng") for j, a in enumerate(s.get("actions") or []))
        secs.append(f'<section><h2>{i}. {E(s["name"])} <span class="badge {s["state"]}">{LABEL[s["state"]]}</span></h2>'
                    f'<p>{E(s["text"])}</p>{s.get("html", "")}{acts}</section>')
    all_done = len(ss) == 5 and all(s["state"] == "done" for s in ss)
    head = (f'<h1>{E(song["artist"])}「{E(song["title"])}」の公開の準備</h1>'
            f'<p class="sub">{E(song["id"])}・' + ("ここまで全部できています。中身を確かめて、DistroKid に進んでください。" if all_done
                                                  else "自動で進んでいます。このページは 5 秒ごとに新しくなります。") + "</p>")
    if all_done:
        head += "<section><h2>次にやること</h2><p>中身を確かめたら、DistroKid の登録画面に自動で入れます（最後の登録ボタンは Gon さんが押します）。</p>" + \
            _btn({"action": "fill_distrokid", "id": song["id"], "label": "DistroKid に入力する（ブラウザが開きます）"}, True) + "</section>"
    body = head + episode_html(song.get("episode")) + "".join(secs)
    return PAGE.replace("__BODY__", body).replace("__TOKEN__", token).replace("__AUTO__", "false" if all_done else "true")


def index_page(token: str) -> str:
    rows = []
    for s in _songs.all_songs():
        st = _songs.state(s).get("status")
        if not st or str(s["week"]).startswith("2000-"):
            continue
        ja = {"imported": "取り込み済み", "waiting_ffmpeg": "！ 音量の道具が無い", "waiting_cover": "！ ジャケット待ち", "ready": "✓ DistroKid に出せる",
              "registered": "DistroKid 登録済み", "live": "配信中"}.get(st, st)
        rows.append(f'<li><a href="/song/{quote(s["id"])}">{E(s["artist"])}「{E(s["title"])}」</a> <span class="sub">{E(s["id"])}・{E(ja)}</span></li>')
    body = "<h1>公開の準備の一覧</h1>" + (f"<section><ul>{''.join(rows)}</ul></section>" if rows else
                                        "<section><p>取り込んだ曲はまだありません。Suno でいいねした 1 本を SSD の「楽曲情報」にダウンロードすると、ここに出ます。</p></section>")
    return PAGE.replace("__BODY__", body).replace("__TOKEN__", token).replace("__AUTO__", "true")


def open_window(song: dict) -> None:
    """曲を取り込んだら、確認ページをブラウザで開く（Mac のとき。ダッシュボードが動いていれば）"""
    if sys.platform != "darwin" or os.environ.get("POSTDOG_NO_OPEN"):
        return
    import socket
    url = f"http://localhost:{PORT}/song/{quote(song['id'])}"
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
    except OSError:
        print(f"   － ダッシュボードが動いていないので、確認ページは開きませんでした（{url}）")
        return
    subprocess.Popen(["open", url])
    print(f"   ○ 公開の準備の確認ページを開きました：{url}")
