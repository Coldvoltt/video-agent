import { useState } from 'react';

const KEY = 'editorToolTab';

/** The editing tools as tabs, each marked with the colour of the timeline layer it makes. */
export default function ToolTabs({ tabs }) {
  const [active, setActive] = useState(() => {
    try {
      return localStorage.getItem(KEY) || tabs[0].id;
    } catch {
      return tabs[0].id;
    }
  });
  const current = tabs.find((t) => t.id === active) || tabs[0];
  const pick = (id) => {
    setActive(id);
    try {
      localStorage.setItem(KEY, id);
    } catch {
      /* private mode: the tab just isn't remembered */
    }
  };

  return (
    <section className="tools" aria-label="Editing tools">
      <div className="tool-tabs" role="tablist" aria-label="Tools">
        {tabs.map((t) => (
          <button key={t.id} role="tab" id={`tab-${t.id}`} aria-selected={t.id === current.id}
            aria-controls={`tool-${t.id}`} className={`tool-tab tone-${t.tone}`} onClick={() => pick(t.id)}>
            <span className="tool-dot" aria-hidden="true" />
            {t.label}
            {t.count ? <span className="tool-count">{t.count}</span> : null}
          </button>
        ))}
      </div>
      <div className="tool-body" role="tabpanel" id={`tool-${current.id}`} aria-labelledby={`tab-${current.id}`}>
        {current.node}
      </div>
    </section>
  );
}
