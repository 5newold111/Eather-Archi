import React, { useState } from 'react';
import { Alert, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation';
import { radius, useTheme } from '../theme';
import { PrimaryButton, SecondaryButton } from '../components/Buttons';
import { purchasePro, restorePurchases } from '../lib/purchases';
import { FREE_AI_SCANS_PER_MONTH, FREE_MAX_ITEMS } from '../lib/entitlements';

type Props = NativeStackScreenProps<RootStackParamList, 'Paywall'>;

const FEATURES: Array<{ icon: string; title: string; description: string }> = [
  {
    icon: '∞',
    title: '登録台数 無制限',
    description: `無料版の${FREE_MAX_ITEMS}台の上限がなくなり、家中の家電をすべて記録できます。`,
  },
  {
    icon: '✨',
    title: 'AI読み取り 無制限',
    description: `無料版は月${FREE_AI_SCANS_PER_MONTH}回まで。Proなら何枚でも写真から自動読み取りできます。`,
  },
  {
    icon: '🔔',
    title: '保証期限のお知らせ',
    description: '期限の30日前・7日前に通知。修理のチャンスを逃しません。',
  },
  {
    icon: '📤',
    title: 'CSVエクスポート',
    description: '引っ越し・保険請求・買い替え検討に。一覧をCSVで書き出せます。',
  },
];

export function PaywallScreen({ navigation }: Props) {
  const theme = useTheme();
  const [busy, setBusy] = useState(false);

  const buy = async () => {
    setBusy(true);
    try {
      const ok = await purchasePro();
      if (ok) {
        Alert.alert('Pro版へようこそ！', 'すべての機能が使えるようになりました。');
        navigation.goBack();
      }
    } catch {
      Alert.alert('購入を完了できませんでした', 'しばらくしてからもう一度お試しください。');
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    setBusy(true);
    try {
      const ok = await restorePurchases();
      Alert.alert(ok ? '購入を復元しました' : '復元できる購入はありませんでした');
      if (ok) navigation.goBack();
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.content}
    >
      <Text style={[styles.title, { color: theme.text }]}>保証書Vault Pro</Text>
      <Text style={[styles.subtitle, { color: theme.secondaryText }]}>
        家中の家電を、まるごと安心に。
      </Text>

      <View style={styles.features}>
        {FEATURES.map((f) => (
          <View key={f.title} style={[styles.feature, { backgroundColor: theme.card }]}>
            <Text style={styles.featureIcon}>{f.icon}</Text>
            <View style={styles.featureBody}>
              <Text style={[styles.featureTitle, { color: theme.text }]}>{f.title}</Text>
              <Text style={[styles.featureDesc, { color: theme.secondaryText }]}>
                {f.description}
              </Text>
            </View>
          </View>
        ))}
      </View>

      <View style={[styles.priceCard, { backgroundColor: theme.card }]}>
        <Text style={[styles.price, { color: theme.text }]}>月額 ¥300</Text>
        <Text style={[styles.priceNote, { color: theme.tertiaryText }]}>
          年額プラン ¥2,800（約22%お得）も選べます
        </Text>
      </View>

      <PrimaryButton title={busy ? '処理中...' : 'Pro版をはじめる'} onPress={buy} disabled={busy} />
      <SecondaryButton
        title="購入を復元する"
        onPress={restore}
        disabled={busy}
        style={styles.restore}
      />
      <Text style={[styles.legal, { color: theme.tertiaryText }]}>
        サブスクリプションは自動更新されます。App Storeのアカウント設定からいつでも解約できます。
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: 24, paddingBottom: 60 },
  title: { fontSize: 30, fontWeight: '700', textAlign: 'center' },
  subtitle: { fontSize: 15, textAlign: 'center', marginTop: 4, marginBottom: 24 },
  features: { gap: 10, marginBottom: 20 },
  feature: {
    flexDirection: 'row',
    gap: 14,
    padding: 16,
    borderRadius: radius.card,
    alignItems: 'center',
  },
  featureIcon: { fontSize: 26, width: 34, textAlign: 'center' },
  featureBody: { flex: 1 },
  featureTitle: { fontSize: 16, fontWeight: '700', marginBottom: 2 },
  featureDesc: { fontSize: 13, lineHeight: 19 },
  priceCard: {
    borderRadius: radius.card,
    padding: 18,
    alignItems: 'center',
    marginBottom: 16,
  },
  price: { fontSize: 24, fontWeight: '700' },
  priceNote: { fontSize: 12, marginTop: 4 },
  restore: { marginTop: 10 },
  legal: { fontSize: 11, lineHeight: 17, textAlign: 'center', marginTop: 16 },
});
