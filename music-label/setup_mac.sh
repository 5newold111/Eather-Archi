#!/bin/bash
# まっさらな Mac（初期化したあと・買い替えたあと）で、最初に 1 回だけ流す「用意の台本」（決定 180）
#
#   使い方（ターミナルで、この music-label フォルダに入ってから）
#     bash setup_mac.sh            # 足りない道具を調べて、入れられるものは入れる
#     bash setup_mac.sh --check    # 調べるだけ（何も入れない）
#
#   すること（上から順に。何度流しても安全。もう入っているものは飛ばす）
#     1 開発者ツール（git など）    … 無ければ、入れる命令を表示して止まる（画面のボタンを押す操作が要るため）
#     2 Homebrew（道具を入れる仕組み）… 無ければ、入れる命令を表示して止まる（Mac のパスワードの入力が要るため）
#     3 ffmpeg（音を扱う道具）と新しい Python（Homebrew の最新。いまは 3.15）… Homebrew で入れる
#          Mac に最初から入っている Python（3.9）は古くて、この仕組みは動かない
#     4 Python の部品（requirements.txt）… この仕組み専用の置き場（music-label/.venv）を作って、そこに入れる
#     5 Chrome があるかを見る
#     6 立て直しの点検（python3 scripts/recover.py check）を出す
#
#   しないこと：鍵・トークン・パスワードには一切触れない（鍵は、あとで python3 scripts/setup_keys.py で入れる）。
#               管理者の権限（sudo）が要ることを、勝手には進めない。

set -u
cd "$(dirname "$0")" || exit 1
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1
ORIG_PY="$(command -v python3 2>/dev/null || true)"   # この窓でいま python3 と打つと動くもの

say()  { printf '%s\n' "$*"; }
head_() { printf '\n■ %s\n' "$*"; }
stop() {   # 人の操作が要るところで止まる
  printf '\n【ここで止まります】%s\n' "$1"
  shift
  for line in "$@"; do printf '     %s\n' "$line"; done
  printf '   終わったら、もう一度：  bash setup_mac.sh\n'
  exit 0
}

say "▶ Mac の用意（Postdog Records の仕組み）$([ $CHECK = 1 ] && echo '：調べるだけ')"
MAC=0
[ "$(uname -s)" = "Darwin" ] && MAC=1
[ $MAC = 0 ] && say "   ※ ここは Mac ではありません。調べるだけにします" && CHECK=1

# ---- 1 開発者ツール
head_ "1. 開発者ツール（git など）"
if [ $MAC = 1 ] && ! xcode-select -p >/dev/null 2>&1; then
  say "   ✕ まだ入っていません"
  [ $CHECK = 1 ] || stop "開発者ツールを入れてください（出てきた窓で「インストール」を押す。10 分ほど）" "xcode-select --install"
elif command -v git >/dev/null 2>&1; then
  say "   ○ $(git --version 2>/dev/null)"
else
  say "   ✕ git がありません"
fi

# ---- 2 Homebrew
head_ "2. Homebrew（道具を入れる仕組み）"
BREW="$(command -v brew 2>/dev/null || true)"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -z "$BREW" ] && [ -x "$b" ] && BREW="$b"
done
if [ -z "$BREW" ]; then
  say "   ✕ まだ入っていません"
  if [ $CHECK = 0 ]; then
    stop "Homebrew を入れてください（途中で Mac のパスワードを聞かれます。打っても画面には出ません）" \
      '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  fi
else
  say "   ○ $("$BREW" --version 2>/dev/null | head -1)（${BREW}）"
  eval "$("$BREW" shellenv 2>/dev/null)"   # この台本の中で、Homebrew の道具を使えるようにする
  if [ $MAC = 1 ] && [ $CHECK = 0 ]; then    # 新しいターミナルでも python3・ffmpeg が Homebrew のものになるように（1 回だけ書く）
    PROFILE="$HOME/.zprofile"
    if ! grep -q 'brew shellenv' "$PROFILE" 2>/dev/null; then
      printf '\n# Homebrew（道具を入れる仕組み）の道具を使えるようにする（setup_mac.sh が書きました）\neval "$(%s shellenv)"\n' "$BREW" >> "$PROFILE"
      say "   ○ ターミナルの設定（~/.zprofile）に、Homebrew を使う 1 行を足しました"
    fi
  fi
