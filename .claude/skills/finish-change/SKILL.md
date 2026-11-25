---
name: finish-change
description: music-label（Postdog Records の仕組み）の変更を締める手順。試験 → 本名の見張り → 決定の記録 → コミット → GitHub に送る → GitHub 側の試験を見届ける。コードや説明書を変えて、コミットや push をする前に必ず使う。
---

# 変更の締め方（Postdog Records の仕組み）

コードや説明書を変えたら、最後に必ずこの順で締める。順番を飛ばさない。

## 1. 試験を流す

```bash
cd music-label && python3 -m unittest discover -s tests 2>&1 | tail -3
```

- 全部 OK になるまで送らない。落ちた試験は、試験を消したり飛ばしたりせず、原因を直す。
- 新しく足した機能・直した不具合には、試験を足す（`tests/test_*.py`）。実機で起きた不具合は、その形を再現する試験にする。
- Python 3.15（オーナーの Mac の版）で動くかが関わる変更では、3.15 でも流す。作業場に無ければ `uv python install 3.15` → `python3.15 -m venv <置き場>` で用意し、`pip install -r requirements.txt pretty_midi playwright` を入れて流す。

## 2. 送るものを選ぶ

```bash
git add -A music-label .claude CLAUDE.md
git add -f music-label/tests/*.py music-label/tests/fixtures 2>/dev/null   # tests は除外の決まりに当たることがあるので -f で足す
git reset -q music-label/tests/__pycache__ 2>/dev/null                      # 控えファイル（__pycache__）は送らない
git status --short
```

- `.env`・鍵・`out/`・音源・画像は送らない（.gitignore にある。`git status` で紛れていないか目で見る）。
- `git stash -u` は使わない（ほかの作業の、まだ記録していないファイルを一時的に消してしまったことがある）。

## 3. 本名・個人の情報の見張り（必ず）

```bash
python3 music-label/scripts/name_guard.py
```

- `【結果】本名・個人の情報は見つかりませんでした` になるまで送らない。
- 本名はオーナーの Mac の `.env`（OWNER_LEGAL_NAME）か、環境変数 `NAME_GUARD` からだけ読む。**本名・Mac の利用者名を、コード・説明書・コミットの文・この手順書に書かない。** 見張りの言葉も、ファイルに書き足さない。
- クラウドの作業場で `NAME_GUARD` が無いと、見張れるのは `/Users/…` の場所とメールアドレスだけになる（結果の行にそう出る）。そのときは `git diff --cached` を自分でも読み、本名らしい言葉が無いか確かめる。

## 4. 決定の記録（仕組みの決まりが変わったとき）

- `music-label/docs/12_decisions.md` の最後の決定に続けて書く（新しい決まりなら `## <番号>. 題（日付。オーナーの指示）`、同じ流れの追加なら、その決定の中の箇条書き）。
- 新しい番号を足したら、`music-label/HANDOFF.md` の「決めたこと」の行の範囲（`1〜<番号>`）も直す。
- 書くこと：何が起きたか（実機の症状）・原因・どう直したか・試験。オーナーの言葉は「」で残す。

## 5. コミット

- コミットの文は日本語。1 行目に「何をどう変えたか」、空行のあと、理由と中身を箇条書きで。
- 最後に、その会話で指示された 2 行（`Co-Authored-By: …` と `Claude-Session: …`）を付ける。**モデルの名前・番号を、コミットの文の本文や PR に書かない**（指示された 2 行以外）。

## 6. GitHub に送る

```bash
git pull -q --rebase --autostash origin <作業のブランチ>
git push -q -u origin <作業のブランチ>
```

- 作業のブランチは、その会話で指示されたもの（いまは `claude/jolly-edison-bzgnjf`）。ほかのブランチには送らない。
- 送るのを断られたら、強制（force）で送らない。`git fetch` → `git merge` で取り込んでから送り直す。
- 「This repository moved」の表示は、名前が変わっただけなので気にしない。
- **PR は合流（マージ）しない**（オーナーの決まり。PR の題にも「合流しない」とある）。

## 7. GitHub 側の試験を見届ける

- GitHub のアプリの知らせは届かない作りなので、自分で見に行く。送ってから 8 分ほどあとに、PR の check runs（`pull_request_read` の `get_check_runs`）を確かめる。待つあいだは `send_later` で自分に知らせを予約し、`sleep` で待たない。
- 赤なら原因を直して、この手順の 1 からやり直す。「たまたま」と決めつけない。
- 緑なら、オーナーには知らせなくてよい（変化が無ければ黙って終える）。

## 8. オーナーへの報告

- 何を直したか・オーナーが Mac ですること（命令は `cd ~/Eather-Archi/music-label && …` から始まる 1 行で）・送ってほしい表示、を短く。
- Mac は 10 分ごとの自動更新（self_update）で、送った変更を取り込む。すぐ試してほしいときだけ `git pull` を付ける。
