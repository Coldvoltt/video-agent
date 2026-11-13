import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../lib/api';

/**
 * Sign in or create an account, in a dialog. `reason` says why it opened (e.g. to start editing);
 * `onDone(user)` runs once signed in. Without `onClose` it can't be dismissed (pages that need an account).
 */
export default function AuthModal({ reason, onDone, onClose, initialMode = 'signup' }) {
  const [mode, setMode] = useState(initialMode);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef(null);
  const first = useRef(null);

  useEffect(() => {
    const d = dialog.current;
    if (d && !d.open) d.showModal();
    first.current?.focus();
    return () => d?.open && d.close();
  }, []);

  useEffect(() => {
    setError('');
    first.current?.focus();
  }, [mode]);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const r = mode === 'signup' ? await api.signUp(email, password, name) : await api.signIn(email, password);
      onDone(r.user, r.projects_moved || 0);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  const signup = mode === 'signup';
  return (
    <dialog ref={dialog} className="auth-modal" aria-labelledby="auth-title"
      onCancel={(e) => { if (!onClose) e.preventDefault(); else onClose(); }}>
      <form onSubmit={submit} className="auth-form">
        <div className="auth-head">
          <span className="brand-mark" aria-hidden="true">V</span>
          {onClose && (
            <button type="button" className="button-quiet auth-close" onClick={onClose} aria-label="Close">
              ✕
            </button>
          )}
        </div>
        <h2 id="auth-title">{signup ? 'Create your account' : 'Welcome back'}</h2>
        {reason && <p className="auth-reason">{reason}</p>}

        <div className="auth-switch" role="tablist" aria-label="Account">
          <button type="button" role="tab" aria-selected={signup} onClick={() => setMode('signup')}>
            Create account
          </button>
          <button type="button" role="tab" aria-selected={!signup} onClick={() => setMode('signin')}>
            Sign in
          </button>
        </div>

        {signup && (
          <label className="auth-field">
            <span className="control-label">Your name</span>
            <input ref={first} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={80}
              placeholder="What should we call you?" />
          </label>
        )}
        <label className="auth-field">
          <span className="control-label">Email</span>
          <input ref={signup ? undefined : first} type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            autoComplete="email" placeholder="you@example.com" />
        </label>
        <label className="auth-field">
          <span className="control-label">Password{signup ? ' (at least 8 characters)' : ''}</span>
          <input type="password" required minLength={signup ? 8 : undefined} value={password}
            onChange={(e) => setPassword(e.target.value)} autoComplete={signup ? 'new-password' : 'current-password'} />
        </label>

        {error && <p className="auth-error" role="alert">{error}</p>}

        <button className="button-primary auth-submit" disabled={busy}>
          {busy ? 'One moment…' : signup ? 'Create account and continue' : 'Sign in and continue'}
        </button>
        <p className="fine-print auth-foot">
          {signup ? 'Anything you’ve already started here moves into your account.' : 'Your projects are waiting.'}
        </p>
      </form>
    </dialog>
  );
}
