import type { ApplianceRecord } from '../types';

function escapeCsv(value: string | null): string {
  const s = value ?? '';
  if (/[",\n]/.test(s)) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

/** 引っ越し・保険請求・買い替え検討用のCSVエクスポート（Pro機能） */
export function recordsToCsv(records: ApplianceRecord[]): string {
  const header = [
    '名称',
    'メーカー',
    '型番',
    'カテゴリ',
    '購入日',
    '保証期間',
    '保証終了日',
    '購入店舗',
    'サポート電話',
    '保証書番号',
    'メモ',
  ].join(',');
  const rows = records.map((r) =>
    [
      r.name,
      r.manufacturer,
      r.model,
      r.categoryLabel,
      r.purchaseDate,
      r.warrantyPeriodText,
      r.warrantyEndDate,
      r.retailer,
      r.supportPhone,
      r.serialNumber,
      r.memo,
    ]
      .map(escapeCsv)
      .join(','),
  );
  return [header, ...rows].join('\n');
}
