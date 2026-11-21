"""
ダッシュボードの別ページ（再生数・お金・鍵・仕上げを聴く など）で共通の枠（決定 176）

  page(title, body, token, active="", auto=False)  … 見出しの帯・共通の見た目・ボタンの動きを付けた 1 ページの HTML
  btn(spec, primary=False, fields=None, post=None)   … ボタン 1 つ
      spec   … {"action": "…", "label": "…", "confirm": "…（任意）", ほかは /api/action に送る値}
      fields … {"送る名前": "入力欄の id"}。押したときに入力欄の値を一緒に送る
      post   … /api/action 以外に送るとき（例 "/api/keys/save"）。返事が {"ok": true} ならページを読み直す
  kpis([(値, 見出し, 補足), …])                      … 大きい数字のタイル
  table(head, rows, num=())                         … 表（num に入れた列番号は右寄せ・等幅の数字）
  badge(kind, text)                                 … 状態の印（kind：done・wait・run・ng・info）

  使える見た目の名前（class）：card（1 つの箱・<section class="card">）・sub（小さい補足）・grid2（2 列）・row（横並び）・
    primary / ghost / danger（ボタン）・field（<label class="field">見出し<input></label>）・empty（中身が無いとき）
"""
from __future__ import annotations

import html
import json

E = html.escape

# 上の帯の行き先（ダッシュボード本体と同じ並び。決定 176）
NAV = [("/", "ホーム"), ("/#make", "制作"), ("/#acts", "アーティスト"), ("/plays", "再生数"), ("/money", "お金"), ("/keys", "設定"), ("/#tools", "道具")]
PARENT = {"/listen": "/#make", "/pitch": "/#make", "/check": "/#make", "/suno": "/#make", "/photos": "/#acts", "/phone": "/#tools", "/deep": "/#tools"}   # 帯に無いページが属する所

CSS = """
:root{--bg:#f4f5f4;--card:#fff;--ink:#1b2024;--sub:#5d6770;--line:#d9dedb;--soft:#e9edeb;--accent:#23786b;--accent-ink:#fff;
 --good:#2e7d4f;--warn:#b7791f;--crit:#c2513a;--info:#3d5a80}
@media (prefers-color-scheme:dark){:root{--bg:#121518;--card:#1a1f23;--ink:#e7ecea;--sub:#9aa5ae;--line:#2e363c;--soft:#22292e;--accent:#5cc3b1;--accent-ink:#0d1a18;
 --good:#6cc593;--warn:#e3b25a;--crit:#f08a72;--info:#8fb0da}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
header.top{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line)}
header.top nav{max-width:1080px;margin:auto;padding:8px 16px;display:flex;gap:4px 14px;flex-wrap:wrap;align-items:center}
header.top b{font-size:15px;margin-right:8px}
header.top a{color:var(--sub);text-decoration:none;font-size:13.5px;padding:4px 2px;border-bottom:2px solid transparent}
header.top a.on{color:var(--ink);border-color:var(--accent);font-weight:600}
main{max-width:1080px;margin:auto;padding:16px;display:grid;gap:14px}
h1{font-size:21px;margin:6px 0 0} h2{font-size:16px;margin:0 0 6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap} h3{font-size:14px;margin:10px 0 4px}
a{color:var(--accent)} .sub{color:var(--sub);font-size:13px} .empty{color:var(--sub);font-size:14px;padding:8px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px} .row{display:flex;gap:8px 12px;flex-wrap:wrap;align-items:center}
@media (max-width:720px){.grid2{grid-template-columns:1fr}}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:grid;gap:2px}
.kpi b{font-size:24px;font-variant-numeric:tabular-nums;line-height:1.2} .kpi span{font-size:12.5px;color:var(--sub)} .kpi i{font-style:normal;font-size:12px;color:var(--sub)}
.tbl{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{border-top:1px solid var(--soft);padding:7px 10px;text-align:left;vertical-align:top} th{color:var(--sub);font-weight:600;font-size:12.5px;white-space:nowrap;border-top:0}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.badge{font-size:12px;border-radius:99px;padding:1px 10px;border:1px solid;white-space:nowrap}
.badge.done{color:var(--good)} .badge.ng{color:var(--crit)} .badge.run,.badge.wait{color:var(--warn)} .badge.info{color:var(--info)}
button,.btn{font:inherit;font-size:14px;border-radius:8px;padding:6px 14px;cursor:pointer;border:1px solid var(--line);background:var(--card);color:var(--ink);text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
button.ghost,.btn.ghost{border-color:transparent;color:var(--accent);padding-inline:6px} button.danger{border-color:var(--crit);color:var(--crit)}
button:disabled{opacity:.5;cursor:default} :focus-visible{outline:2px solid var(--accent);outline-offset:2px}
label.field{display:grid;gap:3px;font-size:13px;color:var(--sub)} input,select,textarea{font:inherit;font-size:14px;padding:6px 8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);max-width:100%}
textarea{width:100%;min-height:6em} audio{width:100%} img.cover{width:min(280px,100%);border-radius:8px;border:1px solid var(--line)}
code,.mono{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:12.5px;word-break:break-all}
#job{position:sticky;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:8px 16px;font-size:13px;color:var(--sub)} #job:empty{display:none}
"""

