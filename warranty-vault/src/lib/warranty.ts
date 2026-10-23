import type { WarrantyStatus } from '../types';

export const STATUS_META: Record<WarrantyStatus, { label: string }> = {
  active: { label: '保証期間内' },
  expiring: { label: 'まもなく期限' },
  expired: { label: '保証期限切れ' },
  unknown: { label: '期限未設定' },
};

export function computeStatus(endDateStr: string | null): WarrantyStatus {
  if (!endDateStr) return 'unknown';
  const end = new Date(endDateStr + 'T00:00:00');
  if (isNaN(end.getTime())) return 'unknown';
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  const diffDays = Math.round((end.getTime() - now.getTime()) / 86400000);
  if (diffDays < 0) return 'expired';
  if (diffDays <= 30) return 'expiring';
  return 'active';
}

export function daysUntil(endDateStr: string | null): number | null {
  if (!endDateStr) return null;
  const end = new Date(endDateStr + 'T00:00:00');
  if (isNaN(end.getTime())) return null;
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((end.getTime() - now.getTime()) / 86400000);
}

/** 「◯年」「◯ヶ月」表記から保証終了日を推定する（プロトタイプ移植） */
export function tryComputeEndDate(
  purchaseDateStr: string | null,
  periodText: string | null,
): string | null {
  if (!purchaseDateStr || !periodText) return null;
  const start = new Date(purchaseDateStr + 'T00:00:00');
  if (isNaN(start.getTime())) return null;
  const yMatch = periodText.match(/(\d+)\s*年/);
  const mMatch = periodText.match(/(\d+)\s*(?:ヶ月|か月|カ月|ケ月)/);
  const years = yMatch ? parseInt(yMatch[1], 10) : 0;
  const months = mMatch ? parseInt(mMatch[1], 10) : 0;
  if (years === 0 && months === 0) return null;
  const end = new Date(start);
  end.setFullYear(end.getFullYear() + years);
  end.setMonth(end.getMonth() + months);
  const y = end.getFullYear();
  const m = String(end.getMonth() + 1).padStart(2, '0');
  const d = String(end.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function telUrl(phone: string): string {
  return 'tel:' + phone.replace(/[^0-9+]/g, '');
}

export function formatDateJa(dateStr: string | null): string {
  if (!dateStr) return '―';
  const d = new Date(dateStr + 'T00:00:00');
  if (isNaN(d.getTime())) return dateStr;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}
