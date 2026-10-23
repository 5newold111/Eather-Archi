import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import type { ApplianceRecord } from '../types';
import * as db from '../lib/db';
import { deleteAllPhotos, deletePhoto } from '../lib/images';
import { syncExpiryNotifications } from '../lib/notifications';

interface RecordsContextValue {
  records: ApplianceRecord[];
  loading: boolean;
  refresh: () => Promise<void>;
  save: (record: ApplianceRecord) => Promise<void>;
  remove: (id: string) => Promise<void>;
  removeAll: () => Promise<void>;
}

const RecordsContext = createContext<RecordsContextValue | null>(null);

export function RecordsProvider({ children }: { children: React.ReactNode }) {
  const [records, setRecords] = useState<ApplianceRecord[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const list = await db.listRecords();
    setRecords(list);
    setLoading(false);
    syncExpiryNotifications(list).catch(() => {});
  }, []);

  useEffect(() => {
    refresh().catch(() => setLoading(false));
  }, [refresh]);

  const save = useCallback(
    async (record: ApplianceRecord) => {
      const previous = records.find((r) => r.id === record.id);
      if (previous?.photoUri && previous.photoUri !== record.photoUri) {
        await deletePhoto(previous.photoUri);
      }
      await db.upsertRecord(record);
      await refresh();
    },
    [records, refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      const target = records.find((r) => r.id === id);
      await db.deleteRecord(id);
      if (target?.photoUri) await deletePhoto(target.photoUri);
      await refresh();
    },
    [records, refresh],
  );

  const removeAll = useCallback(async () => {
    await db.deleteAllRecords();
    await deleteAllPhotos();
    await refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ records, loading, refresh, save, remove, removeAll }),
    [records, loading, refresh, save, remove, removeAll],
  );

  return <RecordsContext.Provider value={value}>{children}</RecordsContext.Provider>;
}

export function useRecords(): RecordsContextValue {
  const ctx = useContext(RecordsContext);
  if (!ctx) throw new Error('useRecords must be used within RecordsProvider');
  return ctx;
}
