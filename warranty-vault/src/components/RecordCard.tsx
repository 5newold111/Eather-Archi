import React from 'react';
import { Image, Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { computeStatus, telUrl } from '../lib/warranty';
import { categoryColors, radius, useTheme } from '../theme';
import type { ApplianceRecord } from '../types';
import { StatusBadge } from './StatusBadge';

interface Props {
  record: ApplianceRecord;
  onPress: () => void;
}

export function RecordCard({ record, onPress }: Props) {
  const theme = useTheme();
  const status = computeStatus(record.warrantyEndDate);
  const sub = [record.manufacturer, record.model].filter(Boolean).join(' / ');
  const catColor = categoryColors[record.categoryLabel];

  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: theme.card,
          opacity: pressed ? 0.85 : 1,
          shadowColor: '#000',
        },
      ]}
    >
      <View style={styles.row}>
        {record.photoUri ? (
          <Image source={{ uri: record.photoUri }} style={styles.thumb} />
        ) : (
          <View style={[styles.thumb, styles.thumbPlaceholder, { backgroundColor: theme.fill }]}>
            <Text style={styles.thumbEmoji}>🧾</Text>
          </View>
        )}
        <View style={styles.body}>
          <Text style={[styles.category, { color: catColor }]} numberOfLines={1}>
            {record.categoryLabel}
          </Text>
          <Text style={[styles.name, { color: theme.text }]} numberOfLines={2}>
            {record.name || '名称未設定'}
          </Text>
          {sub ? (
            <Text style={[styles.sub, { color: theme.secondaryText }]} numberOfLines={1}>
              {sub}
            </Text>
          ) : null}
          <View style={styles.footer}>
            <StatusBadge status={status} />
            {record.supportPhone ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="サポートに電話"
                onPress={() => Linking.openURL(telUrl(record.supportPhone!))}
                hitSlop={8}
              >
                <Text style={[styles.phone, { color: theme.accent }]}>📞 電話</Text>
              </Pressable>
            ) : null}
          </View>
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radius.card,
    padding: 14,
    marginBottom: 12,
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  row: { flexDirection: 'row', gap: 12 },
  thumb: { width: 72, height: 72, borderRadius: 12 },
  thumbPlaceholder: { alignItems: 'center', justifyContent: 'center' },
  thumbEmoji: { fontSize: 26 },
  body: { flex: 1 },
  category: { fontSize: 11, fontWeight: '700', letterSpacing: 0.3, marginBottom: 2 },
  name: { fontSize: 17, fontWeight: '600', marginBottom: 2 },
  sub: { fontSize: 13, marginBottom: 6 },
  footer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 2,
  },
  phone: { fontSize: 13, fontWeight: '600' },
});
