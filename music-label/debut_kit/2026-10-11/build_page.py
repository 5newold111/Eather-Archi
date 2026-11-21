#!/usr/bin/env python3
"""
デビュー 5 曲の公開手順ページ（index.html）を kit.json と設定書から作る。

  python3 debut_kit/2026-10-11/build_page.py   → debut_kit/2026-10-11/index.html
"""
from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
kit = json.loads((HERE / "kit.json").read_text(encoding="utf-8"))
E = html.escape


def spec(slug: str) -> dict:
    a = json.loads((ROOT / "templates" / "artists" / f"{slug}.json").read_text(encoding="utf-8"))
    return {"never": (a.get("composition_habits") or {}).get("never") or [],
            "drive": (a.get("sound") or {}).get("drive_spec") or {},
            "persona": a["vocal"].get("sex") not in (None, "none"),
            "formation": a.get("formation")}


def copy_block(sid: str, label: str, text: str, pre: bool = True, open_: bool = False) -> str:
    body = f'<pre id="{sid}">{E(text)}</pre>' if pre else f'<p class="inline" id="{sid}">{E(text)}</p>'
    return (f'<details class="field"{" open" if open_ else ""}><summary><span class="flabel">{E(label)}</span>'
            f'<span class="count">{len(text)} 文字</span>'
            f'<button type="button" class="copy" data-target="{sid}">コピー</button></summary>{body}</details>')


cards = []
for n, s in enumerate(kit["songs"], 1):
    sp = spec(s["slug"])
    cat = f"PDR-{n:03d}"
    sid = s["slug"]
    lang = s["language"] + ("（短い声の切れ端だけ。Instrumental にはチェックしない）" if s["slug"] == "hollowplan" else "")
    dk_rows = [
        ("Artist / Band name", s["artist"], "初回は「新しいアーティスト」として作る。綴りは大文字のまま"),
        ("Song title", s["title"], "feat. などは入れない"),
        ("Record label", kit["label"], ""),
        ("Language", lang, ""),
        ("Primary genre", s["genre"][0], ""),
        ("Secondary genre", s["genre"][1], ""),
        ("Explicit", "No（Clean）", ""),
        ("Songwriter", "あなたの本名", "DistroKid の決まりで本名が必要"),
        ("AI の申告", "作曲・ボーカル・演奏・歌詞の一部に AI を使用", "質問が出たら、使ったものをすべて選ぶ"),
        ("Cover art", f"covers/{sid}.jpg（3000×3000）", "上のジャケットのフルサイズ"),
    ]
    dk = "".join(f'<tr><th scope="row">{E(k)}</th><td><span class="val" id="{sid}-dk{i}">{E(v)}</span>'
                 + ("" if i in (7, 8, 9) else f'<button type="button" class="copy" data-target="{sid}-dk{i}">コピー</button>')
                 + (f'<span class="note">{E(note)}</span>' if note else "") + "</td></tr>"
                 for i, (k, v, note) in enumerate(dk_rows))
    d = sp["drive"]
    listen = [f"{d.get('hook_within_sec', 30)} 秒以内にサビ（フック）が来る",
              f"長さが {d.get('length_sec_min', 150) // 60} 分 {d.get('length_sec_min', 150) % 60:02d} 秒〜{d.get('length_sec_max', 210) // 60} 分 {d.get('length_sec_max', 210) % 60:02d} 秒",
              "途中で急に無音にならない・最後が自然に終わる"] + [f"入っていない：{x}" for x in sp["never"][:4]]
    checks = ["Suno で 2〜4 テイク作った", "1 本選んで WAV で保存した", "DistroKid に登録した"]
    check_html = "".join(f'<label class="check"><input type="checkbox" id="{sid}-c{i}" data-key="{sid}-c{i}"> {E(c)}</label>'
                         for i, c in enumerate(checks))
    alts = "／".join(s["title_alt"])
    cards.append(f'''
<article class="song" id="{sid}">
  <div class="cover">
    <img src="covers/{sid}_preview.jpg" data-full="covers/{sid}.jpg" alt="{E(s['artist'])}『{E(s['title'])}』のジャケット" width="600" height="600" loading="lazy">
    <button type="button" class="ghost full" data-img="{sid}">フルサイズを表示（長押し・右クリックで保存）</button>
  </div>
  <div class="body">
    <p class="cat"><span>{cat}</span><span>{s['bpm']} BPM</span><span>{E(sp['formation'] or '')}</span></p>
    <h2><span class="artist">{E(s['artist'])}</span><span class="title">{E(s['title'])}</span></h2>
    <p class="why">{E(s['why_ja'])}</p>
    <p class="alts">曲名の別案：{E(alts)}</p>

    <h3>1. Suno に貼る（Custom モード）</h3>
    <div class="fields">
      {copy_block(sid + "-title", "Song Title", s["title"], pre=False, open_=True)}
      {copy_block(sid + "-style", "Style of Music", s["style"], open_=True)}
      {copy_block(sid + "-exclude", "Exclude Styles（More Options の中）", s["exclude"], open_=True)}
      {copy_block(sid + "-lyrics", "Lyrics", s["lyrics"])}
    </div>
    {"<p class='tip'>声をこの先の曲でもそろえるため、気に入ったテイクから Suno の Persona を作っておく（2 曲目から使う）。</p>" if sp["persona"] else "<p class='tip'>歌のない曲。声の切れ端（still here）が言葉として聞き取れすぎるテイクは避ける。</p>"}

    <h3>2. 聴いて 1 本選ぶ</h3>
    <ul class="listen">{"".join(f"<li>{E(x)}</li>" for x in listen)}</ul>

    <h3>3. DistroKid に入れる</h3>
    <div class="tablewrap"><table class="dk"><tbody>{dk}</tbody></table></div>

    <div class="checks" role="group" aria-label="{E(s['artist'])} の進み具合">{check_html}</div>
  </div>
</article>''')

