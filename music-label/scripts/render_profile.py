#!/usr/bin/env python3
"""
アーティスト管理表の 1 組ぶんを、読みやすい資料（Markdown）にする。

  python3 scripts/artist_book.py render            # 管理表の全組 → proposals/<組>.md
  python3 scripts/render_profile.py proposals/2026-10-08/nao_easterly.json   # 提案の JSON 1 つを直接

提案の行も採用の行も全部載せる。行の状態（提案・保留など）は見出しの横に【】で示す。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _artist_book import TABS  # noqa: E402

GENDER_ICON = {"女性": "♀", "男性": "♂"}


def _h(tab: str) -> dict[str, str]:
    return {k: h for k, h, _ in TABS[tab][1]}


def _v(x) -> str:
    if isinstance(x, list):
        return "、".join(str(i) for i in x)
    if isinstance(x, bool):
        return "○" if x else ""
    return "" if x is None else str(x)


def _st(r: dict) -> str:
    s = str(r.get("status") or "").strip()
    return f"【{s}】" if s and s != "採用" else ""


def _fields(tab: str, r: dict, keys: list[str] | None = None, skip=("id", "act_id", "member_id", "status", "_row", "_pos")) -> list[str]:
    h = _h(tab)
    out = []
    for k in keys or [k for k, _, _ in TABS[tab][1]]:
        if k in skip:
            continue
        v = _v(r.get(k))
        if v:
            out.append(f"- **{h[k]}**：{v}")
    return out


def _rows(data: dict, tab: str, aid: str) -> list[dict]:
    return [r for r in data.get(tab, []) if r.get("act_id") == aid]


def render_act(act: dict, data: dict) -> str:
    aid = act["id"]
    members = _rows(data, "members", aid)
    mname = {m.get("id"): m.get("name", m.get("id")) for m in members}
    L = [f"# {act.get('name', aid)} {_st(act)}", ""]
    meta = [act.get(k) for k in ("reading", "formation", "scene")]
    L += [" ／ ".join(f"{x}" for x in [f"読み：{meta[0]}" if meta[0] else "", meta[1],
                                       f"場面：{meta[2]}" if meta[2] else ""] if x), ""]
    if act.get("bio_en"):
        L += [f"> {act['bio_en']}", ""]

    L += ["## アーティスト", ""] + _fields("acts", act, skip=("id", "status", "_row", "_pos", "bio_en", "name", "reading",
                                                              "formation", "scene", "label")) + [""]

    for m in members:
        icon = GENDER_ICON.get(m.get("gender", ""), "")
        L += [f"## メンバー：{m.get('name', m.get('id'))} {icon} {_st(m)}", ""]
        L += _fields("members", m, skip=("id", "act_id", "status", "_row", "_pos", "name")) + [""]
        marks = [r for r in _rows(data, "marks", aid) if r.get("member_id") == m.get("id")]
        if marks:
            L += ["**傷・タトゥー**", ""]
            for r in marks:
                L.append(f"- {r.get('kind', '')}：{' ／ '.join(_v(r.get(k)) for k in ('where', 'when', 'what') if r.get(k))}"
                         f"{'。' + r['meaning'] if r.get('meaning') else ''} {_st(r)}")
            L.append("")

    tl = _rows(data, "timeline", aid)
    if tl:
        L += ["## 人生年表", "", "| 誰 | 年齢 | 西暦 | 場所 | 暮らし・学校 | 周りにいた人 | 出来事 | そのときの気持ち | 種 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(tl, key=lambda r: (str(r.get("member_id") or "～"), r.get("age_from") or 0)):
            age = f"{r.get('age_from', '')}〜{r.get('age_to', '')}" if r.get("age_to") not in (None, "", r.get("age_from")) else str(r.get("age_from", ""))
            cells = [mname.get(r.get("member_id"), "全員" if not r.get("member_id") else r.get("member_id")), age,
                     _v(r.get("years")), _v(r.get("place")), _v(r.get("life")), _v(r.get("people")),
                     _v(r.get("event")) + _st(r), _v(r.get("feeling")), "○" if r.get("seed") else ""]
            L.append("| " + " | ".join(c.replace("|", "／").replace("\n", " ") for c in cells) + " |")
        L.append("")

    deb = _rows(data, "debut", aid)
    if deb:
        L += ["## デビューの経緯", ""]
        for r in deb:
            L.append(f"- **{_v(r.get('when'))}**（{_v(r.get('venue'))}）{_v(r.get('what'))} → {_v(r.get('reaction'))}"
                     f"{'。転機：' + r['turning_point'] if r.get('turning_point') else ''} {_st(r)}")
        L.append("")

    inf = _rows(data, "influences", aid)
    if inf:
        L += ["## 影響を受けたもの", "", "| 誰 | 種類 | 名前・題名 | 作者 | 作品・時期 | 出会い | 受け取ったもの | どこに表れているか |",
              "|---|---|---|---|---|---|---|---|"]
        for r in inf:
            L.append("| " + " | ".join(c.replace("|", "／").replace("\n", " ") for c in [
                mname.get(r.get("member_id"), "全員"), _v(r.get("kind")), _v(r.get("title")), _v(r.get("author")),
                _v(r.get("work")), _v(r.get("when_met")), _v(r.get("taken")) + _st(r), _v(r.get("shows_in"))]) + " |")
        L.append("")

    seeds = _rows(data, "seeds", aid)
    if seeds:
        L += ["## 歌の種", ""]
        for r in seeds:
            L.append(f"- **{_v(r.get('id'))}**（{_v(r.get('period'))}）{_v(r.get('idea'))} {_st(r)}")
            L.append(f"  - 気持ち：{_v(r.get('feeling'))} ／ 情景：{_v(r.get('scene'))} ／ 視点：{_v(r.get('pov'))}")
            if r.get("words"):
                L.append(f"  - 言葉：{_v(r.get('words'))}")
        L.append("")

    lyr = _rows(data, "lyrics", aid)
    if lyr:
        L += ["## 歌詞の試作", ""]
        for r in lyr:
            L += [f"### {_v(r.get('title'))}（種 {_v(r.get('seed_id'))}）{_st(r)}", ""]
            if r.get("verse"):
                L += ["1 番の書き出し", "", "```", str(r["verse"]), "```", ""]
            if r.get("chorus"):
                L += ["サビ", "", "```", str(r["chorus"]), "```", ""]
            if r.get("meaning_ja"):
                L += [str(r["meaning_ja"]), ""]

    up = _rows(data, "updates", aid)
    if up:
        L += ["## 近況", ""]
        for r in up:
            L.append(f"- {_v(r.get('date'))}：{_v(r.get('event'))} → {_v(r.get('feeling'))}{'（歌にする）' if r.get('to_song') else ''} {_st(r)}")
        L.append("")

    ch = _rows(data, "choices", aid)
    if ch:
        L += ["## 参考曲の候補（この組が選びそうな曲）", "", "| 曲名 | アーティスト | 年 | 借りたい要素 | 選ぶ理由 | 入手 | 解析 |",
              "|---|---|---|---|---|---|---|"]
        for r in ch:
            L.append("| " + " | ".join([_v(r.get("title")) + _st(r), _v(r.get("artist")), _v(r.get("year")), _v(r.get("slots")),
                                          _v(r.get("why")).replace("|", "／"), "○" if r.get("acquired") else "",
                                          "○" if r.get("analyzed") else ""]) + " |")
        L.append("")

    for tab, title in (("music", "声と曲づくり"), ("visual", "見た目")):
        for r in _rows(data, tab, aid):
            L += [f"## {title} {_st(r)}", ""] + _fields(tab, r) + [""]

    chg = _rows(data, "changes", aid)
    if chg:
        L += ["## 変更の提案", ""]
        for r in chg:
            L.append(f"- {_v(r.get('date'))} {_v(r.get('tab'))}『{_v(r.get('field'))}』：{_v(r.get('current'))} → "
                     f"{_v(r.get('proposed'))}（{_v(r.get('reason'))}）{_st(r)}")
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def from_proposal(p: dict, status: str = "提案") -> tuple[dict, dict]:
    """提案の JSON 1 組ぶん → (act, data) の形"""
    from artist_book import proposal_rows
    rows = proposal_rows(p, status)
    return rows["acts"][0], {t: rows.get(t, []) for t in TABS}


def main() -> None:
    import json
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for f in sys.argv[1:]:
        p = json.loads(Path(f).read_text(encoding="utf-8"))
        act, data = from_proposal(p)
        out = Path(f).with_suffix(".md")
        out.write_text(render_act(act, data), encoding="utf-8")
        print(f"   ○ {act.get('name')} → {out}")


if __name__ == "__main__":
    main()
