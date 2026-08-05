import * as FileSystem from 'expo-file-system/legacy';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';

const PHOTO_DIR = FileSystem.documentDirectory + 'photos/';

async function ensurePhotoDir(): Promise<void> {
  const info = await FileSystem.getInfoAsync(PHOTO_DIR);
  if (!info.exists) {
    await FileSystem.makeDirectoryAsync(PHOTO_DIR, { intermediates: true });
  }
}

function resizeSpec(
  width: number,
  height: number,
  maxDim: number,
): { width: number } | { height: number } | null {
  if (Math.max(width, height) <= maxDim) return null;
  return width >= height ? { width: maxDim } : { height: maxDim };
}

/**
 * サムネイル用に縮小した写真をアプリ領域へ保存し、そのファイルURIを返す。
 * DBには base64 ではなくこのパスのみ保持する。
 */
export async function saveThumbnail(
  sourceUri: string,
  width: number,
  height: number,
): Promise<string> {
  await ensurePhotoDir();
  const spec = resizeSpec(width, height, 640);
  const ctx = ImageManipulator.manipulate(sourceUri);
  if (spec) ctx.resize(spec);
  const rendered = await ctx.renderAsync();
  const saved = await rendered.saveAsync({ format: SaveFormat.JPEG, compress: 0.7 });
  const dest = `${PHOTO_DIR}${Date.now()}-${Math.random().toString(36).slice(2, 8)}.jpg`;
  await FileSystem.copyAsync({ from: saved.uri, to: dest });
  return dest;
}

/**
 * AI解析用に長辺1400px・quality0.85程度へ縮小し base64 を返す。
 * 通信量とAPIコストを抑えるため（プロトタイプと同じ方針）。
 */
export async function prepareForAnalysis(
  sourceUri: string,
  width: number,
  height: number,
): Promise<{ base64: string; mediaType: 'image/jpeg' }> {
  const spec = resizeSpec(width, height, 1400);
  const ctx = ImageManipulator.manipulate(sourceUri);
  if (spec) ctx.resize(spec);
  const rendered = await ctx.renderAsync();
  const saved = await rendered.saveAsync({
    format: SaveFormat.JPEG,
    compress: 0.85,
    base64: true,
  });
  if (!saved.base64) throw new Error('画像の変換に失敗しました');
  return { base64: saved.base64, mediaType: 'image/jpeg' };
}

export async function deletePhoto(uri: string | null): Promise<void> {
  if (!uri) return;
  try {
    await FileSystem.deleteAsync(uri, { idempotent: true });
  } catch {
    // 写真の削除失敗は致命的ではないため無視
  }
}

export async function deleteAllPhotos(): Promise<void> {
  try {
    await FileSystem.deleteAsync(PHOTO_DIR, { idempotent: true });
  } catch {
    // ignore
  }
}
