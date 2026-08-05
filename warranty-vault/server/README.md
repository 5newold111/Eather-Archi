# 保証書Vault — AI読み取りプロキシ

保証書・レシート写真から情報を抽出する Vercel Serverless Function です。
APIキーをアプリに埋め込まないためのプロキシで、エンドポイントは1本のみです。

```
POST /api/extract-warranty
Content-Type: application/json

{ "image": "<base64>", "mediaType": "image/jpeg" }
```

## デプロイ手順

1. Vercel で新規プロジェクトを作成し、この `server/` ディレクトリをルートに指定
   （既存の Mesh-Order 用プロジェクトへ相乗りする場合は `api/extract-warranty.ts` をコピー）
2. 環境変数 `ANTHROPIC_API_KEY` を Production / Preview に設定
3. デプロイ後のURL `https://<project>.vercel.app/api/extract-warranty` を
   アプリ側の `EXPO_PUBLIC_EXTRACT_API_URL`（`eas.json` の env）に設定

## セキュリティ方針

- `ANTHROPIC_API_KEY` はサーバー環境変数のみ。クライアント・ログへ一切出さない
- 画像・抽出結果はログに出力しない
- 画像サイズ・メディアタイプをバリデーション（最大 base64 約8MB）
- モデルは `claude-sonnet-5`、構造化出力（JSON Schema）で常に固定形式のJSONを返す
- 失敗時は `{ "error": "読み取りに失敗しました" }` を返し、アプリ側は
  「もう一度試す／手入力で登録する」画面へ遷移する

## コストの目安

1400px・quality 0.85 のJPEG 1枚あたり概ね 1,500〜2,500 入力トークン程度。
`effort: "low"` 指定で出力トークンを抑えています。無料版のAI読み取りを
月5回に制限しているのは、このAPIコストをPro課金で回収するためです。
