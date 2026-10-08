# 音楽レーベル 自動制作・配信パイプライン

EtherArchi の 5 つの軸（**形・質・光・時・自**）を 5 組のアーティストに見立て、
毎週水曜に 5 曲を世界配信するための「設計図」と「土台ファイル」をまとめたフォルダです。

> **このフォルダは公式サイトに合流させないこと。** 公式サイトはリポジトリ全体を公開する設定なので、合流すると設定書や戦略資料が公開されます。
> 非公開の別リポジトリに切り出す手順は `docs/12_decisions.md`。

## 用語の説明（初めて読む人向け）

| 用語 | 意味 |
|---|---|
| パイプライン | 作業を流れ作業のように順番につなげた仕組み |
| ブリーフ | 1 曲ぶんの「作曲の指示書」。どの参考曲から何を借りるか、Suno への指示文、歌詞案が入る |
| 解析シート | 参考曲 1 曲を細かく分析した記録。音源そのものは保存しない |
| 枠（スロット） | ブリーフの中で「参考曲から借りる項目」のこと。1 曲につき 1 枠しか借りない |
| Persona | Suno の機能。声と作風を固定して次の曲にも引き継ぐ。アーティスト 1 組につき 1 つ |
| Supabase | データベース（PostgreSQL）とファイル保管庫がセットになったクラウドサービス |
| R2 / B2 | Cloudflare R2、Backblaze B2。音源ファイルの予備保管先 |
| ET | 米国東部時間。配信時刻の基準 |

## フォルダ構成

