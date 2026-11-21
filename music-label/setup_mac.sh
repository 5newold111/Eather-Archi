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
#     3 ffmpeg（音を扱う道具）と、新しい Python … Homebrew で入れる
#          Mac に最初から入っている Python（3.9）は古くて、この仕組みは動かない（3.10 以上が要る）
#     4 Python の部品（requirements.txt）… 足りないものを入れる
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
  say "   ○ $("$BREW" --version 2>/dev/null | head -1)（$BREW）"
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

py_ok() { [ -x "$1" ] && "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
find_py() {   # Homebrew の Python を先に、無ければいま python3 と打って動くもの（3.10 以上のものだけ）
  PREFIX=""
  [ -n "$BREW" ] && PREFIX="$("$BREW" --prefix 2>/dev/null)"
  if [ -n "$PREFIX" ]; then
    for v in 3 3.14 3.13 3.12 3.11 3.10; do
      if py_ok "$PREFIX/bin/python$v"; then printf '%s' "$PREFIX/bin/python$v"; return 0; fi
    done
  fi
  c="$(command -v python3 2>/dev/null || true)"
  if [ -n "$c" ] && py_ok "$c"; then printf '%s' "$c"; return 0; fi
  return 1
}
PY="$(find_py || true)"
if [ -z "$PY" ] && [ -n "$BREW" ] && [ $CHECK = 0 ]; then
  say "   ・新しい Python を入れています（Mac に最初から入っている 3.9 は古いため。数分かかります）"
  "$BREW" install python || say "   ✕ Python を入れられませんでした（上の表示を見てください）"
  PY="$(find_py || true)"
fi
if [ -n "$PY" ]; then
  say "   ○ $("$PY" --version 2>&1)（$PY）"
else
  say "   ✕ 新しい Python（3.10 以上）がありません。いまあるもの：$(python3 --version 2>&1 || echo なし)"
fi

# ---- 4 Python の部品
head_ "4. Python の部品（requirements.txt）"
if [ -z "$PY" ]; then
  say "   － 新しい Python が入ってから"
elif [ $CHECK = 1 ]; then
  say "   － 調べるだけなので入れません（入っているかは、下の点検に出ます）"
else
  say "   ・足りない部品を入れています（初回は数分かかります）"
  # Homebrew の Python は「自分用の場所（--user）に入れる」と言わないと断るので、その言い方を順に試す
  if "$PY" -m pip install --user --quiet -r requirements.txt 2>/dev/null \
     || "$PY" -m pip install --user --quiet --break-system-packages -r requirements.txt; then
    say "   ○ 部品が入りました"
  else
    say "   ✕ 入らなかった部品があります（上の表示を見てください。もう一度流すと、続きから入ります）"
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
  printf '\n※ ターミナルの窓をいちど閉じて、開き直してください。そのあとの python3 は、新しい Python（%s）になります。\n' "$PY"
fi
printf '\n鍵・パスワードには、この台本は触れていません。鍵は、点検の「次にすること」に出る命令で入れます（チャットには貼りません）。\n'
