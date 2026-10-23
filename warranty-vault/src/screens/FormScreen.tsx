import React, { useEffect, useState } from 'react';
import {
  Alert,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation';
import { CATEGORY_LABELS, type ApplianceRecord, type CategoryLabel } from '../types';
import { useRecords } from '../state/RecordsContext';
import { radius, categoryColors, useTheme } from '../theme';
import { FormField } from '../components/FormField';
import { DestructiveButton, PrimaryButton } from '../components/Buttons';
import { saveThumbnail } from '../lib/images';
import { tryComputeEndDate } from '../lib/warranty';

type Props = NativeStackScreenProps<RootStackParamList, 'Form'>;

function uid(): string {
  return 'a_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
}

function normalizeCategory(value: string | null | undefined): CategoryLabel {
  return (CATEGORY_LABELS as readonly string[]).includes(value ?? '')
    ? (value as CategoryLabel)
    : 'その他';
}

export function FormScreen({ navigation, route }: Props) {
  const theme = useTheme();
  const { records, save, remove } = useRecords();
  const { existingId, extracted, pendingPhoto } = route.params ?? {};
  const existing = existingId ? records.find((r) => r.id === existingId) : undefined;

  const [name, setName] = useState(existing?.name ?? extracted?.name ?? '');
  const [manufacturer, setManufacturer] = useState(
    existing?.manufacturer ?? extracted?.manufacturer ?? '',
  );
  const [model, setModel] = useState(existing?.model ?? extracted?.model ?? '');
  const [category, setCategory] = useState<CategoryLabel>(
    normalizeCategory(existing?.categoryLabel ?? extracted?.category),
  );
  const [purchaseDate, setPurchaseDate] = useState(
    existing?.purchaseDate ?? extracted?.purchaseDate ?? '',
  );
  const [warrantyPeriodText, setWarrantyPeriodText] = useState(
    existing?.warrantyPeriodText ?? extracted?.warrantyPeriodText ?? '',
  );
  const [warrantyEndDate, setWarrantyEndDate] = useState(
    existing?.warrantyEndDate ?? extracted?.warrantyEndDate ?? '',
  );
  const [retailer, setRetailer] = useState(existing?.retailer ?? extracted?.retailer ?? '');
  const [supportPhone, setSupportPhone] = useState(
    existing?.supportPhone ?? extracted?.supportPhone ?? '',
  );
  const [serialNumber, setSerialNumber] = useState(
    existing?.serialNumber ?? extracted?.serialNumber ?? '',
  );
  const [memo, setMemo] = useState(existing?.memo ?? extracted?.memo ?? '');
  const [photo, setPhoto] = useState<
    { uri: string; width: number; height: number } | undefined
  >(pendingPhoto);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    navigation.setOptions({ title: existingId ? '内容を編集する' : '内容を確認する' });
  }, [navigation, existingId]);

  // 保証終了日が空のまま購入日と期間が揃ったら自動推定
  useEffect(() => {
    if (!warrantyEndDate && purchaseDate && warrantyPeriodText) {
      const computed = tryComputeEndDate(purchaseDate, warrantyPeriodText);
      if (computed) setWarrantyEndDate(computed);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [purchaseDate, warrantyPeriodText]);

  const changePhoto = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: 'images',
      quality: 1,
    });
    if (result.canceled || result.assets.length === 0) return;
    const asset = result.assets[0];
    setPhoto({ uri: asset.uri, width: asset.width, height: asset.height });
  };

  const handleSave = async () => {
    if (!name.trim()) {
      Alert.alert('家電の名称を入力してください');
      return;
    }
    setSaving(true);
    try {
      let photoUri = existing?.photoUri ?? null;
      if (photo) {
        photoUri = await saveThumbnail(photo.uri, photo.width, photo.height);
      }
      const now = Date.now();
      const record: ApplianceRecord = {
        id: existing?.id ?? uid(),
        name: name.trim(),
        manufacturer: manufacturer.trim() || null,
        model: model.trim() || null,
        categoryLabel: category,
        purchaseDate: purchaseDate.trim() || null,
        warrantyPeriodText: warrantyPeriodText.trim() || null,
        warrantyEndDate: warrantyEndDate.trim() || null,
        retailer: retailer.trim() || null,
        supportPhone: supportPhone.trim() || null,
        serialNumber: serialNumber.trim() || null,
        memo: memo.trim() || null,
        photoUri,
        createdAt: existing?.createdAt ?? now,
        updatedAt: now,
      };
      await save(record);
      navigation.popToTop();
    } catch {
      Alert.alert('保存に失敗しました。もう一度お試しください。');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = () => {
    if (!existing) return;
    Alert.alert('この家電の情報を削除します', 'よろしいですか？', [
      { text: 'キャンセル', style: 'cancel' },
      {
        text: '削除する',
        style: 'destructive',
        onPress: async () => {
          await remove(existing.id);
          navigation.popToTop();
        },
      },
    ]);
  };

  const previewUri = photo?.uri ?? existing?.photoUri ?? null;

  return (
    <KeyboardAvoidingView
      style={[styles.flex, { backgroundColor: theme.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {previewUri ? (
          <Image source={{ uri: previewUri }} style={styles.photo} resizeMode="cover" />
        ) : null}
        <Pressable onPress={changePhoto} hitSlop={8}>
          <Text style={[styles.changePhoto, { color: theme.accent }]}>
            {previewUri ? '写真を変更する' : '写真を追加する（あとからでも可）'}
          </Text>
        </Pressable>

        <FormField label="家電の名称（必須）" value={name} onChangeText={setName} />

        <Text style={[styles.label, { color: theme.secondaryText }]}>カテゴリ</Text>
        <View style={styles.categoryWrap}>
          {CATEGORY_LABELS.map((label) => {
            const selected = label === category;
            return (
              <Pressable
                key={label}
                accessibilityRole="button"
                onPress={() => setCategory(label)}
                style={[
                  styles.categoryChip,
                  {
                    backgroundColor: selected ? categoryColors[label] : theme.card,
                    borderColor: selected ? categoryColors[label] : theme.separator,
                  },
                ]}
              >
                <Text
                  style={[
                    styles.categoryChipText,
                    { color: selected ? '#fff' : theme.text },
                  ]}
                >
                  {label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        <FormField label="メーカー" value={manufacturer} onChangeText={setManufacturer} />
        <FormField label="型番" value={model} onChangeText={setModel} />
        <FormField label="購入店舗" value={retailer} onChangeText={setRetailer} />
        <FormField
          label="購入日"
          value={purchaseDate}
          onChangeText={setPurchaseDate}
          placeholder="例: 2025-04-01"
        />
        <FormField
          label="保証期間（メモ）"
          value={warrantyPeriodText}
          onChangeText={setWarrantyPeriodText}
          placeholder="例: 1年間"
        />
        <FormField
          label="保証終了日"
          value={warrantyEndDate}
          onChangeText={setWarrantyEndDate}
          placeholder="例: 2026-04-01"
        />
        <FormField
          label="サポート電話番号"
          value={supportPhone}
          onChangeText={setSupportPhone}
          keyboardType="phone-pad"
        />
        <FormField
          label="保証書番号・製造番号"
          value={serialNumber}
          onChangeText={setSerialNumber}
        />
        <FormField label="メモ" value={memo} onChangeText={setMemo} multiline />

        <PrimaryButton
          title={saving ? '保存しています...' : '保存する'}
          onPress={handleSave}
          disabled={saving}
          style={styles.saveBtn}
        />
        {existing ? (
          <DestructiveButton title="削除する" onPress={handleDelete} style={styles.deleteBtn} />
        ) : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: { padding: 20, paddingBottom: 60 },
  photo: { width: '100%', height: 200, borderRadius: radius.card, marginBottom: 8 },
  changePhoto: { fontSize: 14, fontWeight: '600', marginBottom: 18 },
  label: { fontSize: 13, fontWeight: '600', marginBottom: 6 },
  categoryWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 16 },
  categoryChip: {
    borderRadius: radius.pill,
    borderWidth: 1,
    paddingVertical: 7,
    paddingHorizontal: 12,
  },
  categoryChipText: { fontSize: 13, fontWeight: '600' },
  saveBtn: { marginTop: 8 },
  deleteBtn: { marginTop: 12 },
});
