# 09. 鍵とアカウントの一括設定（後でまとめて行う作業）

仕組みは鍵が無くても「下書き」まで動くように作ってある。ここに並べた作業を一度に済ませると、
テイク選び・登録シート・データベース・SNS 投稿までが本番で動く。

> **共通のルール**
> - 鍵は `music-label/.env` にだけ置く（Git に入らない）。チャットや画面共有に貼らない
> - 入れるときは `python3 scripts/set_key.py 鍵の名前`（画面に表示されない入力欄で受け取る）
> - 入ったかの確認は `python3 scripts/set_key.py --check`（値は表示しない）

## 一覧（上から順に。合計 2〜3 時間）

| # | サービス | 何に使う | 所要 | 入れる鍵 | 済 |
|---|---|---|---|---|---|
| 1 | Supabase | データベース・音源の保管 | 15 分 | `SUPABASE_URL` `SUPABASE_SERVICE_ROLE_KEY` | [ ] |
| 2 | OpenAI | ロゴ・写真・ジャケット | 5 分 | 残高の追加だけ（鍵は設定済み） | [ ] |
| 3 | YouTube | ショート投稿 | 30 分 | `YOUTUBE_CLIENT_ID` `YOUTUBE_CLIENT_SECRET` → 自動で `YOUTUBE_REFRESH_TOKEN` | [ ] |
| 4 | Instagram | リール投稿 | 30 分 | `IG_USER_ID` `IG_ACCESS_TOKEN` | [ ] |
| 5 | TikTok | 動画投稿 | 30 分＋審査待ち | `TIKTOK_CLIENT_KEY` `TIKTOK_CLIENT_SECRET` `TIKTOK_REFRESH_TOKEN` | [ ] |
| 6 | Cloudflare R2 | 予備の保管 | 10 分 | `R2_*` | [ ] |
| 7 | DistroKid | 配信 | 15 分 | 鍵なし（アカウントとプランだけ） | [ ] |
| 8 | Suno | 声の固定（Persona） | 1 組 10 分 | 鍵なし（Persona ID を設定書へ） | [ ] |

## 1. Supabase（データベース）

1. https://supabase.com でプロジェクト作成。リージョンは **Northeast Asia (Tokyo)**
2. SQL Editor で `supabase/schema.sql` → `supabase/storage.sql` の順に貼って実行
3. Settings → API Keys で次を控えて .env へ
   - Project URL → `python3 scripts/set_key.py SUPABASE_URL`
   - 秘密鍵（新しい画面では `sb_secret_…`、旧画面では `service_role`）→ `python3 scripts/set_key.py SUPABASE_SERVICE_ROLE_KEY`
4. 確認と初回登録

```
python3 scripts/supabase_sync.py seed --apply          # レーベル 4・アーティスト 17 組を登録
python3 scripts/supabase_sync.py references --apply    # 解析シートがあれば
```

鍵を入れる前でも、`supabase_sync.py seed` が書き出す `out/supabase/seed.sql` を SQL Editor に貼れば同じ登録ができる。

## 2. OpenAI（画像）

platform.openai.com → Settings → Billing で残高を追加（月 $10〜20 が目安）。
画像モデルで 403 が出たら、同じ画面の Organization → Verification（本人確認）を済ませる。

```
python3 scripts/generate_visuals.py --artist light --kind debut     # 5 組同時デビューのロゴと写真
```

## 3. YouTube（ショート）

1. https://console.cloud.google.com で新しいプロジェクトを作る（例：etherarchi-label）
2. 「API とサービス → ライブラリ」で **YouTube Data API v3** を有効にする
3. 「OAuth 同意画面」：種類は外部、アプリ名は EtherArchi Label、テストユーザーに自分の Google アカウントを追加
   - 公開ステータスを **本番環境** にする（テストのままだと 7 日で合鍵が切れる。自分だけで使うので審査は不要。許可画面に警告が出るが自分のアプリなので進めてよい）
4. 「認証情報 → OAuth クライアント ID を作成」：種類は **デスクトップ アプリ**
5. 表示された ID と シークレットを入れて、合鍵を取る

```
python3 scripts/set_key.py YOUTUBE_CLIENT_ID
python3 scripts/set_key.py YOUTUBE_CLIENT_SECRET
python3 scripts/oauth_youtube.py              # ブラウザで「許可」→ .env に自動保存（投稿と、再生数の読み取りの 2 つの権限）
python3 scripts/oauth_youtube.py --for focus  # 子レーベル用チャンネルが別なら、レーベルごとに
```

**注意**
- API でアップロードした動画は、プロジェクトが YouTube の監査（API Services Audit）を通るまで **非公開に制限**される。
  公開投稿したいなら、Google の「YouTube API Services - Audit and Quota Extension Form」から申請する（数週間）
