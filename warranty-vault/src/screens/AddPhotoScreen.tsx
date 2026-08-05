import React, { useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation';
import { radius, useTheme } from '../theme';
import { DestructiveButton, PrimaryButton, SecondaryButton } from '../components/Buttons';
import { prepareForAnalysis } from '../lib/images';
import { extractWarranty } from '../lib/api';
import { tryComputeEndDate } from '../lib/warranty';
import {
  canUseAiScan,
  recordAiScan,
  remainingAiScans,
} from '../lib/entitlements';

type Props = NativeStackScreenProps<RootStackParamList, 'AddPhoto'>;

type Phase =
  | { kind: 'idle' }
  | { kind: 'analyzing'; message: string }
  | { kind: 'error'; photo: { uri: string; width: number; height: number } };

export function AddPhotoScreen({ navigation }: Props) {
  const theme = useTheme();
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });

  const pickImage = async (fromCamera: boolean) => {
    const options: ImagePicker.ImagePickerOptions = {
      mediaTypes: 'images',
      quality: 1,
    };
    const result = fromCamera
      ? await ImagePicker.launchCameraAsync(options)
      : await ImagePicker.launchImageLibraryAsync(options);
    if (result.canceled || result.assets.length === 0) return;
    const asset = result.assets[0];
    await analyze({ uri: asset.uri, width: asset.width, height: asset.height });
  };

  const analyze = async (photo: { uri: string; width: number; height: number }) => {
    if (!canUseAiScan()) {
      Alert.alert(
        '今月のAI読み取り回数を使い切りました',
        'Pro版では回数無制限で読み取れます。写真なしの手入力はいつでも利用できます。',
        [
          { text: '手入力で登録する', onPress: () => navigation.replace('Form', { pendingPhoto: photo }) },
          { text: 'Pro版を見る', onPress: () => navigation.navigate('Paywall') },
          { text: 'キャンセル', style: 'cancel' },
        ],
      );
      return;
    }
    try {
      setPhase({ kind: 'analyzing', message: '写真を読み取り用に整えています...' });
      const prepared = await prepareForAnalysis(photo.uri, photo.width, photo.height);
      setPhase({ kind: 'analyzing', message: 'AIが写真を読み取っています...' });
      const extracted = await extractWarranty(prepared.base64, prepared.mediaType);
      recordAiScan();
      if (!extracted.warrantyEndDate && extracted.purchaseDate && extracted.warrantyPeriodText) {
        extracted.warrantyEndDate = tryComputeEndDate(
          extracted.purchaseDate,
          extracted.warrantyPeriodText,
        );
      }
      setPhase({ kind: 'analyzing', message: '確認画面を準備しています...' });
      navigation.replace('Form', { extracted, pendingPhoto: photo });
    } catch {
      setPhase({ kind: 'error', photo });
    }
  };

  const remaining = remainingAiScans();

  return (
    <SafeAreaView style={[styles.safe, { backgroundColor: theme.background }]}>
      {phase.kind === 'analyzing' ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={theme.accent} />
          <Text style={[styles.statusText, { color: theme.secondaryText }]}>
            {phase.message}
          </Text>
        </View>
      ) : phase.kind === 'error' ? (
        <View style={styles.center}>
          <Text style={[styles.title, { color: theme.text }]}>読み取りに失敗しました</Text>
          <Text style={[styles.description, { color: theme.secondaryText }]}>
            写真からの自動読み取りがうまくいきませんでした。{'\n'}
            もう一度お試しいただくか、手入力で登録できます。
          </Text>
          <View style={styles.buttons}>
            <SecondaryButton title="もう一度試す" onPress={() => analyze(phase.photo)} />
            <PrimaryButton
              title="手入力で登録する"
              onPress={() => navigation.replace('Form', { pendingPhoto: phase.photo })}
            />
          </View>
        </View>
      ) : (
        <View style={styles.content}>
          <View
            style={[
              styles.dropzone,
              { borderColor: theme.separator, backgroundColor: theme.card },
            ]}
          >
            <Text style={styles.icon}>📷</Text>
            <Text style={[styles.description, { color: theme.secondaryText }]}>
              保証書やレシートの写真を選ぶと、{'\n'}内容を自動で読み取ります。
            </Text>
            {remaining !== null && (
              <Text style={[styles.remaining, { color: theme.tertiaryText }]}>
                今月のAI読み取り: あと{remaining}回（無料版）
              </Text>
            )}
            <View style={styles.buttons}>
              <PrimaryButton title="写真を撮る" onPress={() => pickImage(true)} />
              <SecondaryButton title="ライブラリから選ぶ" onPress={() => pickImage(false)} />
            </View>
          </View>
          <View style={styles.manualWrap}>
            <DestructiveButton
              title="写真を使わずに手入力する"
              onPress={() => navigation.replace('Form', {})}
              style={styles.manualBtn}
            />
          </View>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1 },
  content: { flex: 1, padding: 20 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  dropzone: {
    borderWidth: 2,
    borderStyle: 'dashed',
    borderRadius: radius.card,
    padding: 28,
    alignItems: 'center',
    gap: 12,
  },
  icon: { fontSize: 40 },
  title: { fontSize: 22, fontWeight: '700', marginBottom: 10 },
  description: { fontSize: 15, textAlign: 'center', lineHeight: 22 },
  remaining: { fontSize: 12 },
  statusText: { marginTop: 16, fontSize: 15 },
  buttons: { gap: 10, marginTop: 12, alignSelf: 'stretch' },
  manualWrap: { marginTop: 24 },
  manualBtn: { borderWidth: 0 },
});
