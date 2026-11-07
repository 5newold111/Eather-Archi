#!/usr/bin/env python3
"""
作曲の時点の質の確認：Suno から取り込んだ曲を測り、質が足りなければ曲の案を自動で作り直して、作曲（Suno）に戻す
（オーナーの指示 2026-10-26。決定 100）

  見るもの（select_takes.py と同じ測り方）：長さ・音量（途中の無音・急な音量差）・サビの位置・終わり方（途中で切れていない）・
  歌詞の一致（文字起こしが使えるとき）。どれかが外れるか、点が 55 点未満なら「質が足りない」
  ・足りないとき：外れた理由をブリーフの retake に書き、曲名・エピソード・テーマはそのままに直して、曲を「作曲待ち」に戻す
    直し方は、できるだけお金（Claude の料金）を使わない順に（決定 100）：
      1 回目：機械の直し（Claude を使わない・0 円）…理由ごとに、Suno のスタイルの言葉・除外・歌詞の札（[Outro][Fade Out] など）を足す
              （「雰囲気が違う」のように機械では直せない理由だけのときは、2 回目の直し方にする）
      2 回目：Claude の小さな書き直し（スタイルと歌詞だけ。1 曲 数円）
      3 回目：作り直さずに進め、Gon さんに「聴いて判断して」と知らせる
  ・Suno で聴いて「2 曲とも良くない」とき（ダウンロード前）：まず同じ入力のまま Create をもう一度（0 円）。それでもだめなら
    ダッシュボードの「2 曲とも良くない（直して入れ直す）」で理由を選ぶ → 上と同じ順の直し方 → もう一度 Suno に入力
  ・デビュー曲は対象外。取り込んだテイクは out/takes/<曲>/retake_<回>/ に残す
  ・Suno のダウンロードを 1 本ずつ使う（Premier は月 60 本）。止めるときは .env に AUTO_RETAKE=0
  ・Suno のダウンロード枠に合わせているあいだ（suno_quota.py。決定 105）は、自動で作り直さずに「聴いて判断して」と知らせる

使い方：
  python3 scripts/take_check.py <曲 ID>          # 測るだけ（作り直さない）
  python3 scripts/take_check.py <曲 ID> --apply  # 足りなければ作り直す（publish.py scan が取り込みのたびに使う）
  python3 scripts/take_check.py <曲 ID> --redo サビが弱い,終わり方   # Suno で聴いて 2 曲とも良くないとき（ダウンロード前）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, length_spec, load_artist, load_dotenv, load_label, notify, read_json  # noqa: E402

load_dotenv()
MIN_SCORE = 55
MAX_RETAKES = 2
# Gon さんが Suno で聴いて選ぶ理由（ダウンロード前）→ 直し方のキー
MANUAL = {"サビが弱い": "点", "サビが遅い": "サビの位置", "終わり方が変": "終わり方", "長すぎる": "長い", "短すぎる": "長さ",
          "歌が歌詞と違う": "歌詞の一致", "音が途切れる・割れる": "音量", "声が合わない": "声", "雰囲気が違う": "雰囲気"}
# 機械の直し（Claude を使わない）：(スタイルに足す言葉, 除外に足す言葉, 歌詞の終わりに足す札)
RULE_FIX = {
    "長さ": ("full-length song, extended second chorus and outro", "", ""),
    "長い": ("concise radio edit, no long instrumental passages", "", ""),
    "音量": ("continuous steady dynamics, no breaks", "silence, abrupt stops", ""),
    "サビの位置": ("short intro, chorus arrives early", "long intro", ""),
    "終わり方": ("clean ending with a fade out", "", "[Outro]\n[Fade Out]\n[End]"),
    "歌詞の一致": ("clear diction, vocals follow the written lyrics", "", ""),
    "点": ("big chorus lift, the chorus clearly stronger than the verses", "", ""),
}
CLAUDE_ONLY = {"雰囲気"}   # 機械では直せない理由（Claude の小さな書き直しへ）
FIX_HINT = {   # 外れた理由 → Claude への直し方の手がかり
    "長さ": "the song length was outside the allowed range: adjust the number of sections/lines and say the target length in the style",
    "音量": "there were silent gaps or sudden volume jumps: avoid breaks and stops, keep the dynamics continuous",
    "サビの位置": "the first chorus/hook came too late: bring the hook earlier (shorter intro, chorus within the hook limit)",
    "終わり方": "the song ended abruptly: end with a clear [Outro] and [Fade Out] or a final held chord",
    "歌詞の一致": "the singing did not follow the lyrics: simpler, clearer lyrics with fewer words per line and clear section tags",
    "点": "the take was weak overall (low energy contrast at the hook): make the chorus lift clearly above the verses",
    "長い": "the song was too long: tighten sections and say the target length in the style",
    "声": "the voice did not fit the act: describe the act's voice (sex, range, timbre) explicitly at the start of the style",
    "雰囲気": "the overall feel did not match the brief's direction: rewrite the style around the episode, mood and explore axes",
}


def rule_fix(b: dict, keys: list[str], artist: dict) -> list[str]:
    """機械の直し（0 円）。直したことの一覧"""
    done, style, excl, lyr = [], str(b.get("suno_style_prompt") or ""), str(b.get("suno_exclude") or ""), str(b.get("suno_lyrics") or "")
    for k in keys:
        if k == "声":
            v = artist.get("vocal") or {}   # Suno のスタイルは英語だけ（日本語の説明は入れない）
            parts = [str(x) for x in [(v.get("voice_spec") or {}).get("timbre"), v.get("guest_description")] if isinstance(x, str) and x.isascii() and x.strip()]
            sex = {"male": "male", "female": "female", "mixed": "male and female"}.get(v.get("sex"), "")
            add, ex, tail = (", ".join([f"{sex} lead vocal, consistent timbre".strip()] + parts), "", "")
        elif k in RULE_FIX:
            add, ex, tail = RULE_FIX[k]
        else:
            continue
        if add and add.lower() not in style.lower():
            style = f"{add}, {style}" if k in ("声", "サビの位置") else f"{style}, {add}"
            done.append(f"スタイルに「{add}」")
        if ex and ex.lower() not in excl.lower():
            excl = f"{excl}, {ex}".strip(", ")
            done.append(f"除外に「{ex}」")
        if tail and "[fade out]" not in lyr.lower() and lyr:
            lyr = lyr.rstrip() + "\n\n" + tail
            done.append("歌詞の終わりに [Outro][Fade Out][End]")
    b["suno_style_prompt"], b["suno_exclude"], b["suno_lyrics"] = style[:950], excl[:500], lyr
    return done


LIGHT_SYSTEM = """You fix a Suno song request after the result was not good. You get the current Suno style prompt, exclude list
and lyrics, the act's voice and sound, this song's direction, and the reasons it failed (Japanese). Return a corrected style
prompt (English, comma-separated, under 900 characters, no artist names), exclude list, lyrics (keep the meaning, title and
section tags; change only what fixes the reasons; lyric format: capital at line start, no end punctuation except ! ?), and
notes_ja (Japanese, one or two sentences on what you changed)."""
LIGHT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["style", "exclude", "lyrics", "notes_ja"],
                "properties": {k: {"type": "string"} for k in ("style", "exclude", "lyrics", "notes_ja")}}


def light_rewrite(b: dict, keys: list[str], reasons: list[str], artist: dict) -> str:
    """Claude の小さな書き直し（スタイルと歌詞だけ）。直したことの説明（できなければ空）"""
    import _claude
    if not _claude.available():
        return ""
    v, snd = artist.get("vocal") or {}, artist.get("sound") or {}
    user = json.dumps({"style": b.get("suno_style_prompt", ""), "exclude": b.get("suno_exclude", ""), "lyrics": b.get("suno_lyrics", ""),
                       "voice": {"sex": v.get("sex"), "voice_spec": v.get("voice_spec")}, "sound": {k: snd.get(k) for k in ("genres", "palette")},
                       "direction": {k: x.get("en") for k, x in ((b.get("explore") or {}).get("axes") or {}).items()},
                       "episode_theme": (b.get("episode") or {}).get("theme", ""), "reasons_ja": reasons,
                       "how_to_fix": [FIX_HINT.get(k, "") for k in keys]}, ensure_ascii=False)
    res, _st = _claude.call_json(LIGHT_SYSTEM, user, LIGHT_SCHEMA, effort="low", max_tokens=6000)
    if not res:
        return ""
    from _lyric_rules import tidy
    b["suno_style_prompt"], b["suno_exclude"], b["suno_lyrics"] = res["style"][:950], res["exclude"][:500], tidy(res["lyrics"])
    return res.get("notes_ja", "")


def check(song: dict) -> dict:
    """取り込んだテイクを測る：{passed, hard_fail, score, checks, take}"""
    import explore
    import select_takes as stk
    sel = OUT / "takes" / song["key"] / "selection.json"
    d = read_json(sel) if sel.exists() else {}
    take = (d.get("decision") or {}).get("selected")
    f = OUT / "takes" / song["key"] / take if take else None
    if not f or not f.exists():
        return {"passed": None, "why": "取り込んだテイクが見つかりません"}
    b = read_json(Path(song["brief"])) if song.get("brief") else {}
    artist = explore.relax(load_artist(song["slug"], song.get("label_slug")))
    spec = length_spec(artist, load_label(song.get("label_slug")))
    spec.update({k: v for k, v in (b.get("scene") or {}).items() if k in ("length_sec_min", "length_sec_max", "hook_within_sec", "no_sudden_silence")})
    instrumental = b.get("language") == "none" or (artist.get("lyrics") or {}).get("mode") == "instrumental"
    m = stk.measure_audio(f, spec)
    transcript, how = (None, "歌なし") if instrumental else stk.transcribe(f)
    lyr_p = Path(song["brief"]).with_suffix(".lyrics_final.txt") if song.get("brief") else None
    lyrics = lyr_p.read_text(encoding="utf-8") if lyr_p and lyr_p.exists() else (b.get("suno_lyrics") or "")
    match = stk.lyric_match(transcript, lyrics) if (transcript and lyrics) else None
    j = stk.judge(m, spec, match, stk.tic_targets(artist), transcript, instrumental)
    fails = list(j["hard_fail"]) + (["点"] if j["score"] < MIN_SCORE else [])
    return {"passed": not fails, "hard_fail": fails, "score": j["score"], "checks": j["checks"], "take": take, "transcript_source": how}


def fix_brief(song: dict, keys: list[str], reasons: list[str], source: str) -> tuple[bool, str]:
    """ブリーフを直す（お金を使わない順）。(直したか, 何をしたか)。回数切れなら (False, "")"""
    bp = Path(song["brief"])
    b = read_json(bp)
    n = int((b.get("retake") or {}).get("round") or 0)
    if n >= MAX_RETAKES:
        return False, ""
    artist = load_artist(song["slug"], song.get("label_slug")) or {}
    machine = [k for k in keys if k not in CLAUDE_ONLY]
    if n == 0 and machine:   # 1 回目：機械の直し（0 円）
        done = rule_fix(b, machine, artist)
        how = "機械の直し（0 円）：" + ("・".join(done) or "直す言葉はもう入っていました")
        if any(k in CLAUDE_ONLY for k in keys):
            how += "（「雰囲気が違う」は次の回で Claude が直します）"
    else:                    # 2 回目（または機械では直せない理由だけ）：Claude の小さな書き直し
        note = light_rewrite(b, keys, reasons, artist)
        how = f"Claude の小さな書き直し（スタイルと歌詞だけ）：{note}" if note else "Claude が使えないので、機械の直しだけ：" + "・".join(rule_fix(b, machine, artist) or ["なし"])
    b["retake"] = {"round": n + 1, "reasons_ja": reasons, "fix": [FIX_HINT.get(k, "") for k in keys], "how": how, "source": source,
                   "at": datetime.now().isoformat(timespec="seconds")}
    bp.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, how


def retake(song: dict, res: dict) -> bool:
    """取り込んだ曲の質が足りない：曲の案を直して「作曲待ち」に戻す。戻したら True"""
    import _songs
    bp = Path(song["brief"])
    reasons = [f"{k}：{res['checks'][k]['detail']}" if k in res["checks"] else f"点 {res['score']}（{MIN_SCORE} 点未満）" for k in res["hard_fail"]]
    keys = list(res["hard_fail"])
    if "長さ" in keys:   # 長すぎるのか短すぎるのか
        try:
            dur = float(str(res["checks"]["長さ"]["detail"]).split(" ")[0])
            lo = float(str(res["checks"]["長さ"]["detail"]).split("（")[1].split("〜")[0])
            if dur > lo:
                keys[keys.index("長さ")] = "長い"
        except (KeyError, ValueError, IndexError):
            pass
    ok, how = fix_brief(song, keys, reasons, f"取り込んだテイク {res.get('take')}（{res.get('score')} 点）")
    if not ok:
        return False
    print(f"     直し方：{how}")
    tdir = OUT / "takes" / song["key"]
    keepdir = tdir / f"retake_{(read_json(bp).get('retake') or {}).get('round', 1)}"
    keepdir.mkdir(parents=True, exist_ok=True)
    for f in list(tdir.iterdir()):
        if f.is_file():
            shutil.move(str(f), keepdir / f.name)
    fin = bp.with_suffix(".lyrics_final.txt")   # 前の合体した歌詞が残っていると、直した歌詞より先に使われるので、よける
    if fin.exists():
        shutil.move(str(fin), keepdir / fin.name)
    st = _songs.state(song)
    rt = read_json(bp).get("retake") or {}
    hist = list(st.get("retakes") or []) + [{"round": rt.get("round"), "reasons": reasons, "take": res.get("take"), "how": how, "at": rt.get("at")}]
    _songs.save_state(song, {k: v for k, v in st.items() if k in ("generated_on",)} | {"status": "", "retakes": hist})
    return True


def gate(song: dict) -> str:
    """publish.py scan から：ok（進める）／retake（作り直して作曲待ちに戻した）／keep（足りないが回数切れで進める）／skip（測れない）"""
    if song.get("kind") == "debut" or os.environ.get("AUTO_RETAKE", "1") == "0" or not song.get("brief"):
        return "skip"
    try:
        res = check(song)
    except Exception as e:  # noqa: BLE001  測れなくても、取り込みは止めない
        print(f"   － 質の確認ができませんでした（{type(e).__name__}: {str(e)[:120]}）。このまま進めます")
        return "skip"
    if res.get("passed") is None:
        return "skip"
    name = f"{song['artist']}「{song['title']}」"
    if res["passed"]:
        print(f"   ✓ {name}：作曲の質の確認に通りました（{res['score']} 点）")
        return "ok"
    why = "・".join(res["hard_fail"])
    try:   # Suno のダウンロード枠を使うあいだは自動で作り直さない（作り直すとダウンロードを 1 曲分使う。決定 105）
        import suno_quota
        quota = suno_quota.on()
    except ImportError:
        quota = False
    if quota:
        print(f"   ⚠ {name}：測ると {why}（{res['score']} 点）。Suno のダウンロード枠を使うあいだは自動で作り直しません。聴いて、進めるか決めてください"
              "（作り直すときは「前の工程に戻す」→ 理由を選んで直す。ダウンロードを 1 曲分使います）")
        notify("聴いて判断してください", f"{name}：{why}。作り直すとダウンロードを 1 曲分使います")
        return "keep"
    if retake(song, res):
        print(f"   ↻ {name}：質が足りないので（{why}・{res['score']} 点）、曲の案を直して作曲待ちに戻しました。Suno でもう一度作ってください")
        notify("作曲をやり直します", f"{name}：{why}。案を直したので、Suno でもう一度作ってください")
        return "retake"
    print(f"   ⚠ {name}：自動の作り直しを {MAX_RETAKES} 回しても質が足りません（{why}）。聴いて、進めるか決めてください")
    notify("聴いて判断してください", f"{name}：自動の作り直し {MAX_RETAKES} 回でも {why}")
    return "keep"


def main() -> None:
    import _songs
    ap = argparse.ArgumentParser(description="作曲の時点の質の確認")
    ap.add_argument("id")
    ap.add_argument("--apply", action="store_true", help="足りなければ案を作り直して作曲待ちに戻す")
    ap.add_argument("--redo", help="Suno で聴いて 2 曲とも良くないときの理由（カンマ区切り：" + "・".join(MANUAL) + "）")
    a = ap.parse_args()
    song = _songs.by_id(a.id)
    if not song:
        sys.exit(f"[エラー] 曲 {a.id} が見つかりません")
    if a.redo is not None:
        why = [w.strip() for w in a.redo.split(",") if w.strip() in MANUAL] or ["サビが弱い"]
        ok, how = fix_brief(song, [MANUAL[w] for w in why], why, "Suno で聴いて 2 曲とも良くない（オーナー）")
        if not ok:
            print(f"【結果】この曲はもう {MAX_RETAKES} 回直しました。曲の案から作り直すか（不定期配信の「却下して作り直す」）、このまま聴いて選んでください")
            sys.exit(2)
        print(f"【結果】{song['artist']}「{song['title']}」の入力を直しました：{how}。もう一度「Suno に入力する」を押してください")
        return
    if a.apply:
        print(f"【結果】{gate(song)}")
        return
    res = check(song)
    if res.get("passed") is None:
        print(f"【結果】{res.get('why')}")
        return
    for k, c in res["checks"].items():
        print(f"   {'○' if c['ok'] else ('✕' if c['ok'] is False else '－')} {k}：{c['detail']}")
    print(f"【結果】{'通りました' if res['passed'] else '質が足りません：' + '・'.join(res['hard_fail'])}（{res['score']} 点）")


if __name__ == "__main__":
    main()
