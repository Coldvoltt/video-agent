import { useCallback, useEffect, useState } from 'react';
import AuthModal from './components/AuthModal';
import CheckoutModal from './components/CheckoutModal';
import PlanCards from './components/PlanCards';
import { ago } from './components/ProjectInfo';
import { api, assetFileUrl, errorMessage } from './lib/api';
import { timecode } from './lib/format';

const openProject = (id) => {
  try {
    localStorage.setItem('projectId', id);
  } catch {
    /* the editor falls back to the latest project */
  }
  window.location.href = '/edit';
};

function ProjectCard({ p, onDelete }) {
  return (
    <li className="dash-card">
      <button className="dash-open" onClick={() => openProject(p.id)} aria-label={`Open ${p.name}`}>
        <span className="dash-thumb">
          {p.thumb ? (
            <video src={`${assetFileUrl(p.id, p.thumb)}#t=0.8`} preload="metadata" muted playsInline tabIndex={-1} />
          ) : (
            <span className="dash-thumb-empty">{p.videos ? 'Preparing your video…' : 'No video yet'}</span>
          )}
          {p.duration ? <span className="dash-length">{timecode(p.duration)}</span> : null}
        </span>
        <span className="dash-card-body">
          <strong className="dash-name">{p.name}</strong>
          <span className="dash-meta">
            {p.version ? `Version ${p.version}` : 'Not edited yet'}
            {p.videos ? `, ${p.videos} ${p.videos === 1 ? 'video' : 'videos'}` : ''}
          </span>
          {p.last_change && <span className="dash-change">{p.last_change.replace(/^Agent: /, '')}</span>}
          <span className="dash-when">Updated {ago(p.updated_at)}</span>
        </span>
      </button>
      <button className="button-quiet button-danger dash-delete" onClick={() => onDelete(p)}>
        Delete
      </button>
    </li>
  );
}

function Projects({ projects, onDelete }) {
  if (projects === null) return <p className="empty-hint">Loading your projects…</p>;
  return (
    <section aria-labelledby="projects-title">
      <div className="dash-section-head">
        <h2 id="projects-title">Projects</h2>
        <span className="timeline-total">{projects.length} {projects.length === 1 ? 'project' : 'projects'}</span>
      </div>
      <ul className="dash-grid">
        <li className="dash-card dash-new">
          <a href="/edit?new=1" className="dash-open dash-new-link">
            <span className="dash-plus" aria-hidden="true">+</span>
            <strong>New project</strong>
            <span>Add footage and say what you’re making</span>
          </a>
        </li>
        {projects.map((p) => (
          <ProjectCard key={p.id} p={p} onDelete={onDelete} />
        ))}
      </ul>
    </section>
  );
}

function TelegramIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="integration-icon">
      <circle cx="12" cy="12" r="12" fill="#2AABEE" />
      <path d="M5.5 11.7l11.6-4.5c.5-.2 1 .1.8.9l-2 9.3c-.1.7-.5.8-1.1.5l-3-2.2-1.4 1.4c-.2.2-.3.3-.6.3l.2-3.1 5.6-5.1c.2-.2 0-.3-.4-.1l-6.9 4.4-3-.9c-.6-.2-.6-.6.2-.9z" fill="#fff" />
    </svg>
  );
}

