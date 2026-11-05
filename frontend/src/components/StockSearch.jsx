import { useState } from 'react';
import { api, errorMessage } from '../lib/api';

const PROVIDERS = { pixabay: ['Pixabay', 'https://pixabay.com'], pexels: ['Pexels', 'https://www.pexels.com'] };

/** Search free stock video (Pixabay and/or Pexels) and bring clips into the project as B-roll. */
export default function StockSearch({ project, available, providers = [], act, defaultOrientation }) {
  const [query, setQuery] = useState('');
  const [orientation, setOrientation] = useState(defaultOrientation || '');
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [added, setAdded] = useState({});

  if (!available) {
    return (
      <p className="fine-print">
        Stock footage needs a free Pixabay API key: add <code>PIXABAY_API_KEY</code> to <code>backend/.env</code> and
        restart the server. You can still use your own clips or generate images.
      </p>
    );
  }

  const search = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.stockSearch(project.id, query.trim(), orientation || undefined);
      setResults(r.results);
    } catch (err) {
      setError(errorMessage(err));
    }
    setBusy(false);
  };

  const add = async (clip) => {
    const key = `${clip.provider}-${clip.id}`;
    setAdded((a) => ({ ...a, [key]: 'adding' }));
    const ok = await act(() => api.stockAdd(project.id, clip.provider, clip.id));
    setAdded((a) => ({ ...a, [key]: ok ? 'added' : undefined }));
  };

  return (
    <div className="stock">
      <form className="link-form" onSubmit={search}>
        <label htmlFor="stock-q">Find stock B-roll</label>
        <div className="input-row">
          <input
            id="stock-q"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. pouring coffee close up"
            maxLength={100}
          />
          <button className="button-secondary" disabled={!query.trim() || busy}>
            {busy ? '…' : 'Search'}
          </button>
        </div>
        <label className="visually-hidden" htmlFor="stock-o">
          Orientation
        </label>
        <select id="stock-o" value={orientation} onChange={(e) => setOrientation(e.target.value)}>
          <option value="">Any shape</option>
          <option value="portrait">Vertical</option>
          <option value="landscape">Horizontal</option>
          <option value="square">Square</option>
        </select>
      </form>
      {error && <p className="asset-status tone-error">{error}</p>}
      {results && results.length === 0 && <p className="empty-hint">No clips found. Try a more concrete search.</p>}
      {results && results.length > 0 && (
        <ul className="stock-grid">
          {results.map((r) => {
            const key = `${r.provider}-${r.id}`;
            return (
            <li key={key} className="stock-item">
              <img src={r.thumbnail} alt="" loading="lazy" />
              <span className="stock-meta">
                {r.duration}s, {r.width}×{r.height}
              </span>
              <a className="stock-credit" href={r.credit_url || r.page_url} target="_blank" rel="noreferrer">
                {r.credit}
              </a>
              <button className="button-secondary" disabled={!!added[key]} onClick={() => add(r)}>
                {added[key] === 'added' ? 'Added' : added[key] === 'adding' ? 'Adding…' : 'Add'}
              </button>
            </li>
            );
          })}
        </ul>
      )}
      <p className="fine-print">
        Stock videos from{' '}
        {providers.map((p, i) => (
          <span key={p}>
            {i > 0 && ' and '}
            <a href={PROVIDERS[p]?.[1]} target="_blank" rel="noreferrer">
              {PROVIDERS[p]?.[0] || p}
            </a>
          </span>
        ))}
        .
      </p>
    </div>
  );
}