- 1 日の上限（10,000 ユニット）で、アップロードは 1 日 6 本まで。本体 5 組は水曜に 5 本なので収まる。子レーベルは曜日が分かれている

## 4. Instagram（リール）

1. Instagram アカウントを **プロアカウント**（クリエイター）に切り替える
2. https://developers.facebook.com でアプリ作成 → ユースケース「Instagram でメッセージとコンテンツを管理」（Instagram ログイン方式）
3. 権限 `instagram_business_basic`・`instagram_business_content_publish`・`instagram_business_manage_insights`（再生数・保存数の読み取り）を追加
4. 「アプリの役割」で自分の Instagram アカウントをテスターに追加し、Instagram 側で承認
5. アプリの画面で「トークンを生成」→ 長期トークン（60 日）と Instagram のユーザー ID が出る

```
python3 scripts/set_key.py IG_USER_ID
python3 scripts/set_key.py IG_ACCESS_TOKEN
python3 scripts/post_social.py refresh-ig      # 月 1 回。60 日で切れるのを延長する
```

- 動画は Supabase の非公開保管庫に上げ、10 分だけ有効な URL を Instagram に渡す（手順 1 が先に必要）
- Instagram には AI 申告の API 項目が無いので、説明文に「Made with AI」を必ず入れている

## 5. TikTok

1. https://developers.tiktok.com で開発者登録 → アプリ作成
2. 製品に **Login Kit** と **Content Posting API** を追加。Direct Post を有効化、スコープ `video.publish`・`video.list`（再生数の読み取り）・`user.info.basic`
3. リダイレクト URI を登録（Login Kit の設定）
4. Client Key・Client Secret・登録したリダイレクト URI を .env に入れ、補助スクリプトで許可を取る

```
python3 scripts/set_key.py TIKTOK_CLIENT_KEY
python3 scripts/set_key.py TIKTOK_CLIENT_SECRET
python3 scripts/set_key.py TIKTOK_REDIRECT_URI     # 例 http://localhost:8765/callback/（アプリに登録したものと同じ）
python3 scripts/oauth_tiktok.py                     # ブラウザで「許可」→ TIKTOK_REFRESH_TOKEN を自動保存
```

- リダイレクト URI が localhost なら自動で受け取る。https の自分のサイトなら、許可のあとのアドレス欄の URL を貼る
- デスクトップ用アプリとして登録した場合は `--pkce` を付ける
- 更新用の鍵は投稿のたびに自動で延長・保存し直す

**注意**
- 審査（Content Posting API の監査）前のアプリは **自分だけに公開**（SELF_ONLY）でしか投稿できない。`post_social.py` は自動でそれを選び、結果に注記を残す
- 投稿時に「AI 生成コンテンツ」（`is_aigc`）を必ず立てている

## 6. Cloudflare R2（予備）

Cloudflare ダッシュボード → R2 → バケット作成（例 `label-backup`、公開アクセスはオフ）→ R2 API トークン
（権限 Object Read & Write、対象はこのバケットだけ）。鍵は `R2_*` に入れる。

```
python3 scripts/backup_r2.py --dry-run     # 何を上げるか確認
python3 scripts/backup_r2.py               # 毎晩 03:30 に自動（定期実行）
```

## 7. DistroKid

- 本体レーベル用アカウントを 1 つ。**5 組以上のアーティストを登録できるプラン**か確認する（プランごとにアーティスト数の上限がある）
- 子レーベルを始めるときは、レーベルごとに別アカウント（`templates/labels/*.json` の `accounts.distrokid_email` には**識別名だけ**を書き、メールアドレスは書かない）
- 毎週の登録は `out/distrokid/<週>/sheet.md` を見ながら。登録後に ISRC / UPC を `<組>.metadata.json` に書き、`supabase_sync.py week --apply`

## 8. Suno（Persona）

各組の最初の 1 曲で声が決まったら、その曲から Persona を作り、ID を設定書の `vocal.suno_persona_id` に書く。
商用利用できるプラン（Pro 以上）で生成した曲だけを配信する。

## 全部終わったら（動作確認）

```
python3 scripts/set_key.py --check                                    # 全部「設定済み」か
python3 scripts/supabase_sync.py week --week <制作週> --apply          # DB と保管庫
python3 scripts/post_social.py post --week <制作週> --dry-run          # 何が投稿されるか
python3 scripts/post_social.py post --week <制作週>                    # 本番（YouTube は公開予約、他は時刻後）
```

最後に定期実行を登録すると、自動運転が始まる（時間割は `docs/10_automation.md`）。

```
python3 scripts/schedule.py install
```
