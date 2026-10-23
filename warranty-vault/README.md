# 保証書Vault — 家電の保証書・レシート保管庫

家電の保証書・レシートを写真で記録し、故障時に「型番は？」「保証はいつまで？」
「サポートの電話番号は？」を数秒で引き出せる iOS アプリです。
Appleのサイトを参考にしたデザイン（白基調・#0071e3・ダークモード対応）で、
App Store公開と無料版＋Pro月額課金のフリーミアム構造を前提に実装しています。

## 構成

```
warranty-vault/
├── App.tsx                # ナビゲーション・プロバイダー
├── src/
│   ├── screens/           # 一覧 / 写真追加(AI解析) / フォーム / 詳細 / 設定 / ペイウォール
│   ├── components/        # カード・バッジ・ボタン・フォーム部品
│   ├── lib/
│   │   ├── db.ts          # expo-sqlite CRUD
│   │   ├── images.ts      # サムネイル保存・AI解析用リサイズ
│   │   ├── api.ts         # AI読み取りプロキシ呼び出し
│   │   ├── warranty.ts    # 保証ステータス・期限推定ロジック
│   │   ├── entitlements.ts# 無料版/Pro判定（15台・AI月5回）
│   │   ├── purchases.ts   # 課金抽象化（RevenueCat接続ポイント）
│   │   ├── notifications.ts # 保証期限通知（Pro）
│   │   └── csv.ts         # CSVエクスポート（Pro）
│   └── theme.ts           # Apple風デザイントークン（ライト/ダーク）
├── server/                # AI読み取りプロキシ（Vercel Serverless Function）
└── docs/
    ├── APP_STORE_RELEASE.md  # App Store公開チェックリスト
    ├── MONETIZATION.md       # 課金設計・Pro機能提案
    └── PRIVACY_POLICY.md     # プライバシーポリシー雛形
```

## 開発の始め方

```bash
cd warranty-vault
npm install
npx expo start        # Expo Go で動作確認（課金・通知以外）
```

AI読み取りを使うには `server/` を Vercel にデプロイし（`server/README.md`）、
エンドポイントURLを環境変数 `EXPO_PUBLIC_EXTRACT_API_URL` に設定してください。

```bash
EXPO_PUBLIC_EXTRACT_API_URL=https://<project>.vercel.app/api/extract-warranty npx expo start
```

## 型チェック

```bash
npx tsc --noEmit          # アプリ本体
cd server && npx tsc --noEmit  # プロキシ
```

## App Store 公開

`docs/APP_STORE_RELEASE.md` のチェックリストに沿って進めてください。
課金設計とPro機能の詳細は `docs/MONETIZATION.md` を参照。