```
music-label/
├── README.md                     ← このファイル
├── docs/
│   ├── 01_pipeline.md            ← 週間の流れ・2 週間ベルトコンベア・配信時刻
│   ├── 02_analysis_sheet.md      ← 解析シート v2 の記入ガイド
│   ├── 03_artists.md             ← レーベル構想・アーティスト設定書の書き方
│   ├── 04_borrowing_rules.md     ← 1 曲 1 枠ルール・ボーカル 6:2:2・フレーズ変形ルール
│   ├── 05_storage.md             ← Supabase + R2 の構築手順とクローズド化チェックリスト
│   ├── 06_growth.md              ← 収益目標の現実的な数字・月 1 組追加と週 8 曲上限・伸びた要素を次に返す仕組み・歌手の体と癖
│   ├── 07_sublabels.md           ← 子レーベル構造（場面ごとに別アカウント）で上限なく増やす。自動化の段階
│   ├── 08_visuals.md             ← ロゴ・顔を出さないアーティスト写真・ジャケットの自動生成と選択（最初の 5 回は聞く）
│   ├── 09_auth_batch.md          ← 鍵とアカウントの一括設定（Supabase・OpenAI・YouTube・Instagram・TikTok・DistroKid・Suno）
│   ├── 10_automation.md          ← 自動運転の全体像（定期実行の時間割・人がやること・自動で決めていること）
│   ├── 11_plans_and_costs.md     ← 契約するプランと月の費用（Suno のダウンロード上限・DistroKid の組数）
│   └── 12_decisions.md           ← 決めたこと（非公開リポジトリへの切り出し・SNS はレーベル単位・新人は自動予約＋取り消し猶予）
├── supabase/
│   ├── schema.sql                ← テーブル定義（ルールをデータベース側でも強制する）
│   └── storage.sql               ← 非公開バケットとアクセス制御
├── templates/
│   ├── analysis_sheet.schema.json   ← 解析シートの型定義
│   ├── analysis_sheet.example.json  ← 架空の曲での記入例
│   ├── artist_sheet.schema.json     ← アーティスト設定書の型定義
│   ├── brief.schema.json            ← ブリーフの型定義
│   ├── visual_criteria.json         ← ビジュアル候補の採点基準（オーナーの判断で育つ）
│   ├── artists/                     ← 本体レーベル（ドライブ）の 5 組（光・時・形・質・自）。下書き済み
│   │   ├── _template.json           ← 新しい組を足すときのテンプレ
│   │   └── light.json / time.json / shape.json / quality.json / self.json
│   └── labels/                      ← 子レーベル（場面ごと）。代表アーティスト 3 組の下書きを内包
│       ├── _template.json
│       ├── sleep.json               ← 眠り
│       ├── morning.json             ← 朝のコーヒー
│       ├── focus.json               ← 集中
│       └── move.json                ← 歩く・走る
└── scripts/
    ├── select_references.py      ← 参考曲を枠ごとに自動で割り当てる（1 曲 1 枠を機械的に守る）。設定書の癖・声の仕様をブリーフに固定で入れる
    ├── analyze_track.py          ← 参考曲の解析シートを音源から自動で下書き（BPM・キー・構成・エネルギー・音節・韻、Claude で欄を埋める）
    ├── write_brief.py            ← 骨組みに Suno 用の指示文・歌詞の核・タイトル候補を Claude が書き足す（鍵が無ければ指示文だけ書き出す）
    ├── merge_lyrics.py           ← 固定した核と Suno が書いた節を合体（core_fixed）、または Suno の全文を検査・タグ補完（topic_only）して最終歌詞に
    ├── analyze_growth.py         ← 成績から「伸びている組・曲・要素」を分析し、重みとヒント、方針転換の提案を返す
    ├── generate_visuals.py       ← ロゴ・写真・ジャケットの候補を生成し採点。最初の 5 回はオーナーに聞いて基準を学ぶ
    ├── check_names.py            ← 名前の重複確認（デビュー処理の最初に自動実行。checked でないとロゴ・写真を作らない）
    ├── select_takes.py           ← Suno のテイクを計測（長さ・無音・サビの位置・終わり方・歌詞の一致・癖）し、Tier A/B/C に合わせて選ぶ
    ├── master_track.py           ← 選んだテイクを -14 LUFS / -1 dBTP の WAV（44.1kHz / 24bit）に整える
    ├── finalize_cover.py         ← 選んだジャケットを 3000×3000 の JPEG に仕上げて機械チェック
    ├── distrokid_sheet.py        ← その週の DistroKid 登録シート（コピペ用）と確認リストを作る
    ├── supabase_sync.py          ← 設定書・解析シート・毎週の曲を Supabase に登録（鍵が無ければ SQL を書き出す）
    ├── post_social.py            ← 縦動画（サビ 30 秒）と説明文を作り、配信時刻に YouTube / Instagram / TikTok へ投稿
    ├── oauth_youtube.py          ← YouTube 投稿用の合鍵を取得して .env に保存（一括設定のとき 1 回）
    ├── oauth_tiktok.py           ← TikTok 投稿用の合鍵を取得して .env に保存（一括設定のとき 1 回）
    ├── set_key.py                ← 鍵を .env に 1 つずつ安全に書き込む / 入っているか確認する
    ├── debut.py                  ← デビューの段取り（デビュー週の設定 → 名前確認 → ロゴ・写真 → DB → 準備状況）
    ├── run_week.py               ← 1 週間ぶんを段階ごとに全組まとめて流す（brief / lyrics / takes / finish / status / auto）
    ├── schedule.py               ← 定期実行の登録（Mac の launchd）。時間割は docs/10_automation.md
    ├── fetch_trends.py           ← 今週の話題曲・トレンド言語を Claude が Web で調べ、話題曲の解析シートを作る
    ├── plan_collabs.py           ← その週のコラボ（feat. / remix）を決めてブリーフに書く
    ├── collect_metrics.py        ← 成績を集める（DistroKid・Spotify for Artists の書き出し、SNS の API）→ 成長分析へ
    ├── apply_pivot.py            ← 方針転換の提案を設定書に反映（レベル 2 は写真の撮り直しまで）
    ├── expand_label.py           ← 月 1 組の追加（Claude が設定書を下書きしデビュー予約）と隔週への切り替え
    ├── plan_quarterly.py         ← 四半期の EP・コンピレーションの計画と登録シート
    ├── backup_r2.py              ← Cloudflare R2 への予備保管（変わったファイルだけ）
    ├── setup_keys.py             ← 鍵の一括設定ウィザード（手順 → ブラウザ → 保存 → 実際に接続して確認）
    ├── trial.py                  ← 1 組の試運転（本物の鍵で 1 週間分。YouTube は非公開。--cleanup で片付け）
    ├── dashboard.py              ← 管理画面 out/dashboard.html（人がやること・制作の進み具合・配信予定・組の伸び）
    ├── store_profiles.py         ← ストアと SNS のプロフィール文（文字数の上限つき）
    ├── render_roomtour.py        ← Blender のルームツアーを書き出し、SNS の縦動画の背景にする
    └── _common.py, _supabase.py, _claude.py  ← 上のスクリプトが共通で使う部品
```

