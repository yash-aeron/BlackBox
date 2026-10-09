import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { api } from '../api/client';
import type { RegisterTargetRequest, TargetSummary } from '../api/types';
import { count, toError } from '../lib/util';
import { ErrorState } from './Panel';
import { StatusBadge } from './StatusBadge';

export interface TargetPickerProps {
  targets: TargetSummary[];
  selectedId: string | null;
  loading: boolean;
  error: unknown;
  onSelect: (targetId: string) => void;
  onReload: () => void;
  onRegistered?: (target: TargetSummary) => void;
}

/** Target switcher plus the `POST /targets` authorization form. */
export function TargetPicker({
  targets,
  selectedId,
  loading,
  error,
  onSelect,
  onReload,
  onRegistered,
}: TargetPickerProps): ReactElement {
  const [open, setOpen] = useState(false);
  const [targetId, setTargetId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [mode, setMode] = useState('SUPERVISED');
  const [allowedOrigins, setAllowedOrigins] = useState('');
  const [maxSteps, setMaxSteps] = useState('300');
  const [maxDuration, setMaxDuration] = useState('600');
  const [allowMedium, setAllowMedium] = useState(false);
  const [allowHigh, setAllowHigh] = useState(false);
  const [allowCritical, setAllowCritical] = useState(false);
  const [authorizedBy, setAuthorizedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const selected = targets.find((target) => target.target_id === selectedId) ?? null;

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const id = targetId.trim();
    const url = baseUrl.trim();
    if (!id || !url) {
      setFormError(new Error('target_id and base_url are required'));
      return;
    }
    const steps = Number(maxSteps);
    const duration = Number(maxDuration);
    const body: RegisterTargetRequest = {
      target_id: id,
      base_url: url,
      allowed_origins: allowedOrigins
        .split(',')
        .map((origin) => origin.trim())
        .filter(Boolean),
      mode: mode.trim() || 'SUPERVISED',
      max_steps: Number.isFinite(steps) && steps > 0 ? steps : 300,
      max_duration_seconds: Number.isFinite(duration) && duration > 0 ? duration : 600,
      allow_medium_actions: allowMedium,
      allow_high_actions: allowHigh,
      allow_critical_actions: allowCritical,
      authorized_by: authorizedBy.trim(),
      notes: notes.trim(),
    };
    setSubmitting(true);
    setFormError(null);
    setNotice(null);
    api.registerTarget(body).then(
      (target) => {
        setSubmitting(false);
        setNotice(`registered ${target.target_id}`);
        setTargetId('');
        setBaseUrl('');
        setAuthorizedBy('');
        onRegistered?.(target);
      },
      (cause: unknown) => {
        setSubmitting(false);
        setFormError(toError(cause));
      },
    );
  };

  return (
    <div className="target-picker">
      <div className="target-picker-row">
        <label className="field-inline" htmlFor="target-select">
          <span className="field-label">target</span>
          <select
            id="target-select"
            className="input mono"
            value={selectedId ?? ''}
            disabled={loading && targets.length === 0}
            onChange={(event) => onSelect(event.target.value)}
          >
            {targets.length === 0 ? <option value="">{loading ? 'loading…' : 'no targets'}</option> : null}
            {targets.map((target) => (
              <option key={target.target_id} value={target.target_id}>
                {target.target_id} · {target.mode}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-small" onClick={onReload} title="reload target list">
          refresh
        </button>
        <button
          type="button"
          className="btn btn-small"
          aria-expanded={open}
          aria-controls="register-target"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? 'close registration' : 'register target…'}
        </button>
      </div>

      {selected ? (
        <p className="target-meta mono">
          <StatusBadge value={selected.mode} kind="mode" />{' '}
          <StatusBadge value={selected.live ? 'LIVE' : 'IDLE'} kind="status" />{' '}
          {selected.pending_approvals ? (
            <StatusBadge value={`${selected.pending_approvals} pending`} kind="kind" />
          ) : null}{' '}
          {count(selected.stats.states)} states · {count(selected.stats.transitions)} transitions ·{' '}
          {count(selected.stats.workflows)} workflows · {count(selected.stats.evidence)} evidence
          {selected.start_error ? <span className="warn-text"> · start error: {selected.start_error}</span> : null}
        </p>
      ) : null}

      {error ? <ErrorState error={error} onRetry={onReload} /> : null}

      {open ? (
        <form className="form-grid" id="register-target" onSubmit={submit} aria-label="register a target">
          <label className="field" htmlFor="rt-id">
            <span className="field-label">target id *</span>
            <input
              id="rt-id"
              className="input mono"
              value={targetId}
              required
              onChange={(event) => setTargetId(event.target.value)}
              placeholder="demo-shop"
            />
          </label>
          <label className="field" htmlFor="rt-url">
            <span className="field-label">base url *</span>
            <input
              id="rt-url"
              className="input mono"
              value={baseUrl}
              required
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="http://127.0.0.1:8000"
            />
          </label>
          <label className="field" htmlFor="rt-mode">
            <span className="field-label">mode</span>
            <input
              id="rt-mode"
              className="input mono"
              list="rt-modes"
              value={mode}
              onChange={(event) => setMode(event.target.value)}
            />
            <datalist id="rt-modes">
              <option value="SUPERVISED" />
              <option value="AUTONOMOUS" />
              <option value="UNATTENDED" />
            </datalist>
          </label>
          <label className="field" htmlFor="rt-origins">
            <span className="field-label">allowed origins (comma separated)</span>
            <input
              id="rt-origins"
              className="input mono"
              value={allowedOrigins}
              onChange={(event) => setAllowedOrigins(event.target.value)}
              placeholder="http://127.0.0.1:8000"
            />
          </label>
          <label className="field" htmlFor="rt-steps">
            <span className="field-label">max steps</span>
            <input
              id="rt-steps"
              className="input mono"
              type="number"
              min={1}
              value={maxSteps}
              onChange={(event) => setMaxSteps(event.target.value)}
            />
          </label>
          <label className="field" htmlFor="rt-duration">
            <span className="field-label">max duration (s)</span>
            <input
              id="rt-duration"
              className="input mono"
              type="number"
              min={1}
              value={maxDuration}
              onChange={(event) => setMaxDuration(event.target.value)}
            />
          </label>
          <fieldset className="field field-checks">
            <legend className="field-label">action risk policy</legend>
            <label className="checkbox">
              <input type="checkbox" checked={allowMedium} onChange={(event) => setAllowMedium(event.target.checked)} />
              <span>allow medium</span>
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={allowHigh} onChange={(event) => setAllowHigh(event.target.checked)} />
              <span>allow high</span>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={allowCritical}
                onChange={(event) => setAllowCritical(event.target.checked)}
              />
              <span>allow critical</span>
            </label>
          </fieldset>
          <label className="field" htmlFor="rt-authorized">
            <span className="field-label">authorized by</span>
            <input
              id="rt-authorized"
              className="input"
              value={authorizedBy}
              onChange={(event) => setAuthorizedBy(event.target.value)}
              placeholder="who authorized this target"
            />
          </label>
          <label className="field" htmlFor="rt-notes">
            <span className="field-label">notes</span>
            <input
              id="rt-notes"
              className="input"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </label>
          <div className="field form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'registering…' : 'register'}
            </button>
            {notice ? <span className="notice-text">{notice}</span> : null}
          </div>
          {formError ? (
            <div className="field form-error">
              <ErrorState error={formError} />
            </div>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}
