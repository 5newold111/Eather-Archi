import * as SQLite from 'expo-sqlite';
import { CATEGORY_LABELS, type ApplianceRecord, type CategoryLabel } from '../types';

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const db = await SQLite.openDatabaseAsync('warranty-vault.db');
      await db.execAsync(`
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS appliances (
          id TEXT PRIMARY KEY NOT NULL,
          name TEXT NOT NULL,
          manufacturer TEXT,
          model TEXT,
          categoryLabel TEXT NOT NULL,
          purchaseDate TEXT,
          warrantyPeriodText TEXT,
          warrantyEndDate TEXT,
          retailer TEXT,
          supportPhone TEXT,
          serialNumber TEXT,
          memo TEXT,
          photoUri TEXT,
          createdAt INTEGER NOT NULL,
          updatedAt INTEGER NOT NULL
        );
      `);
      return db;
    })();
  }
  return dbPromise;
}

function normalizeCategory(label: string): CategoryLabel {
  return (CATEGORY_LABELS as readonly string[]).includes(label)
    ? (label as CategoryLabel)
    : 'その他';
}

function rowToRecord(row: Record<string, unknown>): ApplianceRecord {
  return {
    id: String(row.id),
    name: String(row.name),
    manufacturer: (row.manufacturer as string | null) ?? null,
    model: (row.model as string | null) ?? null,
    categoryLabel: normalizeCategory(String(row.categoryLabel)),
    purchaseDate: (row.purchaseDate as string | null) ?? null,
    warrantyPeriodText: (row.warrantyPeriodText as string | null) ?? null,
    warrantyEndDate: (row.warrantyEndDate as string | null) ?? null,
    retailer: (row.retailer as string | null) ?? null,
    supportPhone: (row.supportPhone as string | null) ?? null,
    serialNumber: (row.serialNumber as string | null) ?? null,
    memo: (row.memo as string | null) ?? null,
    photoUri: (row.photoUri as string | null) ?? null,
    createdAt: Number(row.createdAt),
    updatedAt: Number(row.updatedAt),
  };
}

export async function listRecords(): Promise<ApplianceRecord[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<Record<string, unknown>>(
    'SELECT * FROM appliances ORDER BY createdAt DESC',
  );
  return rows.map(rowToRecord);
}

export async function getRecord(id: string): Promise<ApplianceRecord | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<Record<string, unknown>>(
    'SELECT * FROM appliances WHERE id = ?',
    [id],
  );
  return row ? rowToRecord(row) : null;
}

export async function upsertRecord(record: ApplianceRecord): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    `INSERT OR REPLACE INTO appliances
      (id, name, manufacturer, model, categoryLabel, purchaseDate, warrantyPeriodText,
       warrantyEndDate, retailer, supportPhone, serialNumber, memo, photoUri, createdAt, updatedAt)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      record.id,
      record.name,
      record.manufacturer,
      record.model,
      record.categoryLabel,
      record.purchaseDate,
      record.warrantyPeriodText,
      record.warrantyEndDate,
      record.retailer,
      record.supportPhone,
      record.serialNumber,
      record.memo,
      record.photoUri,
      record.createdAt,
      record.updatedAt,
    ],
  );
}

export async function deleteRecord(id: string): Promise<void> {
  const db = await getDb();
  await db.runAsync('DELETE FROM appliances WHERE id = ?', [id]);
}

export async function deleteAllRecords(): Promise<void> {
  const db = await getDb();
  await db.runAsync('DELETE FROM appliances');
}

export async function countRecords(): Promise<number> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ c: number }>(
    'SELECT COUNT(*) AS c FROM appliances',
  );
  return row?.c ?? 0;
}