## 決まっていること（設計の前提）

- **アーティスト**：本体 5 組（光・時・形・質・自）は `templates/artists/` に下書き済み。最終決定と名前はオーナーが行う
- **配信**：毎週水曜 17:00 ET（米国東部時間）に 5 アーティスト × 1 曲 ＝ 5 曲
- **仕込み期間**：2 週間（火曜に作った曲は、2 週間後の水曜に配信）
- **コラボ**：A と B が組む週は「A feat. B」「B feat. A」の **別々の 2 曲**を、それぞれの名義で配信
- **参考曲の借り方**：1 曲 1 枠。歌詞を借りた曲からは他に何も借りない
- **ボーカル**：3 曲から 6:2:2 で配合。「声質」はアーティスト設定時に一度だけ決めて固定、「表現」は曲ごとに配合
- **保存先**：Supabase（東京リージョン）を本番、Cloudflare R2 または Backblaze B2 を予備
- **歌詞**：基本は英語。世界チャートで英語以外の言語が一定割合を超えた週は、担当の 1 組（設定書で `trend_language_ok: true` にした組）がサビの決め台詞などに少量取り入れる
- **分析の対象**：音源だけでなく、MV・公式サイト・SNS・ライブ映像も分析する。将来のジャケット自動生成の参考資料にもなる
- **キャラクター**：組ごとに「生まれ育ち・影響源・作曲の癖・声の仕様（音域・裏声の切り替え点）・得意な歌い方・毎曲入れる癖」を設定書に持ち、毎週のブリーフに固定の制約として入る
- **拡大**：月 1 組ずつ追加。**1 レーベルの**週の総曲数は上限 8。超えたら古い組から隔週に。それでも足りなければ**場面ごとの子レーベル**（別の配信アカウント）を作って増やす。全体の上限は置かない
- **成長ループ**：毎週の成績から伸びている組・曲・要素を分析し、重み（最大 3 倍）とヒントとして次週の割り当てに返す
- **公表方針**：全曲 AI 使用を申告。レーベル自体も「AI と空間デザインで作るバーチャルアーティストのレーベル」と公表する

## 人がやること（毎週 2 か所だけ）

1. **火曜**：Suno で生成した数テイクを聴いて、1 曲選ぶ（5 曲ぶん）。機械が先に計測して、聴く順番と落ちたテイクを教える
2. **水曜**：DistroKid に 5 曲を登録し、配信日時を 2 週間後の水曜 17:00 ET に指定する（登録シートを見ながらコピペ）

それ以外（トレンド取得、ブリーフ生成、コラボの組み合わせ、テイクの計測、音量調整、ジャケットの採点と書き出し、
登録シート、データベース、SNS 投稿、成績回収、成長分析、方針転換、月 1 組の追加、予備保管）は自動で動きます。
時間割と、人がやることの一覧は `docs/10_automation.md`。

## 読む順番

1. `docs/01_pipeline.md` で全体の流れをつかむ
2. `docs/04_borrowing_rules.md` でルールを確認する
3. `docs/03_artists.md` を見ながら、`templates/artists/_template.json` をコピーして 5 組の設定書を書く
4. `docs/02_analysis_sheet.md` を見ながら、参考曲を 1 曲だけ試しに分析してみる
5. `docs/05_storage.md` に沿って Supabase を用意し、`supabase/schema.sql` → `supabase/storage.sql` を流す
6. `pip install -r requirements.txt` のあと `scripts/select_references.py --demo` → `scripts/write_brief.py out/briefs/<週>_demo.json` で、骨組み → ブリーフの流れを確かめる
7. ffmpeg（音と動画の変換ソフト）を入れる（Mac：`brew install ffmpeg`）。そのあと `scripts/select_takes.py --demo` → `scripts/master_track.py --demo` → `scripts/finalize_cover.py --demo` で、テイク選び → 音量 → ジャケットを合成音で確かめる
8. コマンドの順番は `docs/01_pipeline.md` の「コマンドの順番」、鍵の設定は `docs/09_auth_batch.md`
9. 立ち上げ：`scripts/setup_keys.py`（鍵）→ `scripts/trial.py --artist light`（1 組の通し運転）→ `scripts/debut.py --month <YYYY-MM>`（デビューは毎月 1 日のある週）→ `scripts/schedule.py install`（自動運転の開始。`docs/10_automation.md`）