function Settings({ account, onSaved }) {
  const [name, setName] = useState(account.user.name || '');
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [note, setNote] = useState({ where: '', text: '' }); // shown in the panel that changed
  const notify = !!account.settings?.integrations?.telegram?.notify;

  const save = async (body, done, where = 'account') => {
    setNote({ where: '', text: '' });
    try {
      const r = await api.updateAccount(body);
      onSaved(r);
      setNote({ where, text: done });
      return true;
    } catch (e) {
      setNote({ where, text: errorMessage(e) });
      return false;
    }
  };

  return (
    <section aria-labelledby="settings-title" className="dash-settings">
      <div className="dash-section-head">
        <h2 id="settings-title">Settings</h2>
      </div>

      <div className="dash-panel">
        <h3>Account</h3>
        <form className="dash-form" onSubmit={(e) => { e.preventDefault(); save({ name }, 'Name saved.'); }}>
          <label>
            <span className="control-label">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          </label>
          <label>
            <span className="control-label">Email</span>
            <input value={account.user.email} disabled />
          </label>
          <button className="button-secondary" disabled={name === (account.user.name || '')}>Save name</button>
        </form>
        <form className="dash-form" onSubmit={async (e) => {
          e.preventDefault();
          if (await save({ current_password: current, new_password: next }, 'Password changed.')) {
            setCurrent('');
            setNext('');
          }
        }}>
          <label>
            <span className="control-label">Current password</span>
            <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
          </label>
          <label>
            <span className="control-label">New password (at least 8 characters)</span>
            <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={8} />
          </label>
          <button className="button-secondary" disabled={!current || next.length < 8}>Change password</button>
        </form>
        {note.where === 'account' && note.text && <p className="dash-note" role="status">{note.text}</p>}
      </div>

      <div className="dash-panel">
        <h3>Integrations</h3>
        <div className="integration">
          <TelegramIcon />
          <div className="integration-body">
            <div className="integration-title">
              <strong>Telegram</strong>
              <span className="chip chip-blue">Coming soon</span>
            </div>
            <p>
              Send a clip to the VidAgent bot, say what you want, and get the edited video back in the chat. You’ll
              also get a message when a long render finishes.
            </p>
            <label className="check-row">
              <input type="checkbox" checked={notify}
                onChange={(e) => save({ settings: { integrations: { telegram: { notify: e.target.checked } } } },
                  e.target.checked ? 'We’ll let you know when Telegram is ready.' : 'Okay, no Telegram updates.', 'telegram')} />
              <span>Tell me when it’s ready</span>
            </label>
            {note.where === 'telegram' && note.text && <p className="dash-note" role="status">{note.text}</p>}
          </div>
        </div>
      </div>
    </section>
  );
}

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }) : '';
}

/** Plan, coins, upgrades and coin history. */
function PlanTab({ billing, plans, currency, setCurrency, onChoose }) {
  if (!billing || !plans) return <p className="empty-hint">Loading your plan…</p>;
  const ends = billing.plan !== 'free' && billing.period_end;
  return (
    <section aria-labelledby="plan-title" className="dash-plan">
      <div className="dash-section-head">
        <h2 id="plan-title">Plan and vid-coins</h2>
      </div>
      <div className="plan-summary">
        <div className="plan-summary-main">
          <span className="coin coin-big" aria-hidden="true">V</span>
          <div>
            <p className="plan-summary-coins">{billing.coins} vid-coin{billing.coins === 1 ? '' : 's'}</p>
            <p className="plan-summary-plan">
              {billing.plan_name} plan{ends ? `, until ${fmtDate(billing.period_end)}` : ', topped up to 2 each month'}
            </p>
          </div>
        </div>
        <p className="fine-print">1 vid-coin = one full-quality export, per started minute of video. Previews and edits are free.</p>
      </div>

      <div className="currency-toggle" role="radiogroup" aria-label="Show prices in">
        {['USD', 'NGN'].map((c) => (
          <button key={c} role="radio" aria-checked={currency === c} className={currency === c ? 'is-on' : ''}
            onClick={() => setCurrency(c)}>
            {c === 'USD' ? 'USD $' : 'Naira ₦'}
          </button>
        ))}
      </div>
      <PlanCards plans={plans.plans} currency={currency} current={billing.plan} onChoose={onChoose} />

      <div className="dash-panel">
        <h3>Coin history</h3>
        {billing.history.length ? (
          <ul className="ledger">
            {billing.history.map((h, i) => (
              <li key={`${h.created_at}-${i}`}>
                <span className={`ledger-delta ${h.delta < 0 ? 'is-spend' : 'is-gain'}`}>{h.delta > 0 ? `+${h.delta}` : h.delta}</span>
                <span className="ledger-reason">{h.reason}</span>
                <span className="ledger-when">{ago(h.created_at)}</span>
                <span className="ledger-balance">{h.balance} left</span>
              </li>
            ))}
          </ul>
        ) : <p className="empty-hint">No coin activity yet.</p>}
      </div>
    </section>
  );
}

