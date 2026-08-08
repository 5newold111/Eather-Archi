import Storage from 'expo-sqlite/kv-store';
import { PAYWALL_ENABLED } from '../config';

/**
 * 無料版 / Pro版のエンタイトルメント管理。
 *
 * 実際の課金は App Store の自動更新サブスクリプション（月額）を想定し、
 * RevenueCat (react-native-purchases) を接続する（docs/MONETIZATION.md 参照）。
 * このモジュールは判定ロジックの単一窓口で、課金SDK差し替え時も
 * ここ以外に影響が出ないようにしている。
 */

export const FREE_MAX_ITEMS = 15;
export const FREE_AI_SCANS_PER_MONTH = 5;

const PRO_KEY = 'entitlement:pro';
const NOTIFY_KEY = 'setting:expiryNotifications';

function monthKey(): string {
  const now = new Date();
  return `usage:aiScans:${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

export function isPro(): boolean {
  return Storage.getItemSync(PRO_KEY) === '1';
}

/**
 * Pro向け機能（通知・CSV等）が使えるか。
 * 課金導線がオフの間（初回リリース）は全員が使える。
 */
export function hasProFeatures(): boolean {
  return !PAYWALL_ENABLED || isPro();
}

/** 制限表示・ペイウォール導線を出すべきか */
export function paywallEnabled(): boolean {
  return PAYWALL_ENABLED;
}

/** 開発ビルド・審査前の動作確認用。本番では課金SDKの購入結果で設定する。 */
export function setPro(value: boolean): void {
  Storage.setItemSync(PRO_KEY, value ? '1' : '0');
}

export function aiScansUsedThisMonth(): number {
  const v = Storage.getItemSync(monthKey());
  return v ? parseInt(v, 10) || 0 : 0;
}

export function recordAiScan(): void {
  Storage.setItemSync(monthKey(), String(aiScansUsedThisMonth() + 1));
}

export function canUseAiScan(): boolean {
  if (!PAYWALL_ENABLED) return true;
  return isPro() || aiScansUsedThisMonth() < FREE_AI_SCANS_PER_MONTH;
}

export function remainingAiScans(): number | null {
  if (!PAYWALL_ENABLED || isPro()) return null; // 無制限
  return Math.max(0, FREE_AI_SCANS_PER_MONTH - aiScansUsedThisMonth());
}

export function canAddItem(currentCount: number): boolean {
  if (!PAYWALL_ENABLED) return true;
  return isPro() || currentCount < FREE_MAX_ITEMS;
}

export function expiryNotificationsEnabled(): boolean {
  return Storage.getItemSync(NOTIFY_KEY) === '1';
}

export function setExpiryNotificationsEnabled(value: boolean): void {
  Storage.setItemSync(NOTIFY_KEY, value ? '1' : '0');
}
