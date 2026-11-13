import { useState } from 'react';
import { api } from '../lib/api';
import { isActive } from '../lib/format';

/**
 * Pick 2-3 videos of the same take (and optionally a separate audio recording) and sync them into one
 * multicam source: lined up by their sound and transcripts, trimmed to the same length, best audio chosen.
 */
export default function MulticamSync({ project, jobs, act, billing }) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState([]);
  const [audio, setAudio] = useState('');
  const videos = project.assets.filter(
    (a) => a.role === 'footage' && a.kind === 'video' && a.status === 'ready' && a.has_audio,
  );
  const audios = project.assets.filter((a) => a.role === 'footage' && a.kind === 'audio' && a.status === 'ready');
  const running = jobs.find((j) => j.kind === 'multicam' && isActive(j));
  if (videos.length < 2 && !running) return null;

  const maxAngles = billing?.billing ? billing.limits.max_angles : 3;
  if (maxAngles < 2 && !running) {
    return (
      <p className="fine-print plan-hint">
        Syncing camera angles comes with Basic (two angles) and Pro (three).{' '}
        <a href="/dashboard?upgrade=basic#plan">Upgrade</a>
      </p>
    );
  }

  const toggle = (id) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < maxAngles ? [...p, id] : p));
  const start = async () => {
    await act(() => api.createMulticam(project.id, picked, audio || null));
    setOpen(false);
    setPicked([]);
    setAudio('');
  };

  if (running) {
    return (
      <p className="asset-status tone-busy multicam-busy" role="status">
        Syncing cameras{running.message ? `: ${running.message}` : '…'}
      </p>
    );
  }

  return (
    <div className="multicam-sync">
      {!open ? (
        <button className="button-secondary" onClick={() => setOpen(true)}>
          Sync camera angles
        </button>
      ) : (
        <fieldset className="multicam-form">
          <legend className="control-label">
            Videos of the same take, from different angles ({maxAngles === 2 ? 'two on your plan; Pro allows three' : '2 or 3'})
          </legend>
          {videos.map((v) => (
            <label key={v.id} className="check-row">
              <input type="checkbox" checked={picked.includes(v.id)} disabled={!picked.includes(v.id) && picked.length >= maxAngles}
                onChange={() => toggle(v.id)} />
              <span>{v.filename}</span>
            </label>
          ))}
          <label className="multicam-audio">
            <span className="control-label">Sound</span>
            <select value={audio} onChange={(e) => setAudio(e.target.value)}>
              <option value="">The clearest camera (picked for you)</option>
              {audios.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.filename} (separate recording)
                </option>
              ))}
            </select>
          </label>
          <p className="fine-print">
            They're lined up by their sound, trimmed to the part all of them recorded, and the edit can cut between them.
          </p>
          <div className="inspector-actions">
            <button className="button-primary" disabled={picked.length < 2} onClick={start}>
              Sync {picked.length || ''} {picked.length === 1 ? 'video' : 'videos'}
            </button>
            <button className="button-quiet" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </fieldset>
      )}
    </div>
  );
}
