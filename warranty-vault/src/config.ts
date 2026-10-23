/**
 * アプリ全体の機能フラグ。
 *
 * PAYWALL_ENABLED:
 *   初回のApp Store公開は「無料版のみ」で行うため false にしている。
 *   false の間は登録台数・AI読み取り回数の制限や課金画面への導線がすべて無効になり、
 *   通知・CSVエクスポートも無料で使える（審査で「購入できない課金UI」と
 *   みなされるのを避けるため）。
 *
 *   Pro課金を追加するアップデートの際に、RevenueCat を接続してから true へ変更する。
 *   手順: docs/MONETIZATION.md の「RevenueCat 実装手順」参照。
 */
export const PAYWALL_ENABLED = false;
