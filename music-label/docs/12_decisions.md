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

**理由**：組ごとに作ると、アカウント作成・本人確認・API の許可・審査が組の数だけ増える（Postdog Records だけで 5 倍、子レーベルを入れると 17 倍）。
YouTube は 1 プロジェクト 1 日 6 本までのアップロード枠もある。最初はレーベルのアカウントで全組を発信し、説明文と動画の中で組の名前を出す。

**伸びた組だけ独立させる**：成長分析で「伸びている（up）」が続いた組は、その組専用のアカウントを作り、鍵の名前の後ろに組の名前を付けて入れる
（例 `YOUTUBE_REFRESH_TOKEN__LIGHT`、`IG_ACCESS_TOKEN__LIGHT`）。投稿の仕組みは、組専用の鍵があればそちらを優先する。

| レーベル | YouTube | Instagram | TikTok |
|---|---|---|---|
| Postdog Records | 1 チャンネル | 1 アカウント | 1 アカウント |
| 子レーベル | 立ち上げるときに、レーベルごとに 1 組ずつ（鍵は `__FOCUS` など） | 同じ | 同じ |

## 3. 拡大の新人 → ~~確認なしで予約まで進め、取り消しの猶予を取る~~（4. で変更）

> **2026-10-08 変更**：新しい組の自動生成は **提案まで** にした（4.）。以下は変更前の記録。

**理由**：人の手を減らす方針に合わせる。ただし取り消せる時間を十分に取る。

- 毎月 1 日に下書きし、4 週間以上あとの「1 日のある週」にデビューを予約する。制作が始まるのはその 2 週間前なので、**下書きから少なくとも 2 週間は取り消せる**
- 予約したら Mac に通知が出て、管理画面（`out/dashboard.html`）の「人がやること」にも載る
- 取り消し：`python3 scripts/expand_label.py cancel --artist <組>`
- 確認してから予約したくなったら、定期実行の月の仕事を `expand_label.py check`（`--apply` なし）に変える

## 4. アーティストは管理表が正本。自動生成は「提案」まで（2026-10-08）

**理由**：組ごとに人生をかなり作り込み、その時々の想いを歌にしたい。情報を 1 か所で書き足し続けられて、
機械が勝手に決めない形にする。

- それまでの 17 組は白紙にして `templates/archive/2026-10-08/` へ移した
- アーティストの情報は **アーティスト管理表**（`artists.xlsx`）に入れ、逐一書き足す。毎週の曲のテーマ・参考曲はそこから選ぶ（`docs/13_artist_book.md`）
- 機械（Claude）が作るもの（新しい組・来月の近況と歌の種・方針転換の変更）は、管理表に **状態＝提案** の行で足すだけ。採用はオーナー
- 毎月 1 日：返事待ちが無いレーベルに新しい組を 1 組提案。**採用されてから**デビューを予約する（4 週間以上あとの「1 日のある週」）
- 制作の頻度（隔週・休止）とデビュー週は機械が持つ項目のまま（管理表には書かない）

## 5. 軸の廃止と、Google スプレッドシートの管理表（2026-10-09）

- アーティストの **軸（形・質・光・時・自）は廃止**。管理表・設定書・提案から取り除いた（データベースの axis 列は空のまま使う）
- アーティストの情報の正本は、オーナーが共有した **Google スプレッドシートの「アーティスト管理表」**。
  機械は `.env` の `ARTIST_BOOK_GSHEET_ID` で読む（鍵なしなら閲覧のみ、サービスアカウントの鍵があれば書き込みも）。手順は `docs/13_artist_book.md`
- 管理表のデザインを作り直した：項目の多いタブ（アーティスト・メンバー・声と曲づくり・見た目）は **1 列 = 1 組の縦型**、
  タブの色分け・状態の色・組の色・■ の帯・プルダウン・見出しのメモ。中身をそのままデザインし直すのは `artist_book.py restyle`

## 6. レーベル名は Postdog Records。Postdog Records の 5 組をすべて採用（2026-10-11）

- Postdog Records（ドライブ）の **配信・プロフィールに出る名前を Postdog Records** にした（`scripts/_common.py` の `MAIN_LABEL_NAME`）。
  中の呼び名（slug）は `drive` のまま。親の EtherArchi はそのまま
- 管理表の『レーベル』欄は「Postdog Records」と書く（空欄・旧表記の「本体」も Postdog Records として読む）。表の中の「本体」はすべて Postdog Records に書き換えた。子レーベルは slug（sleep など）でも名前（SLACK WATER など）でもよい
- オーナーが **Postdog Records の 5 組（DECLAN FAY・HOLLOWPLAN・NAO EASTERLY・SOFT CANTILEVER・TIDEWOOL）をすべて採用**。
  DECLAN の『変更の提案』8 件も採用・反映した。5 組の設定書は `templates/artists/` にある。
  デビュー週はまだ決めていない（5 組は同じ週。`debut.py --month <YYYY-MM>` で決めると、その 2 週間前の週から制作が始まる）
- 名前の重複確認（`check_names.py --all` と `debut.py`）に、レーベル名 Postdog Records も入れた


## 7. ジャケットとアーティスト写真は ChatGPT で作る（2026-10-13）

- 画像はオーナーが ChatGPT で作る。依頼文は各組の設定書の visual（色・光・構図・顔を隠す決まり）から作り、
  `debut_kit/2026-10-11/chatgpt_prompts.json` と Google ドライブの作業書に置いた（顔なし・文字なし・実在の人物に似せない）
