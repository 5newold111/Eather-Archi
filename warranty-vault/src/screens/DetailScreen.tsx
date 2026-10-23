import React from 'react';
import {
  Image,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation';
import { useRecords } from '../state/RecordsContext';
import { radius, categoryColors, useTheme } from '../theme';
import { StatusBadge } from '../components/StatusBadge';
import { PrimaryButton, SecondaryButton } from '../components/Buttons';
import { computeStatus, formatDateJa, telUrl } from '../lib/warranty';

type Props = NativeStackScreenProps<RootStackParamList, 'Detail'>;

export function DetailScreen({ navigation, route }: Props) {
  const theme = useTheme();
  const { records } = useRecords();
  const record = records.find((r) => r.id === route.params.id);

  if (!record) {
    return (
      <View style={[styles.missing, { backgroundColor: theme.background }]}>
        <Text style={{ color: theme.secondaryText }}>この記録は削除されました。</Text>
      </View>
    );
  }

  const status = computeStatus(record.warrantyEndDate);
  const rows: Array<[string, string]> = [
    ['カテゴリ', record.categoryLabel],
    ['メーカー', record.manufacturer ?? '―'],
    ['型番', record.model ?? '―'],
    ['購入店舗', record.retailer ?? '―'],
    ['購入日', formatDateJa(record.purchaseDate)],
    ['保証期間', record.warrantyPeriodText ?? '―'],
    ['保証終了日', formatDateJa(record.warrantyEndDate)],
    ['保証書番号', record.serialNumber ?? '―'],
    ['メモ', record.memo ?? '―'],
  ];

  return (
    <ScrollView
      style={{ backgroundColor: theme.background }}
      contentContainerStyle={styles.content}
    >
      {record.photoUri ? (
        <Image source={{ uri: record.photoUri }} style={styles.photo} resizeMode="cover" />
      ) : null}

      <Text style={[styles.category, { color: categoryColors[record.categoryLabel] }]}>
        {record.categoryLabel}
      </Text>
      <Text style={[styles.name, { color: theme.text }]}>{record.name}</Text>
      <View style={styles.badgeRow}>
        <StatusBadge status={status} />
      </View>

      <View style={[styles.card, { backgroundColor: theme.card }]}>
        {rows.map(([label, value], i) => (
          <View
            key={label}
            style={[
              styles.row,
              i < rows.length - 1 && {
                borderBottomWidth: StyleSheet.hairlineWidth,
                borderBottomColor: theme.separator,
              },
            ]}
          >
            <Text style={[styles.rowLabel, { color: theme.secondaryText }]}>{label}</Text>
            <Text style={[styles.rowValue, { color: theme.text }]}>{value}</Text>
          </View>
        ))}
        <View style={styles.row}>
          <Text style={[styles.rowLabel, { color: theme.secondaryText }]}>サポート電話</Text>
          {record.supportPhone ? (
            <Text
              style={[styles.rowValue, styles.phoneLink, { color: theme.accent }]}
              onPress={() => Linking.openURL(telUrl(record.supportPhone!))}
            >
              {record.supportPhone}
            </Text>
          ) : (
            <Text style={[styles.rowValue, { color: theme.text }]}>―</Text>
          )}
        </View>
      </View>

      <View style={styles.actions}>
        {record.supportPhone ? (
          <PrimaryButton
            title="電話をかける"
            onPress={() => Linking.openURL(telUrl(record.supportPhone!))}
          />
        ) : null}
        <SecondaryButton
          title="編集する"
          onPress={() => navigation.navigate('Form', { existingId: record.id })}
        />
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: 20, paddingBottom: 60 },
  missing: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  photo: { width: '100%', height: 220, borderRadius: radius.card, marginBottom: 16 },
  category: { fontSize: 12, fontWeight: '700', letterSpacing: 0.3, marginBottom: 4 },
  name: { fontSize: 26, fontWeight: '700', marginBottom: 10 },
  badgeRow: { marginBottom: 16 },
  card: { borderRadius: radius.card, paddingHorizontal: 16 },
  row: {
    flexDirection: 'row',
    paddingVertical: 13,
    gap: 12,
  },
  rowLabel: { width: 96, fontSize: 13, fontWeight: '600' },
  rowValue: { flex: 1, fontSize: 15 },
  phoneLink: { fontWeight: '600' },
  actions: { marginTop: 20, gap: 10 },
});
