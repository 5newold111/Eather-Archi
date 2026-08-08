import React, { useMemo, useState } from 'react';
import {
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation';
import { useRecords } from '../state/RecordsContext';
import { radius, useTheme } from '../theme';
import { RecordCard } from '../components/RecordCard';
import { PrimaryButton } from '../components/Buttons';
import { canAddItem, FREE_MAX_ITEMS, isPro, paywallEnabled } from '../lib/entitlements';
import type { ApplianceRecord } from '../types';

type Props = NativeStackScreenProps<RootStackParamList, 'Home'>;

function matches(record: ApplianceRecord, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return [
    record.name,
    record.manufacturer,
    record.model,
    record.retailer,
    record.memo,
    record.serialNumber,
    record.categoryLabel,
  ].some((v) => (v ?? '').toLowerCase().includes(q));
}

export function HomeScreen({ navigation }: Props) {
  const theme = useTheme();
  const { records, loading } = useRecords();
  const [query, setQuery] = useState('');

  const filtered = useMemo(
    () => records.filter((r) => matches(r, query.trim())),
    [records, query],
  );

  const handleAdd = () => {
    if (!canAddItem(records.length)) {
      navigation.navigate('Paywall');
      return;
    }
    navigation.navigate('AddPhoto');
  };

  return (
    <SafeAreaView style={[styles.safe, { backgroundColor: theme.background }]} edges={['top']}>
      <View style={styles.header}>
        <View style={styles.headerText}>
          <Text style={[styles.title, { color: theme.text }]}>保証書Vault</Text>
          <Text style={[styles.subtitle, { color: theme.secondaryText }]}>
            写真を撮るだけで、保証情報を記録・検索。
          </Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="設定"
          onPress={() => navigation.navigate('Settings')}
          hitSlop={8}
        >
          <Text style={styles.gear}>⚙️</Text>
        </Pressable>
      </View>

      <TextInput
        value={query}
        onChangeText={setQuery}
        placeholder="名前・メーカー・型番・店舗などで検索"
        placeholderTextColor={theme.tertiaryText}
        clearButtonMode="while-editing"
        style={[
          styles.search,
          { backgroundColor: theme.card, color: theme.text, borderColor: theme.separator },
        ]}
      />

      {paywallEnabled() && !isPro() && (
        <Text style={[styles.limitNote, { color: theme.tertiaryText }]}>
          無料版: {records.length} / {FREE_MAX_ITEMS} 台登録済み
        </Text>
      )}

      <FlatList
        data={filtered}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => (
          <RecordCard record={item} onPress={() => navigation.navigate('Detail', { id: item.id })} />
        )}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyIcon}>🗂️</Text>
            <Text style={[styles.emptyText, { color: theme.secondaryText }]}>
              {loading
                ? '読み込んでいます...'
                : records.length === 0
                  ? 'まだ何も登録されていません。\n保証書や家電の写真を追加してみましょう。'
                  : '該当する家電が見つかりませんでした。'}
            </Text>
          </View>
        }
      />

      <View style={styles.fabWrap}>
        <PrimaryButton title="＋ 家電を追加する" onPress={handleAdd} style={styles.fab} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 8,
  },
  headerText: { flex: 1 },
  title: { fontSize: 32, fontWeight: '700', letterSpacing: 0.2 },
  subtitle: { fontSize: 14, marginTop: 2 },
  gear: { fontSize: 24, marginTop: 6 },
  search: {
    marginHorizontal: 20,
    marginTop: 8,
    marginBottom: 6,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.pill,
    paddingHorizontal: 18,
    paddingVertical: 11,
    fontSize: 15,
  },
  limitNote: { marginHorizontal: 24, marginBottom: 4, fontSize: 12 },
  list: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 120 },
  empty: { alignItems: 'center', paddingVertical: 64 },
  emptyIcon: { fontSize: 40, marginBottom: 10 },
  emptyText: { fontSize: 15, textAlign: 'center', lineHeight: 22 },
  fabWrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 28,
    alignItems: 'center',
  },
  fab: {
    paddingHorizontal: 30,
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
});
