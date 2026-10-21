import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { TargetSummary } from '../api/types';
import { toError } from '../lib/util';

const STORAGE_KEY = 'blackbox.selectedTarget';

function readStoredTarget(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredTarget(targetId: string | null): void {
  try {
    if (targetId) window.localStorage.setItem(STORAGE_KEY, targetId);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // private mode / storage disabled: selection just does not persist
  }
}

export interface UseTargetResult {
  targets: TargetSummary[];
  selectedId: string | null;
  selected: TargetSummary | null;
  loading: boolean;
  error: Error | null;
  selectTarget: (targetId: string | null) => void;
  reload: () => void;
}

/**
 * The target list plus the current selection. The previous selection is kept
 * when the list is refreshed, and persisted so a reload stays on the same
 * target.
 */
export function useTarget(): UseTargetResult {
  const [targets, setTargets] = useState<TargetSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(() => readStoredTarget());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => {
    setTick((value) => value + 1);
  }, []);

  const selectTarget = useCallback((targetId: string | null) => {
    setSelectedId(targetId);
    writeStoredTarget(targetId);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.listTargets().then(
      (list) => {
        if (cancelled) return;
        const rows = Array.isArray(list) ? list : [];
        setTargets(rows);
        setSelectedId((previous) => {
          if (previous && rows.some((row) => row.target_id === previous)) return previous;
          return rows.length > 0 ? rows[0].target_id : null;
        });
        setLoading(false);
      },
      (cause: unknown) => {
        if (cancelled) return;
        setTargets([]);
        setError(toError(cause));
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const selected = useMemo(
    () => targets.find((target) => target.target_id === selectedId) ?? null,
    [targets, selectedId],
  );

  return { targets, selectedId, selected, loading, error, selectTarget, reload };
}
