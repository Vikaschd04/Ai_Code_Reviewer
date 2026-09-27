import { useCallback, useEffect, useState } from "react";

import { describeError } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Load data for the current dependencies; stale responses are aborted so a previous project's
 * or scan's data can never overwrite the one on screen. State changes only happen in callbacks.
 */
export function useAsync<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
): AsyncState<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => {
        setState({ data, error: null, loading: false });
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return;
        setState((previous) => ({ ...previous, error: describeError(caught), loading: false }));
      },
    );
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller supplies the dependency list
  }, [...deps, generation]);

  const reload = useCallback(() => {
    setState((previous) => ({ ...previous, loading: true }));
    setGeneration((value) => value + 1);
  }, []);

  return { ...state, reload };
}
