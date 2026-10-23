import type { ExtractedWarranty } from './types';

export type RootStackParamList = {
  Home: undefined;
  Detail: { id: string };
  AddPhoto: undefined;
  Form: {
    existingId?: string;
    extracted?: ExtractedWarranty;
    /** 撮影・選択した元写真（保存時にサムネイル化） */
    pendingPhoto?: { uri: string; width: number; height: number };
  };
  Settings: undefined;
  Paywall: undefined;
};
