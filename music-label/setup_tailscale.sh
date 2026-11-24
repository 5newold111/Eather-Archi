#!/bin/bash
# 外出先のスマホから、Mac のダッシュボードを開けるようにする（Tailscale。決定 28・180）
#
#   Tailscale：Mac とスマホだけをつなぐ専用の通り道。インターネットには公開しない（ルーターのポート開放もしない）。個人は無料
#
#   使い方（ターミナルで、この music-label フォルダに入ってから）
#     bash setup_tailscale.sh            # 足りないところを順に案内して、できるところは進める（何度流しても安全）
#     bash setup_tailscale.sh --check    # 調べるだけ
#
#   すること（上から順に。人の操作が要るところは、何をするかを出して止まる）
#     1 Mac に Tailscale のアプリがあるか   … 無ければ、入れるページを開いて止まる
#     2 Tailscale にログインしているか      … していなければ、メニューバーからログインするよう出して止まる
#     3 ダッシュボードを Tailscale に出す   … tailscale serve --bg 8765（自分の機器だけから https で開ける）
#     4 Mac が勝手に眠らない設定            … .env に WEBAPP_AWAKE=1（鍵ではない設定）を足して、ダッシュボードを動かし直す
#     5 スマホで開く住所を出す
#   鍵・パスワードには触れない

set -u
cd "$(dirname "$0")" || exit 1
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1
PORT=8765
say() { printf '%s\n' "$*"; }
stop() {
  printf '\n【ここで止まります】%s\n' "$1"
  shift
  for line in "$@"; do printf '     %s\n' "$line"; done
  printf '   終わったら、もう一度：  bash setup_tailscale.sh\n'
  exit 0
}
say "▶ 外出先のスマホからダッシュボードを開く（Tailscale）$([ $CHECK = 1 ] && echo '：調べるだけ')"

# 1 アプリ（コマンドの名前は、アプリの中のものも探す）
TS=""
for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale /opt/homebrew/bin/tailscale /usr/local/bin/tailscale; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then TS="$c"; break; fi
done
if [ -z "$TS" ]; then
  say "   ✕ Mac に Tailscale のアプリがありません"
  [ $CHECK = 1 ] && exit 0
  [ "$(uname -s)" = "Darwin" ] && open "https://tailscale.com/download/mac" 2>/dev/null
  stop "Mac に Tailscale を入れてください（開いたページか App Store から。入れたら、アプリを 1 回開く）" \
       "・メニューバー（画面の右上）に Tailscale のマークが出たら、そこから「Log in」（Google アカウントでよい）" \
       "・「システム拡張機能」や「VPN 構成の追加」を聞かれたら、許可する"
fi
say "   ○ Tailscale のアプリ：${TS}"

# 2 ログイン
STATE="$("$TS" status --json 2>/dev/null | /usr/bin/python3 -c 'import sys, json; print(json.load(sys.stdin).get("BackendState", ""))' 2>/dev/null || true)"
if [ "$STATE" != "Running" ]; then
  say "   ✕ Tailscale にログインしていないか、つながっていません（いまの状態：${STATE:-不明}）"
  [ $CHECK = 1 ] && exit 0
  stop "メニューバーの Tailscale のマークから「Log in」して、つないでください（Google アカウントでよい）" \
       "・スマホにも Tailscale のアプリを入れて、同じアカウントでログインしておく（あとで使います）"
fi
NAME="$("$TS" status --json 2>/dev/null | /usr/bin/python3 -c 'import sys, json; print((json.load(sys.stdin).get("Self") or {}).get("DNSName", "").rstrip("."))' 2>/dev/null || true)"
say "   ○ つながっています（この Mac の名前：${NAME:-不明}）"

# 3 ダッシュボードを Tailscale に出す（自分の機器だけ。インターネットには出さない）
if "$TS" serve status 2>/dev/null | grep -q "$PORT"; then
  say "   ○ ダッシュボードは、もう Tailscale に出ています"
elif [ $CHECK = 1 ]; then
  say "   ✕ ダッシュボードは、まだ Tailscale に出ていません"
else
  say "   ・ダッシュボードを Tailscale に出しています（tailscale serve --bg ${PORT}）"
  OUT="$("$TS" serve --bg "$PORT" 2>&1)"
  LINK="$(printf '%s' "$OUT" | grep -oE 'https://login\.tailscale\.com/[^ ]+' | head -1)"
  if [ -n "$LINK" ]; then
    [ "$(uname -s)" = "Darwin" ] && open "$LINK" 2>/dev/null
    stop "Tailscale の「HTTPS」と「Serve」を有効にしてください（開いたページで、有効にするボタンを押す。1 回だけ）" "$LINK"
  fi
  if ! "$TS" serve status 2>/dev/null | grep -q "$PORT"; then
    say "   ✕ うまく出せませんでした。下の表示を、そのまま伝えてください"
    printf '%s\n' "$OUT" | tail -8
    exit 1
  fi
  say "   ○ 出しました"
fi

# 4 Mac が勝手に眠らない（ダッシュボードが動いている間。画面は消えてよい）
if grep -q '^WEBAPP_AWAKE=1' .env 2>/dev/null; then
  say "   ○ Mac が勝手に眠らない設定は、もう入っています"
elif [ $CHECK = 1 ]; then
  say "   ✕ Mac が勝手に眠らない設定（WEBAPP_AWAKE=1）は、まだです"
else
  printf '\n# ダッシュボードが動いている間、Mac が勝手に眠らない（外出先のスマホから使うため。setup_tailscale.sh が書きました）\nWEBAPP_AWAKE=1\n' >> .env
  say "   ○ Mac が勝手に眠らない設定を入れました"
  if [ "$(uname -s)" = "Darwin" ]; then
    launchctl kickstart -k "gui/$(id -u)/jp.etherarchi.label.webapp" >/dev/null 2>&1 \
      && say "   ○ ダッシュボードを動かし直しました（設定を読み直すため）" \
      || say "   ・ダッシュボードを動かし直せませんでした。あとで  python3 scripts/schedule.py install  を 1 回"
  fi
fi

# 5 スマホで開く住所
say ""
if [ -n "$NAME" ]; then
  say "【結果】スマホで開く住所：  https://${NAME}/phone"
  say "   1 スマホの Tailscale をつなぐ（同じアカウント）"
  say "   2 Safari で上の住所を開き、共有ボタン →「ホーム画面に追加」"
  say "   ダッシュボードの全部の画面は  https://${NAME}/  です"
else
  say "【結果】Tailscale の名前が分かりませんでした。メニューバーの Tailscale のマークで、この Mac の名前を確かめてください"
fi
say "   Mac のふたを閉じると眠ります（電源・外付けの画面・キーボードをつないでいれば動き続けます）。"
say "   システム設定 → バッテリー → オプション → 「電源アダプタに接続中は…自動でスリープさせない」もオンにしておくと安心です"
