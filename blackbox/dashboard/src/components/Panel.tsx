import type { ReactElement, ReactNode } from 'react';
import type { ApiResource } from '../hooks/useApiData';
import { classNames, describeError } from '../lib/util';

/* ------------------------------------------------------------------- panel */

export interface PanelProps {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}

export function Panel({ title, subtitle, actions, children, className, id }: PanelProps): ReactElement {
  return (
    <section className={classNames('panel', className)} id={id} aria-label={title}>
      <header className="panel-head">
        <h2 className="panel-title">{title}</h2>
        {subtitle !== undefined && subtitle !== null ? <span className="panel-sub">{subtitle}</span> : null}
        <div className="panel-actions">{actions}</div>
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export interface KeyValueEntry {
  label: string;
  value: ReactNode;
  mono?: boolean;
}

export function KeyValues({ entries }: { entries: KeyValueEntry[] }): ReactElement {
  return (
    <dl className="kv">
      {entries.map((entry, index) => (
        <div className="kv-row" key={`${entry.label}-${index}`}>
          <dt>{entry.label}</dt>
          <dd className={classNames(entry.mono && 'mono')}>{entry.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------- loading/empty/err */

export function Loading({ label = 'loading…' }: { label?: string }): ReactElement {
  return (
    <p className="state state-loading" role="status" aria-live="polite">
      {label}
    </p>
  );
}

export function EmptyState({ message, hint }: { message: string; hint?: ReactNode }): ReactElement {
  return (
    <div className="state state-empty">
      <p>{message}</p>
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }): ReactElement {
  const info = describeError(error);
  return (
    <div className="state state-error" role="alert">
      <p>
        <strong className="mono">{info.label}</strong>
        {info.message ? <span> — {info.message}</span> : null}
      </p>
      {onRetry ? (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          retry
        </button>
      ) : null}
    </div>
  );
}

/**
 * Renders the four states every view needs — error, loading, empty, data — so
 * no view has to re-implement them.
 */
export interface ResourceViewProps<T> {
  resource: ApiResource<T>;
  isEmpty?: (data: T) => boolean;
  emptyMessage?: string;
  emptyHint?: ReactNode;
  loadingLabel?: string;
  children: (data: T) => ReactNode;
}

export function ResourceView<T>({
  resource,
  isEmpty,
  emptyMessage = 'no data',
  emptyHint,
  loadingLabel,
  children,
}: ResourceViewProps<T>): ReactElement {
  if (resource.error) return <ErrorState error={resource.error} onRetry={resource.reload} />;
  if (resource.loading && resource.data === null) return <Loading label={loadingLabel} />;
  if (resource.data === null) return <EmptyState message={emptyMessage} hint={emptyHint} />;
  if (isEmpty && isEmpty(resource.data)) return <EmptyState message={emptyMessage} hint={emptyHint} />;
  return <>{children(resource.data)}</>;
}
