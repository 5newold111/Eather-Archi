import React, { useState } from 'react';
import {
  Alert,
  ScrollView,
  Share,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation';
import { radius, useTheme } from '../theme';
import { useRecords } from '../state/RecordsContext';
import {
  expiryNotificationsEnabled,
  hasProFeatures,
  isPro,
  paywallEnabled,
  setExpiryNotificationsEnabled,
} from '../lib/entitlements';
import {
  requestNotificationPermission,
  syncExpiryNotifications,
} from '../lib/notifications';
import { recordsToCsv } from '../lib/csv';
import { DestructiveButton, SecondaryButton } from '../components/Buttons';

type Props = NativeStackScreenProps<RootStackParamList, 'Settings'>;

export function SettingsScreen({ navigation }: Props) {
  const theme = useTheme();
  const { records, removeAll } = useRecords();
  const pro = isPro();
  const proFeatures = hasProFeatures();
  const [notify, setNotify] = useState(expiryNotificationsEnabled());

  const toggleNotify = async (value: boolean) => {
    if (!proFeatures) {
      navigation.navigate('Paywall');
      return;
    }
    if (value) {
      const granted = await requestNotificationPermission();
      if (!granted) {
        Alert.alert('通知が許可されていません', '設定アプリから通知を許可してください。');
        return;
      }
    }
    setNotify(value);
    setExpiryNotificationsEnabled(value);
    await syncExpiryNotifications(records);
  };

  const exportCsv = async () => {
    if (!proFeatures) {
      navigation.navigate('Paywall');
      return;
    }
    const csv = recordsToCsv(records);
    await Share.share({ message: csv, title: '保証書Vault エクスポート' });
  };

  const resetAll = () => {
    Alert.alert(
      'すべてのデータを削除します',
      '保存されているすべての家電情報と写真を削除します。この操作は取り消せません。本当によろしいですか？',
      [
        { text: 'キャンセル', style: 'cancel' },
        {
          text: 'すべて削除する',
          style: 'destructive',
          onPress: async () => {
            await removeAll();
            Alert.alert('すべてのデータを削除しました');
          },
        },
      ],
    );
  };

  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.content}
    >
      {paywallEnabled() && (
        <View style={[styles.card, { backgroundColor: theme.card }]}>
          <View style={styles.rowBetween}>
            <View style={styles.rowText}>
              <Text style={[styles.rowTitle, { color: theme.text }]}>
                プラン: {pro ? 'Pro' : '無料版'}
              </Text>
              <Text style={[styles.rowDesc, { color: theme.secondaryText }]}>
                {pro
                  ? 'すべての機能が利用できます。'
                  : '登録台数・AI読み取りに上限があります。'}
              </Text>
            </View>
            {!pro && (
              <SecondaryButton title="Pro版を見る" onPress={() => navigation.navigate('Paywall')} />
            )}
          </View>
        </View>
      )}

      <View style={[styles.card, { backgroundColor: theme.card }]}>
        <View style={styles.rowBetween}>
          <View style={styles.rowText}>
            <Text style={[styles.rowTitle, { color: theme.text }]}>
              保証期限のお知らせ {proFeatures ? '' : '（Pro）'}
            </Text>
            <Text style={[styles.rowDesc, { color: theme.secondaryText }]}>
              期限の30日前・7日前に通知します。
            </Text>
          </View>
          <Switch
            value={proFeatures && notify}
            onValueChange={toggleNotify}
            trackColor={{ true: theme.accent }}
          />
        </View>
      </View>

      <View style={[styles.card, { backgroundColor: theme.card }]}>
        <View style={styles.rowBetween}>
          <View style={styles.rowText}>
            <Text style={[styles.rowTitle, { color: theme.text }]}>
              CSVエクスポート {proFeatures ? '' : '（Pro）'}
            </Text>
            <Text style={[styles.rowDesc, { color: theme.secondaryText }]}>
              登録内容をCSVで書き出して共有できます。
            </Text>
          </View>
          <SecondaryButton title="書き出す" onPress={exportCsv} />
        </View>
      </View>

      <DestructiveButton
        title="すべてのデータを削除する"
        onPress={resetAll}
        style={styles.reset}
      />

      <Text style={[styles.footer, { color: theme.tertiaryText }]}>
        保証書Vault v1.0.0{'\n'}
        データはすべて端末内にのみ保存されます。
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: 20, paddingBottom: 60 },
  card: {
    borderRadius: radius.card,
    padding: 16,
    marginBottom: 12,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  rowText: { flex: 1 },
  rowTitle: { fontSize: 16, fontWeight: '700', marginBottom: 2 },
  rowDesc: { fontSize: 13, lineHeight: 19 },
  reset: { marginTop: 16 },
  footer: { fontSize: 12, lineHeight: 18, textAlign: 'center', marginTop: 28 },
});
