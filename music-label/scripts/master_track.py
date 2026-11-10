#!/usr/bin/env python3
"""
選んだテイクの音量を配信向けに整える（マスタリングの最終段だけ）。

  目標：統合ラウドネス -14 LUFS（Spotify / YouTube の基準）・トゥルーピーク -1 dBTP
  出力：WAV 44.1kHz / 24bit（DistroKid に登録する形式）

  ffmpeg の loudnorm を 2 回通す（1 回目で測り、2 回目で測った値に合わせて調整）。
  最後にもう一度測って、目標から ±1 LU 以内かを確かめる。

使い方
  python scripts/master_track.py out/briefs/2026-10-05_light.json      # selection.json の選択テイクを使う
  python scripts/master_track.py --input take_03.wav --out master.wav  # ファイルを直接指定
  python scripts/master_track.py --demo                                  # select_takes.py --demo の結果で試す

用語
  LUFS  … 人の耳の感じ方に合わせた音量の単位。-14 は配信サービスが揃える基準値
  dBTP  … 波形の本当の最大値。-1 にしておくと、配信側の変換で音が割れない
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, read_json, step, write_json  # noqa: E402
from select_takes import ebur128  # noqa: E402

TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.0, 11.0


def loudnorm_measure(src: Path) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af",
                        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        sys.exit("[エラー] 音量を測れませんでした。ffmpeg の出力:\n" + r.stderr[-800:])
    return json.loads(m.group(0))


def master(src: Path, dst: Path) -> dict:
    if not shutil.which("ffmpeg"):
        sys.exit("[エラー] ffmpeg が見つかりません。Mac なら  brew install ffmpeg  で入れてください")
    step(f"1 回目：{src.name} の音量を測っています")
    meas = loudnorm_measure(src)
    step(f"   元の音量 {meas['input_i']} LUFS / ピーク {meas['input_tp']} dBTP / 幅 {meas['input_lra']} LU")
    step("2 回目：測った値に合わせて -14 LUFS・-1 dBTP に整え、44.1kHz / 24bit の WAV に書き出しています")
    dst.parent.mkdir(parents=True, exist_ok=True)
    af = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
          f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
          f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
          f":offset={meas['target_offset']}:linear=true")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(src), "-af", af,
                        "-ar", "44100", "-c:a", "pcm_s24le", str(dst)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("[エラー] 書き出しに失敗しました:\n" + r.stderr[-800:])
    step("確認：書き出したファイルをもう一度測っています")
    after = ebur128(dst)
    ok = abs(after.get("lufs", 0) - TARGET_I) <= 1.0 and after.get("true_peak_db", 0) <= TARGET_TP + 0.3
    report = {"source": str(src), "master": str(dst), "before": meas, "after": after,
              "target": {"lufs": TARGET_I, "true_peak_db": TARGET_TP}, "ok": ok}
    mark = "○" if ok else "✕"
    print(f"   {mark} 仕上がり {after.get('lufs')} LUFS / ピーク {after.get('true_peak_db')} dBTP")
    if not ok:
        print("   目標から外れました。元の音源の音割れ・極端な音量差を確認してください")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="選んだテイクを -14 LUFS / -1 dBTP の WAV に整える")
    ap.add_argument("brief", nargs="?", help="out/briefs/<週>_<組>.json（out/takes/<同名>/selection.json を読む）")
    ap.add_argument("--input", help="元のファイルを直接指定")
    ap.add_argument("--out", help="出力先（既定：out/masters/<ブリーフ名>/master.wav）")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.input:
        src = Path(args.input)
        dst = Path(args.out) if args.out else src.with_name(src.stem + "_master.wav")
        sel_path = None
    else:
        stem = "demo" if args.demo else (Path(args.brief).stem if args.brief else None)
        if not stem:
            sys.exit("[エラー] ブリーフか --input を指定してください")
        sel_path = OUT / "takes" / stem / "selection.json"
        if not sel_path.exists():
            sys.exit(f"[エラー] {sel_path} がありません。先に select_takes.py を実行してください")
        sel = read_json(sel_path)
        chosen = sel["decision"].get("selected")
        if not chosen:
            sys.exit("[案内] まだテイクが選ばれていません。聴いて  select_takes.py <ブリーフ> --choose <番号>  で記録してください")
        src = next(Path(t["path"]) for t in sel["takes"] if t["take"] == chosen)
        dst = Path(args.out) if args.out else OUT / "masters" / stem / "master.wav"

    rep = master(src, dst)
    write_json(dst.with_suffix(".report.json"), rep)
    if sel_path:
        sel = read_json(sel_path)
        sel["master"] = {"path": str(dst), "ok": rep["ok"], "lufs": rep["after"].get("lufs"),
                         "true_peak_db": rep["after"].get("true_peak_db")}
        write_json(sel_path, sel)
    rel = dst.relative_to(ROOT) if dst.resolve().is_relative_to(ROOT) else dst
    step(f"書き出しました: {rel} → 次は distrokid_sheet.py で登録用シートを作ります")


if __name__ == "__main__":
    main()
