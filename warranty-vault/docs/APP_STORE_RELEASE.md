# App Store 公開手順チェックリスト

保証書Vault を App Store で公開するまでの手順です。上から順に進めてください。

## 1. アカウント準備

- [ ] Apple Developer Program に登録（年間 ¥14,900 前後 / 個人でOK）
      https://developer.apple.com/jp/programs/
- [ ] App Store Connect にログインできることを確認
- [ ] Expo アカウントを作成し `npx expo login`

## 2. プロキシサーバーのデプロイ（先に済ませる）

- [ ] `server/README.md` の手順で Vercel にデプロイ
- [ ] `ANTHROPIC_API_KEY` を Vercel の環境変数に設定
- [ ] `eas.json` の `preview` / `production` の
      `EXPO_PUBLIC_EXTRACT_API_URL` を実際のURLに書き換え

## 3. アプリ側の設定

- [ ] `app.json` の `ios.bundleIdentifier`（現在 `com.eatherarch.warrantyvault`）を確認・変更
- [ ] アプリアイコン（`assets/icon.png` 1024x1024）を差し替え
- [ ] `npx eas init` を実行し `extra.eas.projectId` を自動設定
- [ ] 表示名を変えたい場合は `app.json` の `name` を変更（現在「保証書Vault」）

## 4. 課金（Pro版）の設定

- [ ] App Store Connect →「App内課金」で自動更新サブスクリプションを作成
      - 例: `pro_monthly` ¥300/月、`pro_yearly` ¥2,800/年
- [ ] RevenueCat のアカウントを作成しプロジェクトを設定
- [ ] `npx expo install react-native-purchases`
- [ ] `src/lib/purchases.ts` のスタブを RevenueCat 実装に差し替え
      （ファイル内コメントに手順あり）
- [ ] サンドボックステスターで購入・復元をテスト

※ まず無料版のみで公開し、課金は後のアップデートで追加する進め方も可能です。
その場合ペイウォール画面への導線を一時的に隠してください（審査で「購入できない
課金UI」があるとリジェクトされます）。

## 5. ビルドと実機確認

```bash
# 開発ビルド（実機で動作確認）
npx eas build --profile development --platform ios
npx expo start --dev-client

# 審査提出用ビルド
npx eas build --profile production --platform ios
```

- [ ] 実機で追加フロー（撮影→AI読み取り→保存）を一通り確認
- [ ] 機内モードで手入力登録が動くことを確認（オフライン動作）
- [ ] ダークモードで表示崩れがないか確認

## 6. App Store Connect の入力

- [ ] アプリ名: 「保証書Vault - 家電の保証書・レシート保管庫」など
- [ ] カテゴリ: ユーティリティ または ライフスタイル
- [ ] プライバシーポリシーURL: `docs/PRIVACY_POLICY.md` をWebで公開したURL
      （会社サイト Eather-Arch のページとして置くのが簡単）
- [ ] App プライバシー（データ収集の申告）:
      - 「データを収集しない」…写真はAI読み取りのため一時的にサーバーへ送信されるが
        保存・追跡はしない。「ユーザーに紐づかないデータ」として写真の申告が必要か
        最新のガイドラインを確認すること
- [ ] スクリーンショット（6.7インチ / 6.5インチ / 5.5インチ）
- [ ] 審査メモ: AI読み取りのテスト用に保証書のサンプル画像を添付すると親切

## 7. 提出

```bash
npx eas submit --platform ios
```

- [ ] 審査に提出（初回は通常1〜3営業日）
- [ ] リジェクト時は理由に応じて修正して再提出

## よくあるリジェクト理由と対策（このアプリの場合）

| リスク | 対策（実装済み） |
|---|---|
| 課金UIがあるのに購入できない | RevenueCat接続前はペイウォール導線を隠す |
| カメラ・写真の権限文言が英語 | `app.json` で日本語の使用目的を設定済み |
| プライバシーポリシーなし | `docs/PRIVACY_POLICY.md` を公開してURLを登録 |
| 最小機能（4.2 Minimum Functionality） | AI読み取り・通知・検索など十分な機能あり |