/** Your projects and account settings. Needs an account: signed-out visitors get the sign-in dialog. */
export default function Dashboard() {
  const [account, setAccount] = useState(undefined); // undefined = loading, null = signed out
  const [projects, setProjects] = useState(null);
  const params = new URLSearchParams(window.location.search);
  const [tab, setTab] = useState(() => (window.location.hash === '#settings' ? 'settings'
    : window.location.hash === '#plan' || params.get('upgrade') ? 'plan' : 'projects'));
  const [error, setError] = useState('');
  const [billingState, setBilling] = useState(null);
  const [plans, setPlans] = useState(null);
  const [currency, setCurrency] = useState(params.get('currency') === 'NGN' ? 'NGN' : 'USD');
  const [buying, setBuying] = useState(null); // plan being bought
  const [wantPlan, setWantPlan] = useState(params.get('upgrade')); // from an Upgrade button elsewhere

  const load = useCallback(async () => {
    try {
      const a = await api.account();
      setAccount(a);
      const [list, b, pl] = await Promise.all([api.dashboard(), api.billing(), api.plans()]);
      setProjects(list);
      setBilling(b);
      setPlans(pl);
    } catch (e) {
      if (e?.response?.status === 401) setAccount(null);
      else setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // arriving from an Upgrade button: open that plan's checkout once signed in
  useEffect(() => {
    if (wantPlan && plans && billingState) {
      const p = plans.plans.find((x) => x.id === wantPlan);
      if (p && p.price?.USD) setBuying(p);
      setWantPlan(null);
      history.replaceState(null, '', '/dashboard#plan');
    }
  }, [wantPlan, plans, billingState]);

  const pick = (t) => {
    setTab(t);
    history.replaceState(null, '', `#${t}`);
  };

  const remove = async (p) => {
    if (!window.confirm(`Delete “${p.name}” and all its media and renders?`)) return;
    try {
      await api.deleteProject(p.id);
      setProjects((list) => list.filter((x) => x.id !== p.id));
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const signOut = async () => {
    await api.signOut().catch(() => {});
    window.location.href = '/';
  };

  const user = account?.user;
  return (
    <div className="dash">
      <header className="topbar dash-top">
        <a className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">V</span>
          VidAgent
        </a>
        {user && (
          <nav className="dash-tabs" aria-label="Dashboard">
            <button className={`tool-tab ${tab === 'projects' ? 'is-on' : ''}`} aria-current={tab === 'projects' ? 'page' : undefined}
              onClick={() => pick('projects')}>Projects</button>
            <button className={`tool-tab ${tab === 'plan' ? 'is-on' : ''}`} aria-current={tab === 'plan' ? 'page' : undefined}
              onClick={() => pick('plan')}>Plan and coins</button>
            <button className={`tool-tab ${tab === 'settings' ? 'is-on' : ''}`} aria-current={tab === 'settings' ? 'page' : undefined}
              onClick={() => pick('settings')}>Settings</button>
          </nav>
        )}
        {user && (
          <div className="user-menu">
            {billingState && (
              <button className="coin-chip" onClick={() => pick('plan')} title="Your vid-coins and plan">
                <span className="coin" aria-hidden="true">V</span>
                {billingState.coins}
                <span className="coin-chip-plan">{billingState.plan_name}</span>
              </button>
            )}
            <a className="button-primary" href="/edit?new=1">New project</a>
            <span className="user-chip">
              <span className="user-avatar" aria-hidden="true">{(user.name || user.email)[0].toUpperCase()}</span>
              <span className="user-name">{user.name || user.email}</span>
            </span>
            <button className="button-quiet" onClick={signOut}>Sign out</button>
          </div>
        )}
      </header>

      <main className="dash-main">
        {error && <p className="notice notice-error" role="alert">{error}</p>}
        {user && (
          <>
            <h1 className="dash-hello">Hi {user.name ? user.name.split(' ')[0] : 'there'}.</h1>
            {tab === 'projects' && <Projects projects={projects} onDelete={remove} />}
            {tab === 'plan' && (
              <PlanTab billing={billingState} plans={plans} currency={currency} setCurrency={setCurrency}
                onChoose={(id) => setBuying(plans.plans.find((x) => x.id === id))} />
            )}
            {tab === 'settings' && <Settings account={account} onSaved={(r) => setAccount(r)} />}
            {buying && (
              <CheckoutModal plan={buying} initialCurrency={currency} onClose={() => setBuying(null)}
                onPaid={() => api.billing().then(setBilling)} />
            )}
          </>
        )}
        {account === null && (
          <AuthModal initialMode="signin" reason="Sign in to see your projects and settings."
            onDone={() => load()} />
        )}
      </main>
    </div>
  );
}
