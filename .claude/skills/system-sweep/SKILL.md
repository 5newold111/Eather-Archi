---
name: system-sweep
description: music-label（Postdog Records の仕組み）を、作り物のレーベルで全部試しに流して、エラーの出る所を洗い出す点検。大きな変更のあと・Python の版が変わったとき・オーナーに「全部動かしてエラーを出して」と言われたときに使う。
---

# 全部を試しに流す点検

オーナーの Mac にも外のサービスにも触らずに、作り物のレーベル（`tests/_fixture.py` の Project。アーティスト・曲のいろいろな段階・SSD・受け口のファイル）で、仕組みを全部流す。2026-11 にこの点検で、配信済みの曲を二重に登録しかねない所など 10 件以上が見つかった。

## 1. 用意

```bash
cd music-label
python3 --version                               # いつもの Python
ls <3.15 の置き場>/bin/python 2>/dev/null       # オーナーの Mac は Python 3.15。無ければ作る ↓
uv python install 3.15 && python3.15 -m venv <置き場> && <置き場>/bin/python -m pip install -q -r requirements.txt pretty_midi playwright pyflakes
```

- 置き場は会話の作業用フォルダ（scratchpad）に作る。リポジトリの中には作らない。

## 2. 流す（3 つ。Python の版ごとに）

```bash
python3 tests/sweep_static.py python3 <3.15 の python>      # 読むだけ：前に実際に起きた不具合の形（数秒）
python3 tests/sweep_jobs.py python3                          # 定期実行の全部の命令・各道具の主な命令・--help（数分）
python3 tests/sweep_jobs.py <3.15 の python>
python3 tests/sweep_web.py <結果のフォルダ> --run            # ダッシュボードの全ページ・全ボタン（10 分ほど）
<3.15 の python> tests/sweep_web.py <結果のフォルダ> --run
```

- 時間がかかるので、2 つ以上を同時に進めたいときは、サブエージェント（fork）に分けてよい。そのときも「リポジトリは変えない・コミットしない」と伝える。
- `sweep_web.py --run` は、押して安全な操作だけを実際に流す。DistroKid・Suno への入力、ブラウザ・ターミナルを開く、削除、外す、診断を送る、は流さず、命令の組み立てだけを見る。
- 画面の崩れは、Playwright で開いて確かめる（スマホの幅 390px と、パソコンの幅）。JavaScript のエラー（console の error・pageerror）も見る。

## 3. 結果の読み方

- **✕（Traceback・時間切れ・サーバーのエラー・受け手の無いボタン）は、ほぼ本物の不具合**。原因の行まで読んでから直す。
- **終了コードが 0 以外**の中には、意図した止まり方が混ざる。次は不具合ではない：鍵が無い（Notion・R2・Instagram など）・Mac 専用（`schedule.py status`）・作り物に無いもの（SSD の既存楽曲・表計算の部品 openpyxl・書体）・`plays.py collect` の終了コード 2（「まだ集まらない」の意味）。
- ただし「予想どおりの止まり方」が、ダッシュボードで赤い「失敗」と出ていないかは見る（オーナーを不安にさせる）。
- 3.15 だけで落ちるものは、Python の新しい版の変更が原因のことが多い（例：引数の説明文の % が 3.14 から禁止）。

## 4. 実機でしか分からないもの（点検では見えない。報告に添える）

- Mac の古い bash（台本 `*.sh`）・Homebrew・launchd（定期実行の登録）・キーチェーン・専用の Chrome のログイン。
- 外のサービスの本当の返事（Apple Music・Deezer・Spotify・DistroKid・Suno）。作業場から問い合わせるときは、環境の通信の許可（`update_environment`）を足す。
- Mac 用の台本は、`sed 's/^\[ \$MAC = 0 \].*/MAC=1/'` で Mac のつもりにし、`brew`・`xcode-select` の代わりを `PATH` の前に置いて、一時フォルダの HOME で流すと、多くの道筋を試せる。

## 5. 直し方と報告

- 見つけた所は、重い順（高：毎日の仕事が落ちる・配信を誤る／中：ボタンを押すと落ちる／低：表示）に一覧にして、オーナーに出す。
- 直すときは、その形を再現する試験を足してから直す（`tests/test_python_versions.py` の SweepFixes などに集める）。
- 直したら `finish-change` スキルの手順で締める。決定の記録には「全部を試しに流す点検で見つけた」と書く。
- 点検の台本そのもの（`tests/sweep_*.py`）に、打ち方が間違っている命令があれば、台本も直す（誤検出を減らす）。
