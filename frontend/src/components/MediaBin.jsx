import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { isActive, timecode } from '../lib/format';
import StockSearch from './StockSearch';

const ACCEPT_FOOTAGE =
  '.mp4,.mov,.mkv,.webm,.avi,.m4v,.flv,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus,.png,.jpg,.jpeg,.webp,.bmp,.cube';
const ACCEPT_REFERENCE = '.mp4,.mov,.mkv,.webm,.avi,.m4v,.flv';
const ACCEPT_BROLL = `${ACCEPT_REFERENCE},.png,.jpg,.jpeg,.webp`;

function assetStatus(asset, job) {
  if (asset.status === 'failed') return { tone: 'error', text: asset.error || 'Couldn’t read this file.' };
  const working = job?.message ? `${job.message}${job.progress ? ` (${Math.round(job.progress * 100)}%)` : ''}` : null;
  if (asset.status !== 'ready') {
    return { tone: 'busy', text: working || (asset.source === 'youtube' ? 'Waiting to download' : 'Waiting to process') };
  }
  if (asset.role === 'reference') {
    if (asset.analysis_status === 'pending') return { tone: 'busy', text: working || 'Analysing the style' };
    if (asset.analysis_status === 'failed') return { tone: 'error', text: asset.error || 'Style analysis failed' };
    return { tone: 'muted', text: '' };
  }
  if (asset.role === 'generated') return { tone: 'muted', text: 'Made from a reference' };
  if (asset.role === 'broll') {
    if (asset.analysis_status === 'pending') return { tone: 'busy', text: working || 'Finding shots' };
    const n = asset.shots?.length;
    if (!n) return { tone: 'muted', text: asset.kind === 'image' ? 'Photo cutaway' : '' };
    const described = asset.shots.some((s) => s.description);
    return { tone: 'ok', text: `${n} ${n === 1 ? 'shot' : 'shots'}${described ? ', described' : ''}` };
  }
  switch (asset.transcript_status) {
    case 'pending':
      return { tone: 'busy', text: working || 'Transcribing' };
    case 'done':
      return { tone: 'ok', text: 'Transcript ready' };
    case 'failed':
      return { tone: 'warn', text: 'Transcript failed; the agent can’t find moments by what’s said' };
    case 'skipped':
      if (asset.kind === 'audio') return { tone: 'muted', text: 'For music or sound effects' };
      return { tone: 'muted', text: asset.has_audio ? 'Not transcribed' : 'No audio' };
    default:
      return { tone: 'muted', text: '' };
  }
}

function describe(asset) {
  const parts = [];
  if (asset.kind) parts.push({ video: 'Video', audio: 'Audio', image: 'Image', lut: 'Colour LUT' }[asset.kind]);
  if (asset.duration) parts.push(timecode(asset.duration));
  if (asset.width && asset.height) parts.push(`${asset.width}×${asset.height}`);
  return parts.join(', ');
}

/** Short facts from a reference's style profile, in plain words. */
function styleFacts(style) {
  if (!style) return [];
  const facts = [];
  const p = style.pacing || {};
  facts.push(p.cuts ? `Cuts every ${p.median_shot_seconds.toFixed(1)}s` : 'One continuous shot');
  const s = style.suggestions || {};
  if (s.aspect) facts.push(s.aspect);
  if (s.captions) facts.push(`${s.captions.style[0].toUpperCase()}${s.captions.style.slice(1)} captions`);
  if (s.punch_ins) facts.push('Punch-in zooms');
  if (style.audio?.tempo_bpm) facts.push(`Music ~${Math.round(style.audio.tempo_bpm)} BPM`);
  if (style.audio?.speech_wpm) facts.push(`${style.audio.speech_wpm} words/min`);
  if (style.visual?.energy) facts.push(`${style.visual.energy[0].toUpperCase()}${style.visual.energy.slice(1)} energy`);
  return facts;
}

