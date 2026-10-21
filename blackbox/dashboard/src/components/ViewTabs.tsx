import { useRef } from 'react';
import type { KeyboardEvent, ReactElement } from 'react';
import { classNames } from '../lib/util';

export interface ViewTabDefinition {
  id: string;
  label: string;
  hint?: string;
}

export interface ViewTabsProps {
  tabs: ViewTabDefinition[];
  active: string;
  onChange: (id: string) => void;
}

/** Roving-tabindex tab strip: ←/→/Home/End move and focus, standard ARIA tabs. */
export function ViewTabs({ tabs, active, onChange }: ViewTabsProps): ReactElement {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    const last = tabs.length - 1;
    let next = -1;
    if (event.key === 'ArrowRight') next = index === last ? 0 : index + 1;
    else if (event.key === 'ArrowLeft') next = index === 0 ? last : index - 1;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = last;
    if (next < 0) return;
    const tab = tabs[next];
    if (!tab) return;
    event.preventDefault();
    onChange(tab.id);
    refs.current[next]?.focus();
  };

  return (
    <div className="tabs" role="tablist" aria-label="Dashboard views">
      {tabs.map((tab, index) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            title={tab.hint}
            className={classNames('tab', selected && 'tab-active')}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
