export const CATEGORY_LABELS = [
  '冷蔵庫・キッチン家電',
  '洗濯機・生活家電',
  'エアコン・季節家電',
  'テレビ・AV機器',
  'PC・OA機器',
  'その他',
] as const;

export type CategoryLabel = (typeof CATEGORY_LABELS)[number];

export interface ApplianceRecord {
  id: string;
  name: string;
  manufacturer: string | null;
  model: string | null;
  categoryLabel: CategoryLabel;
  purchaseDate: string | null; // "YYYY-MM-DD"
  warrantyPeriodText: string | null; // 例: "1年間"
  warrantyEndDate: string | null; // "YYYY-MM-DD"
  retailer: string | null;
  supportPhone: string | null;
  serialNumber: string | null;
  memo: string | null;
  photoUri: string | null; // 端末内ファイルパス
  createdAt: number; // epoch ms
  updatedAt: number;
}

export type WarrantyStatus = 'active' | 'expiring' | 'expired' | 'unknown';

/** AI読み取りAPIのレスポンス（サーバー側スキーマと一致させること） */
export interface ExtractedWarranty {
  name: string | null;
  manufacturer: string | null;
  model: string | null;
  category: string | null;
  purchaseDate: string | null;
  warrantyPeriodText: string | null;
  warrantyEndDate: string | null;
  retailer: string | null;
  supportPhone: string | null;
  serialNumber: string | null;
  memo: string | null;
}
