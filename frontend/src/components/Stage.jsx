import { useEffect, useRef, useState } from 'react';
import { api, outputUrl } from '../lib/api';
import { isActive, timecode } from '../lib/format';
import { totalDuration } from '../lib/timelineEdit';
import LivePreview from './LivePreview';

/** Newest render job for a timeline version, previews or finals. */
function latestRender(jobs, version, preview) {
  return jobs.find(
    (j) => j.kind === 'render' && j.input?.timeline_version === version && j.input?.preview === preview,
  );
}

export default function Stage({ project, jobs, act, videoRef, liveRef, draft, looks, onTime }) {
  const timeline = project?.timeline;
  const version = timeline?.version;
  const data = draft || timeline?.data;
  const [mode, setMode] = useState(null); // 'live' | 'rendered' | null (automatic)
  const [lines, setLines] = useState([]);
  const lastTime = useRef(0);

  // Caption lines for whatever is on screen (saved or unsaved), for the live preview.
  useEffect(() => {
    if (!project?.id || !data?.captions) {
      setLines([]);
      return undefined;
    }
    let live = true;
    const t = setTimeout(() => {
      api.previewCaptions(project.id, data).then((l) => live && setLines(l)).catch(() => {});
    }, 300);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [project?.id, data]);

  useEffect(() => setMode(null), [version]);

  if (!project) return <div className="stage" aria-busy="true" />;

  if (!timeline) {
    return (
      <div className="stage stage-empty">
        <div className="frame" style={{ aspectRatio: '16 / 9' }}>
          <p>
            {project.assets.length === 0
              ? 'Add footage on the left, then describe your edit in the chat.'
              : !project.assets.some((a) => (a.role || 'footage') === 'footage' && a.status === 'ready' && a.transcript_status !== 'pending')
                ? 'Preparing your video. Once it’s ready, describe your edit in the chat and the preview plays here.'
                : 'Describe the edit you want in the chat. The preview plays here.'}
          </p>
        </div>
      </div>
    );
  }

  const output = data.output;
  const preview = latestRender(jobs, version, true);
  const final = latestRender(jobs, version, false);
  const playable = [final, preview].find((j) => j?.status === 'succeeded');
  const working = [preview, final].find(isActive);
  const failed = [preview, final].find((j) => j?.status === 'failed' && !playable);
  const effective = draft ? 'live' : mode || (playable ? 'rendered' : 'live');

  const report = (t) => {
    if (Math.abs(t - lastTime.current) >= 0.1) {
      lastTime.current = t;
      onTime(t);
    }
  };

  return (
    <div className="stage">
      <div className="stage-tabs" role="tablist" aria-label="Preview">
        <button role="tab" aria-selected={effective === 'live'} className={effective === 'live' ? 'is-on' : ''} onClick={() => setMode('live')}>
          Live
        </button>
        <button
          role="tab"
          aria-selected={effective === 'rendered'}
          className={effective === 'rendered' ? 'is-on' : ''}
          disabled={!!draft}
          title={draft ? 'Save your edits to render them' : undefined}
          onClick={() => setMode('rendered')}
        >
          Rendered
          {working && !draft ? ` (${Math.round((working.progress || 0) * 100)}%)` : ''}
        </button>
      </div>

      {effective === 'live' ? (
        <LivePreview ref={liveRef} project={project} timeline={data} lines={lines} looks={looks} onTime={report} />
      ) : (
        <div className="frame" style={{ aspectRatio: `${output.width} / ${output.height}` }}>
          {playable ? (
            <video
              key={playable.id}
              ref={videoRef}
              src={outputUrl(playable.id)}
              controls
              playsInline
              preload="metadata"
              onTimeUpdate={(e) => report(e.currentTarget.currentTime)}
            />
          ) : working ? (
            <div className="frame-message">
              <p>{working.input?.preview ? 'Rendering preview' : 'Exporting full quality'}</p>
              <progress value={working.progress} max={1} aria-label="Render progress" />
            </div>
          ) : failed ? (
            <div className="frame-message">
              <p className="tone-error">The render failed: {failed.error}</p>
              <button className="button-secondary" onClick={() => act(() => api.render(project.id, true, version))}>
                Try again
              </button>
            </div>
          ) : (
            <div className="frame-message">
              <button className="button-secondary" onClick={() => act(() => api.render(project.id, true, version))}>
                Render preview
              </button>
            </div>
          )}
        </div>
      )}

      <div className="stage-bar">
        <span className="stage-facts">
          <span>{draft ? 'Unsaved edits' : `Version ${version}`}</span>
          <span>{timecode(totalDuration(data.clips))}</span>
          <span>
            {output.width}×{output.height}
          </span>
          {!draft && effective === 'rendered' && playable && playable === preview && <span>Preview quality</span>}
        </span>
        <span className="stage-actions">
          {draft ? null : final?.status === 'succeeded' ? (
            <a className="button-primary" href={outputUrl(final.id)} download>
              Download video
            </a>
          ) : isActive(final) ? (
            <span className="tone-busy">Exporting {Math.round((final.progress || 0) * 100)}%</span>
          ) : (
            <button className="button-primary" onClick={() => act(() => api.render(project.id, false, version))}>
              Export full quality
            </button>
          )}
        </span>
      </div>
    </div>
  );
}
