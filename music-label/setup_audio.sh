#!/bin/bash
# メロディーを起こす部品（Demucs・Basic Pitch）を、音の解析用の置き場（~/.postdog-audio）に入れる（決定 160・180）
#
#   なぜ別の置き場か：この仕組みのふだんの Python（3.15）には、Demucs・Basic Pitch が頼る部品（onnxruntime など）が
#   まだ入らない。音の解析だけは、部品がそろっている Python 3.11 の置き場で動かす（仕組みが自動で見つけて使う）
#
#   使い方（ターミナルで、この music-label フォルダに入ってから）
#     bash setup_audio.sh            # 置き場を作って部品を入れる（何度流しても安全。入っていれば確かめるだけ）
#     bash setup_audio.sh --check    # 調べるだけ
#
#   すること
#     1 uv（Python と部品を入れる道具）が無ければ、Homebrew で入れる
#     2 ~/.postdog-audio に Python 3.11 の置き場を作る（uv が Python 3.11 ごと取ってくる。Mac のほかの Python には触らない）
#     3 Basic Pitch・onnxruntime・Demucs と、音を読む部品を入れる（合わせて 2 GB ほど。10〜20 分）
#     4 実際に読み込めるか確かめる
#   鍵・パスワードには触れない

set -u
cd "$(dirname "$0")" || exit 1
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1
AUD="$HOME/.postdog-audio"
APY="$AUD/bin/python3"
PARTS="basic-pitch onnxruntime demucs librosa soundfile pretty_midi scipy numpy<2 setuptools<81"   # Basic Pitch は numpy 2 とまだ合わず、setuptools の中の pkg_resources を使う（決定 180）

say() { printf '%s\n' "$*"; }
say "▶ メロディーを起こす部品（音の解析用の置き場：~/.postdog-audio）$([ $CHECK = 1 ] && echo '：調べるだけ')"

ok() { [ -x "$APY" ] && "$APY" -c 'import demucs, onnxruntime, librosa; from basic_pitch.inference import predict' >/dev/null 2>&1; }
if ok; then
  say "   ○ もう入っています（$("$APY" --version 2>&1)）"
  exit 0
fi
[ $CHECK = 1 ] && { say "   ✕ まだ入っていません → bash setup_audio.sh"; exit 0; }

# 1 uv
UV="$(command -v uv 2>/dev/null || true)"
for b in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv"; do
  [ -z "$UV" ] && [ -x "$b" ] && UV="$b"
done
if [ -z "$UV" ]; then
  BREW="$(command -v brew 2>/dev/null || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -1)"
  if [ -z "$BREW" ]; then
    say "   ✕ Homebrew がありません。先に  bash setup_mac.sh  を流してください"
    exit 1
  fi
  say "   ・uv（Python と部品を入れる道具）を入れています"
  "$BREW" install uv || { say "   ✕ uv を入れられませんでした（上の表示を見てください）"; exit 1; }
  UV="$(command -v uv 2>/dev/null || ls /opt/homebrew/bin/uv /usr/local/bin/uv 2>/dev/null | head -1)"
fi
say "   ○ uv：$("$UV" --version 2>/dev/null)"

# 2 置き場（Python 3.11）
if [ ! -x "$APY" ] || ! "$APY" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)' 2>/dev/null; then
  say "   ・Python 3.11 の置き場を作っています（~/.postdog-audio）"
  rm -rf "$AUD"
  "$UV" venv --python 3.11 "$AUD" || { say "   ✕ 置き場を作れませんでした（上の表示を見てください）"; exit 1; }
fi
say "   ○ 置き場：$("$APY" --version 2>&1)"

# 3 部品
say "   ・部品を入れています（Basic Pitch・Demucs など。合わせて 2 GB ほど・10〜20 分）"
# shellcheck disable=SC2086
"$UV" pip install --python "$APY" $PARTS || { say "   ✕ 入らなかった部品があります（上の表示を見てください。もう一度流すと、続きから入ります）"; exit 1; }

# 4 確かめる
if ok; then
  say "   ○ 入りました。メロディーの書き起こしは、次の見回り（30 分ごと）から、この置き場で自動で動きます"
  say "【結果】メロディーを起こす部品がそろいました（ダッシュボードの「メロディーを起こす」でも、いますぐ始められます）"
else
  say "   ✕ 入ったはずの部品が読み込めません。この画面の最後の 20 行ほどを、そのまま伝えてください"
  exit 1
fi