function AssetRow({ asset, color, job, children }) {
  const status = assetStatus(asset, job);
  return (
    <li className="asset">
      <span className="swatch" style={{ background: color }} aria-hidden="true" />
      <div className="asset-body">
        <span className="asset-name" title={asset.filename || asset.source_url}>
          {asset.filename || asset.source_url || 'Untitled'}
        </span>
        <span className="asset-meta">{describe(asset)}</span>
        {status.text && <span className={`asset-status tone-${status.tone}`}>{status.text}</span>}
        {children}
      </div>
    </li>
  );
}

function AddMedia({ role, project, act, onUploading }) {
  const [url, setUrl] = useState('');
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef(null);
  const isRef = role === 'reference';
  const isBroll = role === 'broll';

  const uploadFiles = async (fileList) => {
    for (const file of Array.from(fileList)) {
      const key = `${role}-${file.name}-${file.size}-${Date.now()}`;
      onUploading((u) => [...u, { key, name: file.name, progress: 0, role }]);
      await act(() =>
        api.uploadAsset(
          project.id,
          file,
          (p) => onUploading((u) => u.map((x) => (x.key === key ? { ...x, progress: p } : x))),
          role,
        ),
      );
      onUploading((u) => u.filter((x) => x.key !== key));
    }
  };

  const addLink = async (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    const ok = await act(() => api.addYoutube(project.id, url.trim(), role));
    if (ok) setUrl('');
  };

  return (
    <>
      <div
        className={`dropzone ${dragging ? 'is-dragging' : ''} ${isRef ? 'dropzone-compact' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          uploadFiles(e.dataTransfer.files);
        }}
      >
        <button className={isRef || isBroll ? 'button-secondary' : 'button-primary'} onClick={() => fileInput.current?.click()}>
          {isRef ? 'Add a reference video' : isBroll ? 'Add B-roll' : 'Add files'}
        </button>
        <span className="dropzone-hint">
          {isRef
            ? 'A video whose style you like. It’s analysed, never cut into your edit.'
            : isBroll
              ? 'Cutaway clips or photos. Each clip is split into shots and described so the editor can match them to what you say.'
              : 'or drop footage, music, sound effects, images or .cube LUTs here'}
        </span>
        <input
          ref={fileInput}
          type="file"
          multiple={!isRef}
          accept={isRef ? ACCEPT_REFERENCE : isBroll ? ACCEPT_BROLL : ACCEPT_FOOTAGE}
          hidden
          onChange={(e) => {
            uploadFiles(e.target.files);
            e.target.value = '';
          }}
        />
      </div>
      {!isBroll && (
      <form className="link-form" onSubmit={addLink}>
        <label htmlFor={`yt-${role}`}>{isRef ? 'Reference YouTube link' : 'YouTube link'}</label>
        <div className="input-row">
          <input
            id={`yt-${role}`}
            type="url"
            inputMode="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://youtube.com/watch?v=…"
          />
          <button className="button-secondary" disabled={!url.trim()}>
            Add
          </button>
        </div>
        {!isRef && <p className="fine-print">Only add videos you own or have rights to edit.</p>}
      </form>
      )}
    </>
  );
}

function GenerateImage({ project, act, enabled }) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  if (!enabled) return null;
  const submit = async (e) => {
    e.preventDefault();
    if (prompt.trim().length < 3) return;
    setBusy(true);
    const ok = await act(() => api.generateBroll(project.id, prompt.trim()));
    setBusy(false);
    if (ok) setPrompt('');
  };
  return (
    <form className="link-form" onSubmit={submit}>
      <label htmlFor="gen-prompt">Generate a B-roll image</label>
      <div className="input-row">
        <input
          id="gen-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. steaming coffee on a desk, morning light"
          maxLength={500}
        />
        <button className="button-secondary" disabled={prompt.trim().length < 3 || busy}>
          {busy ? '…' : 'Create'}
        </button>
      </div>
      <p className="fine-print">AI-generated stills move with a slow push-in or pan when placed.</p>
    </form>
  );
}

export default function MediaBin({ project, jobs, colors, act, onAsk, canAsk, stockAvailable, stockProviders, aiImages }) {
  const [uploads, setUploads] = useState([]); // [{key, name, progress, role}]

  if (!project) return <aside className="panel media" aria-busy="true" />;

  const ingestJobs = Object.fromEntries(
    jobs.filter((j) => ['ingest', 'stock', 'genimage'].includes(j.kind) && isActive(j)).map((j) => [j.input?.asset_id, j]),
  );
  // Generated files (colour-match LUTs) belong to timeline versions, not to the user's media list.
  const footage = project.assets.filter((a) => !['reference', 'generated', 'broll'].includes(a.role));
  const brolls = project.assets.filter((a) => a.role === 'broll');
  const out = project.timeline?.data?.output;
  const orientation = out ? (out.height > out.width ? 'portrait' : out.height === out.width ? 'square' : 'landscape') : '';
  const references = project.assets.filter((a) => a.role === 'reference');
  const hasFootage = footage.some((a) => a.status === 'ready' && a.kind === 'video');

  const uploadRows = (role) =>
    uploads
      .filter((u) => u.role === role)
      .map((u) => (
        <li key={u.key} className="asset">
          <span className="swatch swatch-pending" aria-hidden="true" />
          <div className="asset-body">
            <span className="asset-name">{u.name}</span>
            <span className="asset-status tone-busy">Uploading {Math.round(u.progress * 100)}%</span>
            <progress value={u.progress} max={1} aria-label={`Uploading ${u.name}`} />
          </div>
        </li>
      ));

  return (
    <aside className="panel media" aria-label="Media">
      <h2 className="panel-title">Footage</h2>
      <AddMedia role="footage" project={project} act={act} onUploading={setUploads} />
      <ul className="asset-list">
        {uploadRows('footage')}
        {footage.map((a) => (
          <AssetRow key={a.id} asset={a} color={colors[a.id]} job={ingestJobs[a.id]} />
        ))}
      </ul>
      {footage.length === 0 && !uploads.some((u) => u.role === 'footage') && (
        <p className="empty-hint">Your footage shows up here. Add at least one video to start editing.</p>
      )}

      <h2 className="panel-title section-gap">B-roll</h2>
      <AddMedia role="broll" project={project} act={act} onUploading={setUploads} />
      <ul className="asset-list">
        {uploadRows('broll')}
        {brolls.map((a) => (
          <AssetRow key={a.id} asset={a} color={colors[a.id]} job={ingestJobs[a.id]}>
            {(a.source === 'pexels' || a.source === 'pixabay') && a.source_url && (
              <a className="stock-credit" href={a.source_url} target="_blank" rel="noreferrer">
                View on {a.source === 'pexels' ? 'Pexels' : 'Pixabay'}
              </a>
            )}
            {a.source === 'ai' && <span className="asset-status tone-muted">AI-generated</span>}
          </AssetRow>
        ))}
      </ul>
      <StockSearch project={project} available={stockAvailable} providers={stockProviders} act={act}
        defaultOrientation={orientation} />
      <GenerateImage project={project} act={act} enabled={aiImages} />

      <h2 className="panel-title section-gap">Style references</h2>
      <AddMedia role="reference" project={project} act={act} onUploading={setUploads} />
      <ul className="asset-list">
        {uploadRows('reference')}
        {references.map((a) => {
          const facts = styleFacts(a.style);
          const name = a.filename || 'the reference';
          return (
            <AssetRow key={a.id} asset={a} color="var(--line-strong)" job={ingestJobs[a.id]}>
              {a.style && (
                <div className="style-card">
                  {a.style.visual?.summary && <p className="style-summary">{a.style.visual.summary}</p>}
                  <ul className="style-facts">
                    {facts.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                  <button
                    className="button-secondary"
                    disabled={!canAsk || !hasFootage}
                    title={!hasFootage ? 'Add footage to edit first' : undefined}
                    onClick={() =>
                      onAsk(
                        `Edit my video in the style of the reference "${name}": match its format, pacing, captions and colour.`,
                      )
                    }
                  >
                    Edit in this style
                  </button>
                </div>
              )}
            </AssetRow>
          );
        })}
      </ul>
    </aside>
  );
}