- 配信用のジャケットは 3000×3000・RGB・10MB 未満の JPG か PNG にそろえる（ChatGPT の画像が小さければ Claude が整える）
- `generate_visuals.py`（画像 API で候補を作る）は当面使わない。`debut.py` の「ロゴと写真の候補を作る」段は、名前の確認が済んでいないと動かないので今は影響なし
- 作業書は Google ドライブの「Postdog Records デビュー 2026-10」フォルダ。詰まっているところの一覧は、Claude が書き込めるようにアーティスト情報のスプレッドシートの「詰まっているところ」タブに置く（ドライブの同名の表は最初の版で、更新しない）


## 8. NAO EASTERLY を VERNALIGHT に改名。名前の重複は Claude が先に調べる（2026-10-13）

- オーナーが Spotify のアプリで 6 つの名前（Postdog Records と 5 組）を検索し、同じ名前は無かった
- それでも NAO EASTERLY はイギリスの歌手「NAO」と検索で並ぶおそれがあるため、DistroKid に登録する前に **VERNALIGHT（ヴァーナライト）** に改名した。
  vernal（春分の）＋ light（光）。春分の明け方に生まれた本人の設定と、デビュー曲「Due East」の意味はそのまま残す。HOLLOWPLAN・TIDEWOOL と同じ 2 語をつなげた造語
- 中の呼び名（ID・slug）は `nao_easterly` のまま（ほかのタブや設定書とのつながりを切らないため）。表に出る名前・読み方・名前の由来・英語の紹介文・メンバー名・SOFT CANTILEVER のコラボの欄を書き換えた。旧名は管理表のメモ欄に残した
- **これから新しい名前（組・曲・レーベル）を出すときは、Claude が先に Web 検索（Spotify・Bandcamp・SoundCloud を含む）で同じ名前のアーティストがいないか調べ、その結果を添えて見せる。** オーナーの手での確認は求めない。Mac で動かせるようになったら `check_names.py` も合わせて使う


## 9. ジャケットとアーティスト写真は自動で作る（2026-10-13。7 の「オーナーが ChatGPT で作る」を置き換える）

- オーナーの依頼で、画像づくりを自動にした。ChatGPT と同じ OpenAI の画像生成を、正式な窓口（画像 API）から呼ぶ
  - ChatGPT の画面をブラウザで自動操作する方法は採らない（OpenAI の利用規約が、自動の仕組みで出力を取り出すことを禁じているため。クラウドの作業環境からは chatgpt.com にも届かない）
- デビュー用の作業セットは `python3 scripts/kit_images.py`（chatgpt_prompts.json の依頼文 → 画像 → 3000×3000・RGB・10MB 未満の JPG）。
  毎週の流れ（ロゴ・写真・ジャケットの候補づくりと採点）は今までどおり `generate_visuals.py`
- 必要なもの：`music-label/.env` の `OPENAI_API_KEY`（ChatGPT の月額とは別の、使った分だけの支払い）。
  クラウドで動かすときは、環境の設定で api.openai.com への接続を許可し、鍵を環境変数 `OPENAI_API_KEY` に入れる。Mac ならどちらも不要（.env だけ）
- ChatGPT の画面で手作りした画像も、`debut_kit/<日付>/images/<組の ID>_cover.png` のように置けば `--fit-only` で整えられる
- できた画像は Google ドライブの「音楽レーベルアプリ/アーティスト情報/<アーティスト名>」に自動で保存する（ジャケットもアーティスト写真も同じフォルダ。名前は <アーティスト名>_cover.jpg・<アーティスト名>_photo.jpg）。
  同じ名前のフォルダ（大文字・小文字や全角・半角の違いは同じとみなす）があればそこへ入れ、無ければ作る。
  Mac の「Google ドライブ（パソコン版）」の同期フォルダにコピーする方式なので、鍵の追加は要らない（`scripts/_gdrive_local.py`）


## 10. アーティスト写真は人物が主役。顔の中央は手・物・仮面・影で隠す（2026-10-13）

- オーナーの判断：最初の自動生成の写真は「人物が小さい後ろ姿で、景色が主役」で単調だったため作り直す
- 新しい決まり（`debut_kit/2026-10-11/chatgpt_prompts.json` の common_photo）
  - 人物が画面の 5〜7 割を占める、腰から上か胸から上の構図。後ろ姿だけの構図にはしない
  - 顔の全体は見せない。目元を含む顔の中央は、手・物・仮面・影のどれかで隠す。見せてよいのは顔の輪郭・顎・耳・髪まで
  - 宣材写真のような意図のある照明。浅い被写界深度、肌や布の質感が分かる高画質
- 組ごとの隠し方：DECLAN FAY＝カセットテープを目の前にかざす／HOLLOWPLAN＝グレーボードで自作した四角い仮面／
  VERNALIGHT＝朝日をさえぎる手の影／SOFT CANTILEVER＝ギターのヘッド・トレーシングペーパー・交差したスティック／TIDEWOOL＝2 人で広げた編みかけのセーター
- 画質：画像 API に高画質（quality=high）を指定し、拡大後に軽くシャープをかける
- 管理表の「見た目」タブの face_concealment（「被り物やマスクは使わない」「後ろ姿が基本」）とは食い違う。
  毎週の自動の流れ（generate_visuals.py）は今も管理表の決まりで作るので、そろえるなら管理表に「変更の提案」を入れて採用する