page = f'''<title>Postdog Debut Desk</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+JP:wght@400;500;700&display=swap">
<style>
/* layout: 1 枚の作業台。上に今日の流れ、下に 5 曲のカード（ジャケット左・作業右、狭い画面では縦に積む） */
:root {{
  --paper: #EEF0EC;   /* 郵便の封筒の灰白 */
  --sheet: #F8F9F6;
  --ink: #1C2127;
  --muted: #5D6670;
  --line: #CFD4D0;
  --post: #2B4C9B;    /* 消印の青 */
  --stamp: #B4532A;   /* 封蝋の赤茶。人がする作業の印 */
  --code: #E4E8E3;
  --display: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --body: "IBM Plex Sans JP", "Hiragino Sans", "Noto Sans JP", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --paper: #121518; --sheet: #1A1E22; --ink: #E7E9E5; --muted: #A1A9B0; --line: #2F353A;
  --post: #93ACEB; --stamp: #E0895F; --code: #23282D; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --paper: #121518; --sheet: #1A1E22; --ink: #E7E9E5; --muted: #A1A9B0; --line: #2F353A;
  --post: #93ACEB; --stamp: #E0895F; --code: #23282D; color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ background: var(--paper); color: var(--ink); font-family: var(--body); font-size: 15px; line-height: 1.7; }}
.wrap {{ max-width: 1120px; margin: 0 auto; padding-inline: 20px; padding-block: 28px 64px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; }}
.wrap > * {{ min-width: 0; }}
header {{ display: grid; gap: 10px; }}
.eyebrow {{ font-family: var(--mono); font-size: 12px; letter-spacing: .14em; color: var(--post); text-transform: uppercase; margin: 0; }}
h1 {{ font-family: var(--display); font-stretch: 72%; font-weight: 700; font-size: clamp(34px, 6vw, 58px); line-height: 1.02; margin: 0; letter-spacing: .01em; text-wrap: balance; }}
.lead {{ max-width: 64ch; margin: 0; color: var(--muted); }}
.flow {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 12px; margin: 0; padding: 0; list-style: none; counter-reset: step; }}
.flow li {{ background: var(--sheet); border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; display: grid; gap: 4px; min-width: 0; }}
.flow b {{ font-family: var(--display); font-stretch: 80%; font-size: 19px; }}
.flow .t {{ font-family: var(--mono); font-size: 12px; color: var(--stamp); }}
.flow p {{ margin: 0; font-size: 13.5px; color: var(--muted); }}
.notes {{ border-left: 3px solid var(--stamp); padding: 4px 0 4px 16px; display: grid; gap: 6px; max-width: 80ch; }}
.notes p {{ margin: 0; font-size: 14px; }}
.song {{ display: grid; grid-template-columns: minmax(0, 360px) minmax(0, 1fr); gap: 28px; background: var(--sheet); border: 1px solid var(--line); border-radius: 8px; padding: 24px; }}
@media (max-width: 820px) {{ .song {{ grid-template-columns: minmax(0, 1fr); padding: 18px; }} }}
.cover {{ display: grid; gap: 8px; align-content: start; }}
.cover img {{ width: 100%; height: auto; aspect-ratio: 1; border-radius: 3px; display: block; background: var(--code); }}
.body {{ min-width: 0; display: grid; gap: 10px; align-content: start; }}
.cat {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 0; font-family: var(--mono); font-size: 12px; color: var(--muted); letter-spacing: .06em; }}
.cat span:first-child {{ color: var(--post); }}
h2 {{ margin: 0; display: grid; gap: 2px; text-wrap: balance; }}
h2 .artist {{ font-family: var(--display); font-stretch: 70%; font-weight: 700; font-size: 30px; letter-spacing: .04em; line-height: 1.05; }}
h2 .title {{ font-family: var(--display); font-stretch: 100%; font-weight: 400; font-size: 20px; color: var(--muted); }}
.why {{ margin: 0; max-width: 62ch; }}
.alts {{ margin: 0; font-size: 13px; color: var(--muted); }}
h3 {{ margin: 14px 0 0; font-size: 14px; letter-spacing: .04em; color: var(--stamp); }}
.fields {{ display: grid; gap: 8px; }}
.field {{ border: 1px solid var(--line); border-radius: 6px; background: var(--paper); min-width: 0; }}
.field summary {{ display: flex; align-items: center; gap: 10px; padding: 8px 10px; cursor: pointer; list-style: none; }}
.field summary::-webkit-details-marker {{ display: none; }}
.flabel {{ font-weight: 700; font-size: 13.5px; }}
.count {{ font-family: var(--mono); font-size: 11px; color: var(--muted); margin-left: auto; }}
.field pre, .field .inline {{ margin: 0; padding: 10px 12px 12px; font-family: var(--mono); font-size: 12.5px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; background: var(--code); border-top: 1px solid var(--line); border-radius: 0 0 6px 6px; max-height: 340px; overflow: auto; }}
button {{ font: inherit; }}
.copy {{ font-family: var(--mono); font-size: 12px; border: 1px solid var(--post); color: var(--post); background: transparent; border-radius: 4px; padding: 3px 10px; cursor: pointer; white-space: nowrap; }}
.copy:hover, .copy:focus-visible {{ background: var(--post); color: var(--sheet); outline: none; }}
.copy.done {{ background: var(--post); color: var(--sheet); }}
.ghost {{ font-size: 12.5px; border: 1px dashed var(--line); background: transparent; color: var(--muted); border-radius: 4px; padding: 6px 8px; cursor: pointer; }}
.ghost:focus-visible, .check input:focus-visible {{ outline: 2px solid var(--post); outline-offset: 2px; }}
.tip {{ margin: 0; font-size: 13px; color: var(--muted); }}
.listen {{ margin: 0; padding-left: 1.2em; display: grid; gap: 2px; font-size: 14px; }}
.tablewrap {{ overflow-x: auto; }}
.dk {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
.dk th {{ text-align: left; font-weight: 500; color: var(--muted); padding: 7px 12px 7px 0; border-bottom: 1px solid var(--line); white-space: nowrap; vertical-align: top; font-family: var(--mono); font-size: 12px; }}
.dk td {{ padding: 7px 0; border-bottom: 1px solid var(--line); }}
.dk .val {{ font-weight: 500; margin-right: 8px; }}
.dk .note {{ display: block; font-size: 12px; color: var(--muted); }}
.checks {{ display: flex; flex-wrap: wrap; gap: 8px 18px; padding-top: 10px; border-top: 1px dashed var(--line); margin-top: 6px; }}
.check {{ display: inline-flex; gap: 6px; align-items: center; font-size: 13.5px; cursor: pointer; }}
.check input {{ accent-color: var(--stamp); width: 16px; height: 16px; }}
footer {{ font-size: 13px; color: var(--muted); max-width: 80ch; display: grid; gap: 6px; }}
footer p {{ margin: 0; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style>
<div class="wrap">
<header>
  <p class="eyebrow">Postdog Records · Debut 2026-10</p>
  <h1>Postdog Debut Desk</h1>
  <p class="lead">5 組のデビュー曲を、今日のうちに DistroKid へ出すための作業台です。各曲のカードの上から順に、コピーして貼るだけで進められます。作曲指示と歌詞には実在のアーティスト名・機種名は入れていません。</p>
</header>

<ol class="flow" aria-label="今日の流れ">
  <li><span class="t">約 60 分</span><b>Suno で作る</b><p>Custom モードで 5 曲 × 2〜4 テイク。Pro か Premier のプランで作った曲だけが配信に使える。</p></li>
  <li><span class="t">約 30 分</span><b>聴いて選ぶ</b><p>各曲の基準で 1 本ずつ選び、WAV で保存する。</p></li>
  <li><span class="t">約 40 分</span><b>DistroKid に登録</b><p>5 つのアーティスト名を使うので、名前を 5 つ以上持てるプランが必要。公開日はいちばん早い日を選ぶ。</p></li>
  <li><span class="t">登録の後</span><b>Claude に知らせる</b><p>選んだテイクと ISRC を教えてもらえれば、管理表と配信の予定を合わせる。</p></li>
</ol>

<div class="notes">
  <p>ストアに並ぶまでは、登録から数日かかることがあります（サービスごとに審査があるため）。今日できるのは「登録して公開を予約する」ところまでです。</p>
  <p>このあと自動の制作では 10/28 の配信枠が予定されています。今日の 5 曲を出したら、その枠は 2 曲目として作るよう私が合わせます。</p>
  <p>ジャケットは画像生成の鍵なしで作った下書きです。写真のジャケットに差し替える場合は、配信の後でも DistroKid から変更できます。</p>
</div>

{"".join(cards)}

<footer>
  <p>チェックの状態は、この端末のブラウザにだけ残ります。</p>
  <p>元のファイル：music-label/debut_kit/2026-10-11/（kit.json・covers/・make_covers.py）</p>
</footer>
</div>
<script>
(function () {{
  function copyText(el, btn) {{
    var text = el.textContent;
    var done = function () {{ btn.textContent = "コピーしました"; btn.classList.add("done");
      setTimeout(function () {{ btn.textContent = "コピー"; btn.classList.remove("done"); }}, 1600); }};
    var fallback = function () {{ var r = document.createRange(); r.selectNodeContents(el);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); btn.textContent = "選択しました。コピーしてください"; }};
    try {{ navigator.clipboard.writeText(text).then(done, fallback); }} catch (e) {{ fallback(); }}
  }}
  document.querySelectorAll(".copy").forEach(function (b) {{
    b.addEventListener("click", function (ev) {{ ev.preventDefault(); ev.stopPropagation();
      var el = document.getElementById(b.dataset.target); var d = el.closest("details"); if (d) d.open = true; copyText(el, b); }});
  }});
  document.querySelectorAll(".full").forEach(function (b) {{
    b.addEventListener("click", function () {{
      var img = document.querySelector("#" + b.dataset.img + " .cover img");
      if (img.dataset.full && img.src.indexOf(img.dataset.full) === -1) {{ img.src = img.dataset.full; b.textContent = "フルサイズ（3000×3000）を表示中"; }}
    }});
  }});
  document.querySelectorAll(".check input").forEach(function (c) {{
    try {{ c.checked = localStorage.getItem("pdd:" + c.dataset.key) === "1"; }} catch (e) {{}}
    c.addEventListener("change", function () {{ try {{ localStorage.setItem("pdd:" + c.dataset.key, c.checked ? "1" : "0"); }} catch (e) {{}} }});
  }});
}})();
</script>
'''
(HERE / "index.html").write_text(page, encoding="utf-8")
print("   ○ index.html", len(page), "文字")
