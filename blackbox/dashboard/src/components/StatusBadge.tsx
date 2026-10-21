import type { ReactElement } from 'react';
import { classNames } from '../lib/util';

export type BadgeKind = 'status' | 'risk' | 'mode' | 'decision' | 'type' | 'kind' | 'generic';

export interface StatusBadgeProps {
  value: string | null | undefined;
  kind?: BadgeKind;
  title?: string;
  className?: string;
}

function slugify(token: string): string {
  return token.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'unknown';
}

/**
 * Colour-coded token badge. Colour comes from the *value* slug, not the kind,
 * so VERIFIED is green whether it is a transition status or a job status.
 */
export function StatusBadge({ value, kind = 'status', title, className }: StatusBadgeProps): ReactElement {
  const token = value === null || value === undefined || value === '' ? 'UNKNOWN' : String(value);
  const slug = slugify(token);
  return (
    <span
      className={classNames('badge', `badge-${kind}`, `badge-${slug}`, className)}
      data-value={token.toUpperCase()}
      title={title ?? token}
    >
      {token}
    </span>
  );
}