fi

# ---- 3 ffmpeg と Python
head_ "3. ffmpeg（音を扱う道具）と、新しい Python"
if command -v ffmpeg >/dev/null 2>&1; then
  say "   ○ ffmpeg があります"
elif [ -n "$BREW" ] && [ $CHECK = 0 ]; then
  say "   ・ffmpeg を入れています（数分かかります）"
  "$BREW" install ffmpeg || say "   ✕ ffmpeg を入れられませんでした（上の表示を見てください）"
else
  say "   ✕ ffmpeg がありません（仕上げ・解析・縦動画づくりに要ります）"
fi

# Python の版：Homebrew の最新の Python（2026-11 時点で 3.15）を使う。3.10 より古いもの（Mac に最初から入っている 3.9）は使わない（決定 180）
py_ok() { [ -x "$1" ] && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
py_ver() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }
find_py() {   # Homebrew の python3（いちばん新しい版）→ 版の番号つきのもの（新しい順）→ いま python3 と打って動くもの
  PREFIX=""
  [ -n "$BREW" ] && PREFIX="$("$BREW" --prefix 2>/dev/null)"
  for d in "$PREFIX/bin" /opt/homebrew/bin /usr/local/bin; do
    if [ -n "$d" ] && py_ok "$d/python3"; then printf '%s' "$d/python3"; return 0; fi
  done
  for v in 3.15 3.14 3.13 3.12 3.11 3.10; do
    for d in "$PREFIX/bin" /opt/homebrew/bin /usr/local/bin; do
      if [ -n "$d" ] && py_ok "$d/python$v"; then printf '%s' "$d/python$v"; return 0; fi
    done
  done
  c="$(command -v python3 2>/dev/null || true)"
  if [ -n "$c" ] && py_ok "$c"; then printf '%s' "$c"; return 0; fi
  return 1
}
BASEPY="$(find_py || true)"
if [ -z "$BASEPY" ] && [ -n "$BREW" ] && [ $CHECK = 0 ]; then
  say "   ・新しい Python を入れています（Mac に最初から入っている 3.9 は古いため。数分かかります）"
  "$BREW" install python || say "   ✕ Python を入れられませんでした（上の表示を見てください）"
  BASEPY="$(find_py || true)"
fi
if [ -n "$BASEPY" ]; then
  say "   ○ $("$BASEPY" --version 2>&1)（${BASEPY}）"
else
  say "   ✕ 新しい Python（3.10 以上）がありません。いまあるもの：$(python3 --version 2>&1 || echo なし)"
fi

# ---- 4 Python の部品（この仕組み専用の置き場 .venv に入れる。Mac のほかの Python には手を入れない）
head_ "4. Python の部品（requirements.txt）"
VENV="$PWD/.venv"
PY=""
if [ -x "$VENV/bin/python3" ] && py_ok "$VENV/bin/python3" && { [ -z "$BASEPY" ] || [ "$(py_ver "$VENV/bin/python3")" = "$(py_ver "$BASEPY")" ]; }; then
  PY="$VENV/bin/python3"     # 置き場の Python の版が、使う Python と同じなら、そのまま使う
elif [ -n "$BASEPY" ] && [ $CHECK = 0 ]; then
  [ -d "$VENV" ] && say "   ・置き場の Python の版が変わったので、作り直します" && rm -rf "$VENV"   # 中身は部品だけ（記録や鍵は入っていない）
  say "   ・この仕組み専用の部品の置き場（.venv）を作っています"
  if "$BASEPY" -m venv "$VENV"; then PY="$VENV/bin/python3"; else say "   ✕ 置き場を作れませんでした（上の表示を見てください）"; fi
