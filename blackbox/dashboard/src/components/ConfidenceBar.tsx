import type { ReactElement } from 'react';
import { classNames, confidencePercent } from '../lib/util';

export interface ConfidenceBarProps {
  value: number | null | undefined;
  label?: string;
  showValue?: boolean;
  className?: string;
  title?: string;
}

/** Narrow 0..1 confidence meter with the number spelled out next to it. */
export function ConfidenceBar({
  value,
  label = 'confidence',
  showValue = true,
  className,
  title,
}: ConfidenceBarProps): ReactElement {
  const percent = confidencePercent(value);
  const tone = percent === null ? 'none' : percent >= 70 ? 'high' : percent >= 40 ? 'mid' : 'low';
  return (
    <span className={classNames('confidence', className)} title={title ?? `${label}: ${percent ?? '—'}%`}>
      <span className="confidence-label">{label}</span>
      <span
        className="confidence-track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-valuetext={percent === null ? 'unknown' : `${percent}%`}
      >
        <span className={classNames('confidence-fill', `confidence-${tone}`)} style={{ width: `${percent ?? 0}%` }} />
      </span>
      {showValue ? <span className="confidence-value mono">{percent === null ? '—' : `${percent}%`}</span> : null}
    </span>
  );
}
