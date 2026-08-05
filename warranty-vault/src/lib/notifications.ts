import * as Notifications from 'expo-notifications';
import type { ApplianceRecord } from '../types';
import { expiryNotificationsEnabled, isPro } from './entitlements';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

export async function requestNotificationPermission(): Promise<boolean> {
  const settings = await Notifications.getPermissionsAsync();
  if (settings.granted) return true;
  const result = await Notifications.requestPermissionsAsync();
  return result.granted;
}

/**
 * 保証期限の30日前・7日前にローカル通知を予約する（Pro機能）。
 * 登録内容が変わるたびに全て組み直すシンプルな方式。
 */
export async function syncExpiryNotifications(records: ApplianceRecord[]): Promise<void> {
  await Notifications.cancelAllScheduledNotificationsAsync();
  if (!isPro() || !expiryNotificationsEnabled()) return;

  const now = Date.now();
  for (const record of records) {
    if (!record.warrantyEndDate) continue;
    const end = new Date(record.warrantyEndDate + 'T09:00:00');
    if (isNaN(end.getTime())) continue;
    for (const daysBefore of [30, 7]) {
      const fireAt = new Date(end.getTime() - daysBefore * 86400000);
      if (fireAt.getTime() <= now) continue;
      await Notifications.scheduleNotificationAsync({
        content: {
          title: '保証期限が近づいています',
          body: `「${record.name}」の保証はあと${daysBefore}日で終了します（${record.warrantyEndDate}まで）`,
        },
        trigger: {
          type: Notifications.SchedulableTriggerInputTypes.DATE,
          date: fireAt,
        },
      });
    }
  }
}
