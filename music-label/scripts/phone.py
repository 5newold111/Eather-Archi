#!/usr/bin/env python3
"""
スマホ用のページ（ダッシュボード webapp.py の /phone と /phone/<曲 ID> が使う）

外出先でも、スマホだけで「作曲 → 取り込み → DistroKid」を進めるためのページ。Mac は家で動かしたまま（決定 28）。
  1 Suno で作る      … Suno の作曲画面（「高度な」）の欄の順に、入れる中身を「写す」ボタンで 1 つずつコピーする。
                       数字やオン・オフは、そのまま画面で選ぶ値を出す（設計図は suno_design.py と同じ）
  2 曲を送る         … スマホにダウンロードした 1 本を、ここから Mac に送る（Mac の「楽曲情報」に入り、取り込みが始まる）
  3 DistroKid に出す … 下書きの各欄を「写す」ボタンで。配信用の音源とジャケットはスマホに保存してから DistroKid で選ぶ
  4 登録の記録       … 最後の登録ボタンを押したら「登録した」を押す
Create と DistroKid の最後の登録ボタンは、今までどおり Gon さんが押す（システムは押さない）。
"""
from __future__ import annotations

import html
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json  # noqa: E402
import _songs  # noqa: E402

E = html.escape
DONE = ("registered", "live")
GENDER = {"male": "男（Male）", "female": "女（Female）", None: "選ばない（両方とも外す）", "unset": "触らない（インスト）"}

EXTRA = """<style>
.field{border-top:1px solid var(--line);padding:10px 0}.field:first-of-type{border-top:0}
.field b{display:block;font-size:14px}.field .v{font-size:13px;color:var(--sub);white-space:pre-wrap;word-break:break-word;max-height:7.5em;overflow:auto;
 background:var(--bg);border-radius:8px;padding:6px 8px;margin-top:4px}
.field .set{font-size:15px;font-weight:600;color:var(--ink)}
.copy{min-height:40px;min-width:88px}.copy.ok{background:var(--good);border-color:var(--good);color:#fff}
.big{display:block;width:100%;min-height:48px;margin:10px 0 0;text-align:center;text-decoration:none;line-height:46px;border-radius:10px;
 border:1px solid var(--accent);color:var(--ink)}
.big.primary{background:var(--accent);color:#fff}
ol.how{padding-left:1.3em;margin:6px 0}ol.how li{margin:2px 0}
input[type=file]{font:inherit;margin-top:8px;max-width:100%}
.note{font-size:13px;color:var(--sub)}
</style>
<script>
// コピー：http のページ（外から合鍵で開いたとき）はクリップボードの新しい仕組みが使えないので、選んでコピーする昔の方法も使う
function copyText(t){
  if(navigator.clipboard&&window.isSecureContext)return navigator.clipboard.writeText(t);
  return new Promise((ok,ng)=>{const a=document.createElement("textarea");a.value=t;a.setAttribute("readonly","");
    a.style.position="fixed";a.style.top="-1000px";document.body.appendChild(a);a.focus();a.setSelectionRange(0,t.length);
    const r=document.execCommand("copy");document.body.removeChild(a);r?ok():ng()})}
document.addEventListener("click",async e=>{const b=e.target.closest("button.copy");if(!b)return;
  const t=document.getElementById(b.dataset.src).textContent;
  try{await copyText(t);b.classList.add("ok");const was=b.textContent;b.textContent="写しました";setTimeout(()=>{b.classList.remove("ok");b.textContent=was},1600)}
  catch(_){alert("写せませんでした。文を長押しして選び、コピーしてください")}});
// 曲を送る：選んだファイルをそのまま Mac に送る（Mac の「楽曲情報」に入り、取り込みが始まる）
async function sendSong(id){
  const f=document.getElementById("up-"+id).files[0],msg=document.getElementById("upmsg-"+id);if(!f){alert("曲のファイルを選んでください");return}
  msg.textContent=`送っています…（${(f.size/1048576).toFixed(1)} MB）`;
  try{const r=await fetch("/api/upload?id="+encodeURIComponent(id),{method:"POST",headers:{"X-Label-Token":TOKEN,"X-Filename":encodeURIComponent(f.name)},body:f});
    const j=await r.json();if(!r.ok){msg.textContent="";alert(j.error||"送れませんでした");return}
    msg.textContent="Mac に届きました。取り込みが終わると、公開の準備のページに進みます。";
    for(;;){await new Promise(r=>setTimeout(r,3000));const s=await (await fetch("/api/jobs/"+j.job)).json();
      if(s.state!=="running"){location.href="/song/"+encodeURIComponent(id);break}}}
  catch(e){msg.textContent="";alert("送れませんでした（"+e+"）。電波のよいところでもう一度")}}
</script>"""