JS = """
const TOKEN="__TOKEN__";
function collect(b){const p=JSON.parse(b.dataset.p||"{}");
  for(const pair of (b.dataset.fields||"").split(",")){if(!pair)continue;const [name,id]=pair.split(":");const el=document.getElementById(id);
    if(el)p[name]=el.type==="checkbox"?el.checked:el.value}
  return p}
async function act(b){const p=collect(b);if(p.confirm&&!confirm(p.confirm))return;delete p.confirm;b.disabled=true;const job=document.getElementById("job");
  try{const r=await fetch(b.dataset.post||"/api/action",{method:"POST",headers:{"Content-Type":"application/json","X-Label-Token":TOKEN},body:JSON.stringify(p)});
    const j=await r.json().catch(()=>({}));if(!r.ok){alert(j.error||"実行できませんでした");return}
    if(j.message)job.textContent=j.message;
    if(!j.job){if(j.ok!==false)setTimeout(()=>location.reload(),j.message?1200:0);return}
    job.textContent="始めました。終わると、このページが新しくなります。";
    for(;;){await new Promise(r=>setTimeout(r,2000));const s=await (await fetch("/api/jobs/"+j.job)).json();
      if(s.progress||s.step)job.textContent="進行中："+[s.progress,s.step].filter(Boolean).join("・");
      if(s.state!=="running"){if(s.state==="error")alert((s.summary||"うまくいきませんでした")+"\\n"+(s.out||"").slice(-400));location.reload();break}}}
  finally{b.disabled=false}}
document.addEventListener("click",e=>{const b=e.target.closest("button[data-p]");if(b)act(b);
  const c=e.target.closest("button[data-copy]");if(c){const t=document.getElementById(c.dataset.copy);const v=t?(t.value??t.textContent):"";
    navigator.clipboard.writeText(v).then(()=>{const o=c.textContent;c.textContent="コピーしました";setTimeout(()=>c.textContent=o,1200)}).catch(()=>{if(t&&t.select)t.select()})}});
if(__AUTO__){setInterval(()=>{if([...document.querySelectorAll("audio")].every(a=>a.paused)&&!document.querySelector("input:focus,textarea:focus,select:focus"))location.reload()},8000)}
"""


def page(title: str, body: str, token: str, active: str = "", auto: bool = False, head: str = "", script: str = "") -> str:
    active = PARENT.get(active, active)
    nav = "".join(f'<a href="{href}" class="{"on" if href == active else ""}">{E(name)}</a>' for href, name in NAV)
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{E(title)}</title><style>{CSS}</style>{head}</head><body>'
            f'<header class="top"><nav><b>Postdog Records</b>{nav}</nav></header><main>{body}</main><div id="job"></div>'
            f'<script>{JS.replace("__TOKEN__", token).replace("__AUTO__", "true" if auto else "false")}{script}</script></body></html>')


def nav_html(active: str = "") -> str:
    """古い作りの別ページ（公開の準備・写真など）にも同じ帯を付ける"""
    active = PARENT.get(active, active)
    return ('<header class="top"><nav><b>Postdog Records</b>'
            + "".join(f'<a href="{href}" class="{"on" if href == active else ""}">{E(name)}</a>' for href, name in NAV) + "</nav></header>")


def btn(spec: dict, primary: bool = False, fields: dict | None = None, post: str | None = None, cls: str = "") -> str:
    p = {k: v for k, v in spec.items() if k != "label"}
    f = ",".join(f"{k}:{v}" for k, v in (fields or {}).items())
    klass = " ".join(x for x in ("primary" if primary else "", cls) if x)
    return (f'<button class="{klass}" data-p="{E(json.dumps(p, ensure_ascii=False))}"'
            + (f' data-fields="{E(f)}"' if f else "") + (f' data-post="{E(post)}"' if post else "") + f'>{E(spec["label"])}</button>')


def copy_btn(target_id: str, label: str = "コピー") -> str:
    return f'<button class="ghost" data-copy="{E(target_id)}">{E(label)}</button>'


def badge(kind: str, text: str) -> str:
    return f'<span class="badge {E(kind)}">{E(text)}</span>'


def kpis(items: list[tuple]) -> str:
    out = []
    for it in items:
        value, label, note = (list(it) + ["", ""])[:3]
        out.append(f'<div class="kpi"><span>{E(str(label))}</span><b>{E(str(value))}</b>' + (f"<i>{E(str(note))}</i>" if note else "") + "</div>")
    return f'<div class="kpis">{"".join(out)}</div>'


def table(head: list[str], rows: list[list], num: tuple = (), raw: tuple = ()) -> str:
    """raw に入れた列番号は、HTML をそのまま入れる（ほかは文字として安全に入れる）"""
    th = "".join(f'<th class="{"n" if i in num else ""}">{E(h)}</th>' for i, h in enumerate(head))
    trs = []
    for r in rows:
        trs.append("<tr>" + "".join(f'<td class="{"n" if i in num else ""}">{(c if i in raw else E("" if c is None else str(c)))}</td>'
                                    for i, c in enumerate(r)) + "</tr>")
    return f'<div class="tbl"><table><tr>{th}</tr>{"".join(trs)}</table></div>'


def yen(v) -> str:
    try:
        return f"¥{round(float(v)):,}"
    except (TypeError, ValueError):
        return "—"


def num(v) -> str:
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "—"
