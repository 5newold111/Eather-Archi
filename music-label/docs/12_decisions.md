# 12. 決めたこと（2026-10-08）

オーナーから任された 3 つの判断と、その理由。どれも後から変えられる。

## 1. フォルダの置き場所 → 非公開の別リポジトリに切り出す（このまま公式サイトに合流させない）

**理由**：公式サイトの公開設定（`.github/workflows/static.yml`）は「リポジトリ全体をそのまま公開する」形になっている。
この作業のプルリクエストの合流先は、そのサイトを公開しているブランチ（`claude/ether-arch-company-site-qfyn98`）。
合流すると、`music-label/` の設定書（アーティストのプロフィール、好きな実在アーティスト名）・戦略資料・データベース設計が
**公式サイトから誰でも見られる状態**になる。最初に決めた「クローズドな環境」と矛盾する。

**やり方**（GitHub で非公開リポジトリを 1 つ作ってから、Mac のターミナルで）

```
# 1. GitHub で New repository → 名前 etherarchi-label → Private → 作成（README などは付けない）
# 2. 今のリポジトリから music-label だけを取り出して、新しいリポジトリに送る
cd ~/Eather-Archi
git fetch origin claude/jolly-edison-bzgnjf
git checkout claude/jolly-edison-bzgnjf
git subtree split --prefix=music-label -b label-only
git push https://github.com/5newold111/etherarchi-label.git label-only:main
# 3. 新しいリポジトリを手元に持ってきて、鍵と作業データを移す
cd ~ && git clone https://github.com/5newold111/etherarchi-label.git
cp ~/Eather-Archi/music-label/.env ~/etherarchi-label/.env
cp -R ~/Eather-Archi/music-label/out ~/etherarchi-label/out 2>/dev/null || true
# 4. 定期実行を入れていたら、新しい場所で登録し直す
cd ~/etherarchi-label && python3 scripts/schedule.py install
```

移したあと、公式サイトのリポジトリのこのプルリクエストは「合流せずに閉じる」。

## 2. SNS のアカウント → まずレーベル単位で 1 組ずつ

**理由**：組ごとに作ると、アカウント作成・本人確認・API の許可・審査が組の数だけ増える（本体だけで 5 倍、子レーベルを入れると 17 倍）。
YouTube は 1 プロジェクト 1 日 6 本までのアップロード枠もある。最初はレーベルのアカウントで全組を発信し、説明文と動画の中で組の名前を出す。

**伸びた組だけ独立させる**：成長分析で「伸びている（up）」が続いた組は、その組専用のアカウントを作り、鍵の名前の後ろに組の名前を付けて入れる
（例 `YOUTUBE_REFRESH_TOKEN__LIGHT`、`IG_ACCESS_TOKEN__LIGHT`）。投稿の仕組みは、組専用の鍵があればそちらを優先する。

| レーベル | YouTube | Instagram | TikTok |
|---|---|---|---|
| 本体（EtherArchi） | 1 チャンネル | 1 アカウント | 1 アカウント |
| 子レーベル | 立ち上げるときに、レーベルごとに 1 組ずつ（鍵は `__FOCUS` など） | 同じ | 同じ |

## 3. 拡大の新人 → ~~確認なしで予約まで進め、取り消しの猶予を取る~~（4. で変更）

> **2026-10-08 変更**：新しい組の自動生成は **提案まで** にした（4.）。以下は変更前の記録。

**理由**：人の手を減らす方針に合わせる。ただし取り消せる時間を十分に取る。

- 毎月 1 日に下書きし、4 週間以上あとの「1 日のある週」にデビューを予約する。制作が始まるのはその 2 週間前なので、**下書きから少なくとも 2 週間は取り消せる**
- 予約したら Mac に通知が出て、管理画面（`out/dashboard.html`）の「人がやること」にも載る
- 取り消し：`python3 scripts/expand_label.py cancel --artist <組>`
- 確認してから予約したくなったら、定期実行の月の仕事を `expand_label.py check`（`--apply` なし）に変える

## 4. アーティストは台帳が正本。自動生成は「提案」まで（2026-10-08）

**理由**：組ごとに人生をかなり作り込み、その時々の想いを歌にしたい。情報を 1 か所で書き足し続けられて、
機械が勝手に決めない形にする。

- それまでの 17 組は白紙にして `templates/archive/2026-10-08/` へ移した
- アーティストの情報は **アーティスト台帳**（`artists.xlsx`）に入れ、逐一書き足す。毎週の曲のテーマ・参考曲はそこから選ぶ（`docs/13_artist_book.md`）
- 機械（Claude）が作るもの（新しい組・来月の近況と歌の種・方針転換の変更）は、台帳に **状態＝提案** の行で足すだけ。採用はオーナー
- 毎月 1 日：返事待ちが無いレーベルに新しい組を 1 組提案。**採用されてから**デビューを予約する（4 週間以上あとの「1 日のある週」）
- 制作の頻度（隔週・休止）とデビュー週は機械が持つ項目のまま（台帳には書かない）

