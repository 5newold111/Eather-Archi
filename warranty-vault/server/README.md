# 保証書Vault — AI読み取りプロキシ

保証書・レシート写真から情報を抽出する Vercel Serverless Function です。
APIキーをアプリに埋め込まないためのプロキシで、エンドポイントは1本のみです。

```
POST /api/extract-warranty
Content-Type: application/json

{ "image": "<base64>", "mediaType": "image/jpeg" }
```

## デプロイ手順（クリックだけ・CLI不要）

1. https://vercel.com/new で `Eather-Arch` リポジトリを Import
2. **Root Directory** を「Edit」から `warranty-vault/server` に変更
3. **Environment Variables** に `ANTHROPIC_API_KEY` を追加して Deploy
4. 発行されたURLをアプリ側に設定: `cd warranty-vault && npm run setup` で
   URLを貼り付けると `eas.json` へ自動書き込みされる

詳しいクリック手順は `../docs/APP_STORE_RELEASE.md` の STEP 2 を参照。
（既存の Mesh-Order 用プロジェクトへ相乗りする場合は `api/extract-warranty.ts` をコピー）

動作確認: ブラウザで `https://<URL>/api/extract-warranty` を開いて
`{"error":"Method Not Allowed"}` が出れば正常。

## セキュリティ方針

- `ANTHROPIC_API_KEY` はサーバー環境変数のみ。クライアント・ログへ一切出さない
- 画像・抽出結果はログに出力しない
- 画像サイズ・メディアタイプをバリデーション（最大 base64 約8MB）
- モデルは `claude-sonnet-5`、構造化出力（JSON Schema）で常に固定形式のJSONを返す
- 失敗時は `{ "error": "読み取りに失敗しました" }` を返し、アプリ側は
  「もう一度試す／手入力で登録する」画面へ遷移する
- **回数制限（料金の使い込み対策）**：このURLはアプリに埋め込まれるため第三者にも知られうる。
  送信元IPごとに1時間10回、全体で1日300回を超えると `429` を返す。ブラウザ（Originヘッダー付き）からの呼び出しは `403`

### 回数制限の設定（Vercel の Environment Variables）

| 変数 | 既定値 | 内容 |
|---|---|---|
| `EXTRACT_LIMIT_PER_IP_PER_HOUR` | `10` | 送信元IPごとの1時間あたりの上限 |
| `EXTRACT_LIMIT_GLOBAL_PER_DAY` | `300` | サービス全体の1日あたりの上限（UTC基準） |
| `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` | なし | 設定すると全サーバー共通で回数を数える（推奨。Vercel の Marketplace から Upstash を無料枠で追加すると自動で入る） |

Upstash を設定しない場合はサーバーのメモリで数えるため、サーバーが複数台に増えると制限が緩くなる。

### Node.js のバージョン

`package.json` の `engines` で **Node.js 24.x（LTS）** を指定している。Vercel の Project Settings の指定より優先され、
セキュリティ修正のマイナー・パッチ版は Vercel が自動で適用する。Node.js 20 は 2026-04-30 にサポートが終わり、
Vercel でも 2026-10-01 に提供終了扱いになったため使わない。

### 必ず設定する：Anthropic の利用上限額

コードの制限に加えて、万一の請求の上振れを確実に止めるため
[Anthropic Console](https://console.anthropic.com/) の **Settings → Limits** で月の利用上限額（Spend limit）を設定する。
このプロキシ専用の Workspace を作り、そこで発行したAPIキーを使うと、他のサービス（Mesh-Order など）と上限を分けられる。

## コストの目安

1400px・quality 0.85 のJPEG 1枚あたり概ね 1,500〜2,500 入力トークン程度。
`effort: "low"` 指定で出力トークンを抑えています。無料版のAI読み取りを
月5回に制限しているのは、このAPIコストをPro課金で回収するためです。
