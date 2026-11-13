import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { friendlyProgress, isActive } from '../lib/format';
import { briefKey } from './StartScreen';

const SUGGESTIONS = [
  'Cut the pauses and the ums',
  'Add B-roll where I mention things you can show',
  'Karaoke captions, with the important words in a comic font',
  'Make a 30-second vertical short of the best moment',
  'Give it a warm, punchy look with quick crossfades',
  'Spice it up: emphasise the key points and add sound effects',
];

/**
 * Why chat can't be used yet (null = ready). Chat stays on screen either way; this only
 * decides whether the box is editable and what the status line says.
 */
function waitingReason({ project, jobs, chatEnabled, pausedReason }) {
  if (!project) return { text: 'Loading your project…', busy: true };
  if (!chatEnabled) return { text: 'Chat needs an OpenAI key on the server (OPENAI_API_KEY in backend/.env).' };
  if (pausedReason) return { text: pausedReason };

  const footage = project.assets.filter((a) => (a.role || 'footage') === 'footage' && ['video', 'image', null].includes(a.kind));
  if (!footage.length) return { text: 'Add your video under Footage to start. Chat opens when it’s ready.' };
  if (footage.some((a) => a.status === 'ready' && (a.kind === 'video' || a.kind === 'image')
    && a.transcript_status !== 'pending')) return null;

  // Still preparing: show what's happening and how far along.
  const busy = footage.find((a) => a.status !== 'failed');
  if (!busy) return { text: 'Your video couldn’t be read. Try another file.' };
  const job = jobs.find((j) => ['ingest', 'stock'].includes(j.kind) && j.input?.asset_id === busy.id && isActive(j));
  const pct = job?.progress ? ` (${Math.round(job.progress * 100)}%)` : '';
  const step = job?.message || (busy.status === 'ready' ? 'Transcribing' : 'Preparing your video');
  return { text: `${step}${pct}. Chat opens when your video is ready.`, busy: true };
}

export default function ChatPanel({ project, jobs, messages, chatEnabled, act, onMessageSent, pausedReason }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const listRef = useRef(null);

  const agentJob = jobs.find((j) => j.kind === 'agent');
  const thinking = isActive(agentJob) ? agentJob : null;
  const lastFailed = agentJob?.status === 'failed' ? agentJob : null;
  const waiting = waitingReason({ project, jobs, chatEnabled, pausedReason });
  const locked = !!waiting || !!thinking;

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages.length, thinking?.message]);

  const send = async (message) => {
    const m = message.trim();
    if (!m || sending || locked) return;
    setSending(true);
    const ok = await act(() => api.chat(project.id, m));
    setSending(false);
    if (ok) {
      setText('');
      onMessageSent?.();
    }
  };

  // A brief written on the start screen waits here and is sent once the footage is ready.
  const [pending, setPending] = useState(null);
  useEffect(() => {
    try {
      setPending(project?.id ? localStorage.getItem(briefKey(project.id)) : null);
    } catch {
      setPending(null);
    }
  }, [project?.id]);
  const dropPending = () => {
    try {
      localStorage.removeItem(briefKey(project.id));
    } catch {
      /* nothing stored */
    }
    setPending(null);
  };
  useEffect(() => {
    if (pending && !locked && !sending) {
      const m = pending;
      dropPending();
      send(m);
    }
  }, [pending, locked, sending]); // eslint-disable-line react-hooks/exhaustive-deps

  const lastAssistantFailed =
    lastFailed && !messages.some((m) => m.role === 'assistant' && m.job_id === lastFailed.id);

  return (
    <aside className="panel chat" aria-label="Chat with the editor">
      <div className="chat-head">
        <span className="chat-avatar" aria-hidden="true">V</span>
        <div className="chat-who">
          <h2 className="panel-title">Edit with chat</h2>
          <span>{thinking ? 'Working on it…' : project?.name ? `Editing ${project.name}` : 'Your editor'}</span>
        </div>
      </div>

      <div className="messages" ref={listRef} aria-live="polite">
        {messages.length === 0 && !thinking && (
          <div className="chat-empty">
            <p>Say what you want, the way you’d brief an editor.</p>
            <ul className="suggestions">
              {SUGGESTIONS.map((s, i) => (
                <li key={s}>
                  <button className={`suggestion tone-${['yellow', 'blue', 'pink', 'mint', 'tangerine', 'yellow'][i]}`}
                    disabled={locked} onClick={() => send(s)}>
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={`message message-${m.role}`}>
            <p>{m.content}</p>
            {m.timeline_version && <span className="message-tag">Saved as version {m.timeline_version}</span>}
          </div>
        ))}

        {thinking && (
          <div className="message message-assistant message-working">
            <span className="pulse" aria-hidden="true" />
            <p>{thinking.status === 'queued' ? 'Starting' : friendlyProgress(thinking.message)}</p>
          </div>
        )}

        {lastAssistantFailed && (
          <div className="message message-assistant message-error">
            <p>That request didn’t go through: {lastFailed.error}</p>
          </div>
        )}
      </div>

      {pending && (
        <div className="pending-brief" role="status">
          <span className="control-label">Sends when your video is ready</span>
          <p>{pending}</p>
          <div className="inspector-actions">
            <button className="button-quiet" onClick={() => { setText(pending); dropPending(); }}>
              Edit it
            </button>
            <button className="button-quiet" onClick={dropPending}>
              Don’t send
            </button>
          </div>
        </div>
      )}

      {waiting && (
        <p className={`chat-status ${waiting.busy ? 'is-busy' : ''}`} role="status">
          {waiting.busy && <span className="pulse" aria-hidden="true" />}
          {waiting.text}
        </p>
      )}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <label className="visually-hidden" htmlFor="chat-input">
          Describe your edit
        </label>
        <textarea
          id="chat-input"
          rows={3}
          value={text}
          maxLength={4000}
          disabled={locked}
          placeholder={
            thinking
              ? 'The editor is working…'
              : waiting
                ? 'Chat opens when your video is ready'
                : 'e.g. Tighten it to under a minute and make it vertical'
          }
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send(text);
            }
          }}
        />
        <button className="send-button" disabled={!text.trim() || sending || locked} aria-label="Send">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </button>
      </form>
    </aside>
  );
}
