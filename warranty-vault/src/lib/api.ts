import type { ExtractedWarranty } from '../types';

/**
 * AI読み取りプロキシ（server/ ディレクトリ参照）のエンドポイント。
 * APIキーはサーバー側にのみ保持し、アプリには一切埋め込まない。
 */
const EXTRACT_API_URL =
  process.env.EXPO_PUBLIC_EXTRACT_API_URL ?? 'https://example.invalid/api/extract-warranty';

export class ExtractError extends Error {}

export async function extractWarranty(
  base64: string,
  mediaType: string,
): Promise<ExtractedWarranty> {
  let response: Response;
  try {
    response = await fetch(EXTRACT_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: base64, mediaType }),
    });
  } catch {
    throw new ExtractError('通信に失敗しました。電波の良い場所でもう一度お試しください。');
  }
  if (!response.ok) {
    throw new ExtractError('読み取りに失敗しました');
  }
  const data = (await response.json()) as Partial<ExtractedWarranty> & { error?: string };
  if (data.error) {
    throw new ExtractError(data.error);
  }
  return {
    name: data.name ?? null,
    manufacturer: data.manufacturer ?? null,
    model: data.model ?? null,
    category: data.category ?? null,
    purchaseDate: data.purchaseDate ?? null,
    warrantyPeriodText: data.warrantyPeriodText ?? null,
    warrantyEndDate: data.warrantyEndDate ?? null,
    retailer: data.retailer ?? null,
    supportPhone: data.supportPhone ?? null,
    serialNumber: data.serialNumber ?? null,
    memo: data.memo ?? null,
  };
}