def _field(n: str, name: str, value: str, copy: bool = True, how: str = "") -> str:
    fid = f"f-{n}"
    body = (f'<div class="v" id="{fid}">{E(value)}</div>' if copy else f'<div class="set">{E(value)}</div>')
    btn = f'<button class="copy" data-src="{fid}">写す</button>' if copy and value else ""
    tip = f'<div class="note">{E(how)}</div>' if how else ""
    return f'<div class="field"><b>{E(name)}</b>{tip}{body}{btn}</div>'


def _onoff(v) -> str:
    return "オン" if v else "オフ"


def suno_section(song: dict) -> str:
    import suno_design
    d = suno_design.design(song)
    sc, total = suno_design.score(d)
    rows = [
        _field("lyrics", "1 歌詞（Lyrics）", d["lyrics"], how="「高度な」を選んでから、歌詞の欄に貼る"),
        _field("style", "2 スタイル（Styles）", d["style"], how="下に出る「Add style」の候補は押さない"),
        _field("exclude", "3 除外するスタイル（Exclude styles）", d["exclude"], how="「その他のオプション（More Options）」を開いて貼る"),
        _field("gender", "4 声の性別（Vocal Gender）", GENDER.get(d["vocal_gender"], str(d["vocal_gender"])), copy=False),
        _field("len", "5 間隔（Duration）", _songs.fmt_len(d["length_sec"]), copy=False,
               how="スライダーか、時間の欄に入れる（例 3:10）"),
        _field("max", "6 Max Mode", _onoff(d["max_mode"]), copy=False),
        _field("weird", "7 Weirdness", f"{d['weirdness']}%" if d["weirdness"] is not None else "触らない", copy=False),
        _field("infl", "8 スタイルの影響度（Style Influence）", f"{d['style_influence']}%" if d["style_influence"] is not None else "触らない", copy=False),
        _field("var", "9 バラエティ（Variety）", f"{d['variety']}（0〜4）" if d["variety"] is not None else "触らない", copy=False),
        _field("taste", "10 あなたの好み（Personalize）", _onoff(d["your_taste"]), copy=False, how="「My Taste」のボタンは押さない"),
        _field("title", "11 曲名（Song Title）", d["title"]),
    ]
    if d["instrumental"]:
        rows.insert(0, _field("inst", "インスト（Instrumental）", "オン", copy=False))
    warn = [t for k, s, t in d["checks"] if s == "warn"]
    return ('<section><h2>1. Suno で作る</h2>'
            f'<p class="note">イメージの写し具合 {sc}／{total}。上から順に、Suno の作曲画面（「高度な」）の欄と同じ並びです。</p>'
            + "".join(f'<p class="note">！ {E(w)}</p>' for w in warn)
            + '<a class="big" href="https://suno.com/create" target="_blank" rel="noopener">Suno の作曲画面を開く ↗</a>'
            + "".join(rows)
            + '<ol class="how"><li>全部入れたら <b>Create</b> は Gon さんが押す（ロボット確認が出たら自分でチェック。続けて何曲も作らない）</li>'
              '<li>2 つを聴いて、気に入った方に<b>いいね</b> → その 1 本を<b>ダウンロード</b>（WAV があれば WAV）</li>'
              '<li>下の「2. 曲を送る」で Mac に送る</li></ol></section>')


def upload_section(song: dict) -> str:
    sid = E(song["id"])
    return ('<section><h2>2. 曲を送る（スマホ → Mac）</h2>'
            '<p class="note">スマホにダウンロードした 1 本を選んで送ります。Mac の「楽曲情報」に入り、取り込み・音量・ジャケット・下書きが自動で進みます。</p>'
            f'<input type="file" id="up-{sid}" accept="audio/*,.wav,.mp3,.m4a,.flac,.aiff,.aif">'
            f'<button class="primary" onclick="sendSong(\'{sid}\')">Mac に送る</button><div class="note" id="upmsg-{sid}"></div>'
            f'<a class="big" href="/song/{quote(song["id"])}">公開の準備のページを見る</a></section>')


