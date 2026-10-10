# 05. 保存先：Supabase ＋ Cloudflare R2 / Backblaze B2

## 方針

「できるだけクローズドに保存する」ため、**音源ファイル**と**曲情報（表データ）**を分けて考えます。

| 役割 | サービス | 理由 |
|---|---|---|
| 曲情報のデータベース ＋ 音源の本番保管 | **Supabase**（東京リージョン、Pro プラン） | 1 つの管理画面で完結。保管庫は非公開がデフォルト。行単位のアクセス制御（RLS）がある |
| 音源の予備保管（バックアップ）・SNS 配信用 | **Cloudflare R2** | ダウンロード転送料が無料。S3 互換なので既存ツールがそのまま使える |
| 長期アーカイブ（任意） | **Backblaze B2** | 最安。年単位で寝かせる没テイクなどに |

**使わないもの**：Google Drive / Dropbox（共有リンク事故が起きやすく、データベースとして使えない）、Notion / Airtable（音源に不向きで、第三者サービス上にデータが広く置かれる）。

## 容量と費用の見積もり

| 内容 | 量 |
|---|---|
| 完成曲（WAV 約 50 MB ＋ MP3 約 7 MB）× 週 5 曲 | 週 約 300 MB、年 約 15 GB |
| 没テイク（5 曲 × 6 テイク × MP3） | 週 約 200 MB、年 約 10 GB |
| ジャケット（3000×3000 PNG）、Blender レンダリング | 年 数 GB |
| **合計** | **年 25〜35 GB** |

Supabase Pro（月約 $25、保管 100 GB 込み）で 3 年ほど収まります。R2 は保管 $0.015/GB/月 なので、予備に全量置いても月数十円〜数百円です。

## 構築手順

### 1. Supabase

1. https://supabase.com でプロジェクトを作成。リージョンは **Northeast Asia (Tokyo)**
2. 「Settings → Billing」で Pro プランに変更（保管 1 GB の無料枠では足りません）
3. 「SQL Editor」で `supabase/schema.sql` の内容を貼り付けて実行（テーブルができます）
4. 続けて `supabase/storage.sql` を実行（非公開バケットとアクセス制御ができます）
5. 「Settings → API」で次の 2 つを控える
   - `Project URL`
   - `service_role` キー（**自動処理専用。絶対に公開しない**）
6. 「Authentication → Providers」で、使わないログイン方法を全部オフにする（メール ＋ 2 段階認証だけ残す）

7. 手元のファイルの登録は `scripts/supabase_sync.py`（`seed` → `references` → 毎週 `week --week <週>`）。
   鍵が入る前は `out/supabase/*.sql` を書き出すので、SQL Editor に貼っても同じ登録ができる。SNS 用の縦動画は `social` バケットに置く

### 2. Cloudflare R2（予備）

1. Cloudflare ダッシュボード → R2 → バケット作成（例：`label-backup`）。**公開アクセスはオフのまま**
2. 「R2 API トークン」を作成（権限：Object Read & Write、対象：このバケットだけ）
3. `rclone` で Supabase の保管庫から R2 へ毎晩同期する（下記）

### 3. 鍵の置き場所

```
.env（Git に入れない。.gitignore に追加済みにすること）
  SUPABASE_URL=
  SUPABASE_SERVICE_ROLE_KEY=
  R2_ACCOUNT_ID=
  R2_ACCESS_KEY_ID=
  R2_SECRET_ACCESS_KEY=
  R2_BUCKET=label-backup
```

自動処理（GitHub Actions や n8n）で使うときは、各サービスの「シークレット」機能に登録し、コードや設定ファイルには書きません。

## 保管庫の中のフォルダ構造

```
masters/                              ← 配信した最終音源（バケット：masters）
  {artist_slug}/{release_id}/
    master.wav
    master.mp3
    cover_3000.png
    metadata.json                     ← 曲名・feat.・言語・ISRC・配信日時・Suno 曲 ID・使った指示文
    brief.json                        ← 火曜のブリーフ（どの参考曲から何を借りたか）

generations/                          ← 没テイクも含む全テイク（バケット：generations）
  {brief_id}/
    take_01.mp3 … take_06.mp3
    takes.json                        ← 各テイクの Suno 曲 ID・聴いたときのメモ・選んだかどうか

covers/                               ← Blender レンダリング元データとジャケット（バケット：covers）
  {artist_slug}/{release_id}/
    scene.blend
    render_4k.png
    cover_3000.png

references/                           ← 参考曲の解析結果だけ（音源は置かない）（バケット：references）
  {reference_id}/
    analysis.json                     ← 解析シート v2
    web_snapshots/                    ← MV・HP・SNS の代表的な場面の静止画（分析用、少数）
```

`metadata.json` に **Suno の曲 ID と使った指示文を必ず残す**のがポイントです。
後で「この曲はこう作った」と権利の証明や再現に使えます。

## クローズドにするためのチェックリスト（必須）

- [ ] 保管庫のバケットは**全て非公開**。公開 URL を作らない
- [ ] 取り出しは**期限付き URL（数分で失効）**だけ。SNS 投稿ツールにもこの URL を渡す
- [ ] 管理アカウントに **2 段階認証**
- [ ] 自動処理用の鍵は環境変数か秘密管理サービスに置き、コードに書かない。`.env` は `.gitignore` に入れる
- [ ] **東京リージョン**を選ぶ（データの所在を国内に）
- [ ] バージョン管理を有効にし、**3-2-1 バックアップ**（本番＝Supabase、2 つ目＝R2、3 つ目＝手元の NAS や外付け）
- [ ] アクセスログを有効化し、月 1 回確認する
- [ ] 本当に秘匿したい場合は、アップロード前に**手元で暗号化**（`rclone crypt`）し、クラウド側には鍵を置かない
- [ ] 解析シート（参考曲の分析）は `references` バケットの中だけで使い、外部に出さない

## 毎晩のバックアップ（rclone の例）

```bash
# Supabase Storage は S3 互換の入口を持っています（Settings → Storage → S3 Connection）
# rclone config で "supabase" と "r2" の 2 つを登録したあと：

rclone sync supabase:masters      r2:label-backup/masters      --progress
rclone sync supabase:generations  r2:label-backup/generations  --progress
rclone sync supabase:covers       r2:label-backup/covers       --progress
rclone sync supabase:references   r2:label-backup/references   --progress
```

`sync` は「本番と同じ状態にする」命令なので、本番で消したものは予備からも消えます。
誤削除に備えるなら、R2 側でバージョン管理を有効にするか、週 1 回は `copy`（追加だけ）で別フォルダに取ります。