fi
if [ -z "$PY" ]; then
  [ $CHECK = 1 ] && say "   － 調べるだけなので、置き場（.venv）は作りません" || say "   － 使える Python が入ってから"
elif [ $CHECK = 1 ]; then
  say "   － 調べるだけなので入れません（入っているかは、下の点検に出ます）"
else
  say "   ・足りない部品を入れています（初回は 5〜15 分かかります）"
  "$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
  if "$PY" -m pip install --quiet -r requirements.txt; then
    say "   ○ 部品が入りました"
  else
    say "   ・まとめては入らなかったので、1 つずつ入れます"
    FAILED=""
    while IFS= read -r line; do
      pkg="$(printf '%s' "$line" | sed 's/#.*//' | tr -d '[:space:]')"
      [ -z "$pkg" ] && continue
      if "$PY" -m pip install --quiet "$pkg" >/dev/null 2>&1; then say "     ○ ${pkg}"; else say "     ✕ ${pkg}"; FAILED="$FAILED $pkg"; fi
    done < requirements.txt
    if [ -z "$FAILED" ]; then say "   ○ 部品が入りました"; else say "   ✕ 入らなかった部品：${FAILED}（この行をそのまま伝えてください）"; fi
  fi
  # 歌詞の聞き取りの部品（faster-whisper）：Python 3.15 では、頼っている onnxruntime（声の区間を見つける部品）にまだ 3.15 版が無い。
  # onnxruntime を外して入れる（聞き取りは onnxruntime が無くても動く）。3.14 までは上の一覧で、ふつうに入る
  if ! "$PY" -c 'import faster_whisper' >/dev/null 2>&1; then
    say "   ・歌詞の聞き取りの部品を、この Python の版に合わせて入れています"
    if "$PY" -m pip install --quiet av ctranslate2 tokenizers huggingface-hub tqdm && "$PY" -m pip install --quiet --no-deps faster-whisper \
       && "$PY" -c 'import faster_whisper' >/dev/null 2>&1; then
      say "     ○ faster-whisper（声の区間を見つける部品は、3.15 版が出るまで使わずに聞き取ります）"
    else
      say "     ✕ faster-whisper（歌詞の聞き取りだけが使えません。ほかは動きます）"
    fi
  fi
  if [ $MAC = 1 ]; then    # 新しいターミナルで python3 と打つと、この置き場の Python になるように（1 回だけ書く）
    PROFILE="$HOME/.zprofile"
    if ! grep -qF "$VENV/bin" "$PROFILE" 2>/dev/null; then
      printf '\n# Postdog Records の仕組みの Python（部品入り）を先に使う（setup_mac.sh が書きました）\nexport PATH="%s/bin:$PATH"\n' "$VENV" >> "$PROFILE"
      say "   ○ ターミナルの設定（~/.zprofile）に、この置き場の Python を先に使う 1 行を足しました"
    fi
  fi
fi

# ---- 5 Chrome
head_ "5. Chrome（Suno・DistroKid への入力に使います）"
if [ -d "/Applications/Google Chrome.app" ] || [ -d "$HOME/Applications/Google Chrome.app" ]; then
  say "   ○ あります"
elif [ $MAC = 1 ]; then
  say "   ✕ ありません → https://www.google.com/chrome/ から入れてください"
else
  say "   － Mac だけの項目です"
fi

# ---- 6 点検
head_ "6. 立て直しの点検"
RUN="${PY:-$(command -v python3 2>/dev/null || true)}"
if [ -n "$RUN" ]; then
  "$RUN" scripts/recover.py check
else
  say "   ✕ Python が見つからないので、点検を出せません"
fi

if [ -n "$PY" ] && [ "$ORIG_PY" != "$PY" ] && [ $MAC = 1 ]; then
  printf '\n※ ターミナルの窓をいちど閉じて、開き直してください。そのあとの python3 は、部品の入った Python（%s）になります。\n' "$PY"
fi
printf '\n鍵・パスワードには、この台本は触れていません。鍵は、点検の「次にすること」に出る命令で入れます（チャットには貼りません）。\n'
