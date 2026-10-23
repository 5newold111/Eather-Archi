import { setPro } from './entitlements';

/**
 * 課金処理の抽象化レイヤー。
 *
 * App Store公開時は RevenueCat (react-native-purchases) をここに接続する:
 *   1. `npx expo install react-native-purchases`
 *   2. App.tsx で `Purchases.configure({ apiKey })`
 *   3. purchasePro() → `Purchases.purchasePackage(...)` の結果で setPro() を呼ぶ
 *   4. restorePurchases() → `Purchases.restorePurchases()` の entitlements で判定
 * 詳細手順は docs/APP_STORE_RELEASE.md を参照。
 *
 * 以下は開発ビルド・実機検証用のスタブ実装（即時にProが有効になる）。
 * 本番ビルドに組み込む前に必ず差し替えること。
 */

export async function purchasePro(): Promise<boolean> {
  setPro(true);
  return true;
}

export async function restorePurchases(): Promise<boolean> {
  // スタブ: 現在のフラグをそのまま返す
  return true;
}
