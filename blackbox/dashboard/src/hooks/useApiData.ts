import { useCallback, useEffect, useRef, useState } from 'react';
import { toError } from '../lib/util';

/**
 * Minimal async-data hook: one resource, explicit reload, no cache.
 *
 * `loader === null` means "nothing to fetch yet" (e.g. no target selected) and
 * resolves to `{ data: null, loading: false, error: null }` so views can show
 * their empty state instead of a spinner.
 */
export interface ApiResource<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
}

export interface UseApiDataOptions {
  /**
   * Swallow failures (data stays null, error stays null). Used for
   * best-effort lookups such as "is this graph edge also a stored transition?".
   */
  optional?: boolean;
}

export function useApiData<T>(
  loader: (() => Promise<T>) | null,
  deps: readonly unknown[],
  options: UseApiDataOptions = {},
): ApiResource<T> {
  const optional = options.optional === true;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(loader !== null);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  // Read the loader/options through refs: callers pass inline closures, which
  // would otherwise restart the request on every single render.
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const optionalRef = useRef(optional);
  optionalRef.current = optional;

  const reload = useCallback(() => {
    setTick((value) => value + 1);
  }, []);

  const enabled = loader !== null;

  useEffect(() => {
    const current = loaderRef.current;
    if (!current) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    current().then(
      (result) => {
        if (cancelled) return;
        setData(result);
        setLoading(false);
      },
      (cause: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(optionalRef.current ? null : toError(cause));
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);

  return { data, loading, error, reload };
}