def distrokid_section(song: dict) -> str:
    import publish
    import song_check
    dp = publish.PUB / song["key"] / "DistroKid下書き.json"
    if not dp.exists():
        return ('<section><h2>3. DistroKid に出す</h2><p class="note">曲を取り込んで、音量とジャケットがそろうと、ここに下書きが出ます。</p></section>')
    fields = read_json(dp)
    rows = "".join(_field(f"dk{i}", str(k), v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                   for i, (k, v) in enumerate(fields.items()))
    folder, audio, cover = publish.publish_folder(song)
    files = []
    for name, label in ((audio, "配信用の音源（WAV）"), (cover, "ジャケット（3000×3000）")):
        f = folder / name if name else None
        if f and f.is_file():
            files.append(f'<a class="big" href="{song_check.furl(f)}&dl=1" download="{E(name)}">{E(label)}をスマホに保存</a>')
        else:
            files.append(f'<p class="note">○ {E(label)}はまだありません</p>')
    return ('<section><h2>3. DistroKid に出す</h2>'
            '<a class="big" href="https://distrokid.com/new/" target="_blank" rel="noopener">DistroKid の登録画面を開く ↗</a>'
            + "".join(files)
            + '<p class="note">保存した音源とジャケットは、DistroKid の画面のファイルを選ぶところで「ファイル」から選びます。</p>'
            + rows
            + '<ol class="how"><li>Artist already in 〜?（5 つ）はすべて <b>No</b>（同じ組の 2 曲目からは下書きのとおり）</li>'
              '<li>Extras はチェックしない。Important checkboxes は文を読んで自分でチェック（Suno の Pro で作った曲か確かめる）</li>'
              '<li><b>最後の登録ボタン</b>は Gon さんが押す → 下の「登録した」を押す</li></ol></section>')


def record_section(song: dict, token: str) -> str:
    import song_check
    today = date.today().isoformat()
    return ('<section><h2>4. 登録の記録</h2><p class="note">DistroKid の最後の登録ボタンを押したら、配信日を選んで押します。</p>'
            f'<input type="date" id="rd" value="{today}"> '
            + song_check._btn({"action": "registered", "id": song["id"], "label": "DistroKid に登録した",
                             "confirm": "DistroKid の最後の登録ボタンを押しましたか？ 登録したと記録します"}, True).replace(
                "<button ", '<button onclick="this.dataset.p=JSON.stringify({...JSON.parse(this.dataset.p),date:document.getElementById(\'rd\').value})" ', 1)
            + '</section>')


def mac_section(song: dict) -> str:
    import song_check
    return ('<section><h2>Mac の画面を遠隔で見ているとき</h2><p class="note">画面共有で Mac を操作しているときは、今までどおり Mac の専用の Chrome に自動で入れられます。</p>'
            + song_check._btn({"action": "fill_suno", "id": song["id"], "label": "Mac の Chrome で Suno に入力する"})
            + song_check._btn({"action": "fill_distrokid", "id": song["id"], "label": "Mac の Chrome で DistroKid に入力する"})
            + '</section>')


def phone_page(song_id: str, token: str) -> str | None:
    import song_check
    song = _songs.by_id(song_id)
    if song is None:
        return None
    st = _songs.state(song).get("status") or ""
    head = (f'<p><a href="/phone">← スマホのページの一覧</a></p><h1>{E(song["artist"])}「{E(song["title"])}」</h1>'
            f'<p class="sub">{E(song["id"])}・' + ("DistroKid に登録済みです" if st in DONE else "上から順に進めます") + "</p>")
    secs = [] if st in DONE else ([suno_section(song), upload_section(song)] if not st else [upload_section(song)])
    if st and st not in DONE:
        secs += [distrokid_section(song), record_section(song, token)]
    secs.append(mac_section(song))
    body = EXTRA + head + "".join(secs)
    return song_check.PAGE.replace("<title>公開の準備</title>", "<title>スマホで進める</title>").replace(
        "__BODY__", body).replace("__TOKEN__", token).replace("__AUTO__", "false")


def phone_index(token: str) -> str:
    import song_check
    todo, done = [], []
    for s in _songs.all_songs():
        if not s.get("title") or str(s.get("week", "")).startswith("2000-"):
            continue
        st = _songs.state(s).get("status") or ""
        ja = {"": "Suno で作る", "imported": "取り込み済み", "waiting_ffmpeg": "！ 音量の道具が無い（Mac で）",
              "waiting_cover": "！ ジャケット待ち", "ready": "DistroKid に出せる"}.get(st, "登録済み" if st in DONE else st)
        li = f'<li><a href="/phone/{quote(s["id"])}">{E(s["artist"])}「{E(s["title"])}」</a> <span class="sub">{E(s["id"])}・{E(ja)}</span></li>'
        (done if st in DONE else todo).append(li)
    body = (EXTRA + "<h1>スマホで進める</h1><p class='sub'>曲を選ぶと、Suno に写す中身・曲を送る・DistroKid に写す中身が順に出ます。</p>"
            + f"<section><h2>これから</h2><ul>{''.join(todo) or '<li>ありません</li>'}</ul></section>"
            + (f"<section><h2>登録済み</h2><ul>{''.join(done)}</ul></section>" if done else ""))
    return song_check.PAGE.replace("<title>公開の準備</title>", "<title>スマホで進める</title>").replace(
        "__BODY__", body).replace("__TOKEN__", token).replace("__AUTO__", "false")
