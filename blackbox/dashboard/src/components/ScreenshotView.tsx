import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../api/client';
import { basename, classNames } from '../lib/util';

export interface ScreenshotViewProps {
  targetId: string;
  /**
   * Screenshot reference from a state or evidence record. Not named `ref`:
   * React reserves that prop name on function components.
   */
  screenshotRef: string | null | undefined;
  alt?: string;
  className?: string;
}

/**
 * Renders one screenshot served by `/api/targets/{id}/screenshots/{filename}`.
 * The only network request the dashboard makes outside the JSON API.
 */
export function ScreenshotView({
  targetId,
  screenshotRef,
  alt,
  className,
}: ScreenshotViewProps): ReactElement {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const ref = screenshotRef ?? null;

  useEffect(() => {
    setFailed(false);
    setLoaded(false);
  }, [ref, targetId]);

  if (!ref) {
    return <div className={classNames('screenshot', 'screenshot-empty', className)}>no screenshot captured</div>;
  }

  const file = basename(ref);
  if (failed) {
    return (
      <div className={classNames('screenshot', 'screenshot-empty', className)}>
        screenshot unavailable <span className="mono">{file}</span>
      </div>
    );
  }

  return (
    <figure className={classNames('screenshot', className)}>
      {loaded ? null : (
        <div className="screenshot-placeholder" role="status">
          loading image…
        </div>
      )}
      <img
        src={api.screenshotUrl(targetId, ref)}
        alt={alt ?? `captured state screenshot ${file}`}
        loading="lazy"
        decoding="async"
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
      />
      <figcaption className="mono">{file}</figcaption>
    </figure>
  );
}
