import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, errorMessage } from './lib/api';
import { assetColorMap, isActive } from './lib/format';
import TopBar from './components/TopBar';
import MediaBin from './components/MediaBin';
import Stage from './components/Stage';
import TimelineStrip from './components/TimelineStrip';
import ChatPanel from './components/ChatPanel';
import CaptionEditor from './components/CaptionEditor';
import ColourPanel from './components/ColourPanel';
import SoundPanel from './components/SoundPanel';

const POLL_MS = 1500;

function storedProjectId() {
  try {
    return localStorage.getItem('projectId');
  } catch {
    return null;
  }
}

export default function App() {
  const [health, setHealth] = useState(null); // null = checking, false = unreachable
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(storedProjectId);
  const [project, setProject] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState(null); // unsaved hand edits (timeline JSON)
  const [draftStack, setDraftStack] = useState([]); // earlier draft states, for Ctrl+Z
  const [selectedClip, setSelectedClip] = useState(null);
  const [playhead, setPlayhead] = useState(0);
  const [saving, setSaving] = useState(false);
  const [looks, setLooks] = useState([]);
  const videoRef = useRef(null);
  const liveRef = useRef(null);

  // ---- loading

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(false));
    api.looks().then(setLooks).catch(() => {});
    api
      .listProjects()
      .then((list) => {
        setProjects(list);
        setProjectId((current) => (current && list.some((p) => p.id === current) ? current : list[0]?.id ?? null));
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(() => {
    try {
      if (projectId) localStorage.setItem('projectId', projectId);
    } catch {
      /* ignore */
    }
  }, [projectId]);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const [p, j, m] = await Promise.all([api.getProject(projectId), api.listJobs(projectId), api.messages(projectId)]);
      setProject(p);
      setJobs(j);
      setMessages(m);
    } catch (e) {
      if (e?.response?.status === 404) {
        setProjectId(null);
        setProject(null);
      } else {
        setError(errorMessage(e));
      }
    }
  }, [projectId]);

  useEffect(() => {
    setProject(null);
    setJobs([]);
    setMessages([]);
    setDraft(null);
    setDraftStack([]);
    setSelectedClip(null);
    refresh();
  }, [refresh]);

  // Poll while anything is in flight; one extra refresh picks up the final state.
  const busy = jobs.some(isActive);
  useEffect(() => {
    if (!busy) return undefined;
    const t = setInterval(refresh, POLL_MS);
    return () => {
      clearInterval(t);
      refresh();
    };
  }, [busy, refresh]);

  // ---- actions

  const createProject = async (name) => {
    try {
      const p = await api.createProject(name);
      setProjects((list) => [p, ...list]);
      setProjectId(p.id);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const deleteProject = async () => {
    if (!project || !window.confirm(`Delete “${project.name}” and all its media and renders?`)) return;
    try {
      await api.deleteProject(project.id);
      const rest = projects.filter((p) => p.id !== project.id);
      setProjects(rest);
      setProjectId(rest[0]?.id ?? null);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  /** Run an API call, surface errors, then refresh project state. */
  const act = useCallback(
    async (fn) => {
      try {
        const out = await fn();
        await refresh();
        return out;
      } catch (e) {
        setError(errorMessage(e));
        return null;
      }
    },
    [refresh],
  );

  const colors = useMemo(() => assetColorMap(project?.assets), [project?.assets]);

  const seek = (seconds) => {
    if (liveRef.current) return liveRef.current.seek(seconds);
    const v = videoRef.current;
    if (v) {
      v.currentTime = seconds;
      v.play().catch(() => {});
    }
  };

  // ---- hand edits: a local draft with its own undo, saved as one version

  const changeDraft = (next) => {
    setDraftStack((stack) => [...stack, draft ?? project.timeline.data].slice(-100));
    setDraft(next);
  };
  const discardDraft = () => {
    setDraft(null);
    setDraftStack([]);
  };
  const saveDraft = async () => {
    setSaving(true);
    const ok = await act(() => api.saveTimeline(project.id, draft, 'Edited by hand', true));
    setSaving(false);
    if (ok) discardDraft();
  };
  const undo = () => {
    if (draft) {
      if (draftStack.length <= 1) return discardDraft();
      setDraft(draftStack[draftStack.length - 1]);
      setDraftStack((stack) => stack.slice(0, -1));
      return undefined;
    }
    if (project?.history?.can_undo) act(() => api.undo(project.id));
    return undefined;
  };
  const redo = () => {
    if (!draft && project?.history?.can_redo) act(() => api.redo(project.id));
  };

  useEffect(() => {
    const onKey = (e) => {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);
      if (typing || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'z') return;
      e.preventDefault();
      if (e.shiftKey) redo();
      else undo();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  return (
    <div className="app">
      <TopBar
        projects={projects}
        project={project}
        projectId={projectId}
        onSelect={setProjectId}
        onCreate={createProject}
        onDelete={deleteProject}
      />

      {health === false && (
        <p className="notice notice-error" role="alert">
          The editor server isn’t responding. Start it from <code>backend/</code> with <code>uvicorn api:app</code> and{' '}
          <code>python -m editor.worker</code>.
        </p>
      )}
      {health && !health.openai_key && (
        <p className="notice">
          Transcription and chat are off: add <code>OPENAI_API_KEY</code> to <code>backend/.env</code> and restart the
          server.
        </p>
      )}
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
          <button className="link-button" onClick={() => setError(null)}>
            Dismiss
          </button>
        </p>
      )}

      {!projectId ? (
        <main className="welcome">
          <h1>Edit by describing it.</h1>
          <p>
            Add your footage or a YouTube link, then tell the editor what you want: a 30-second vertical short, the best
            three moments, no more dead air.
          </p>
          <NewProjectForm onCreate={createProject} />
        </main>
      ) : (
        <main className="workspace">
          <MediaBin
            project={project}
            jobs={jobs}
            colors={colors}
            act={act}
            canAsk={!!health?.openai_key && !draft && !jobs.some((j) => j.kind === 'agent' && isActive(j))}
            stockAvailable={!!health?.stock}
            stockProviders={health?.stock_providers || []}
            aiImages={!!health?.openai_key}
            onAsk={(message) => act(() => api.chat(project.id, message))}
          />
          <section className="center" aria-label="Preview and timeline">
            <Stage
              project={project}
              jobs={jobs}
              act={act}
              videoRef={videoRef}
              liveRef={liveRef}
              draft={draft}
              looks={looks}
              onTime={setPlayhead}
            />
            <TimelineStrip
              project={project}
              colors={colors}
              act={act}
              onSeek={seek}
              draft={draft}
              onDraftChange={changeDraft}
              onSaveDraft={saveDraft}
              onDiscardDraft={discardDraft}
              saving={saving}
              selectedId={selectedClip}
              onSelect={setSelectedClip}
              playhead={() => playhead}
              onUndo={undo}
              onRedo={redo}
            />
            {draft ? (
              <p className="notice notice-inline caption-paused">
                Save or discard your timeline edits to change colour or captions.
              </p>
            ) : (
              <>
                <ColourPanel project={project} looks={looks} act={act} />
                <SoundPanel project={project} act={act} playhead={() => playhead} />
                <CaptionEditor project={project} act={act} onSeek={seek} />
              </>
            )}
          </section>
          <ChatPanel
            project={project}
            jobs={jobs}
            messages={messages}
            chatEnabled={!!health?.openai_key}
            pausedReason={draft ? 'Save or discard your timeline edits before asking the editor for changes.' : null}
            act={act}
            onMessageSent={refresh}
          />
        </main>
      )}
    </div>
  );
}

function NewProjectForm({ onCreate }) {
  const [name, setName] = useState('');
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onCreate(name.trim());
      }}
    >
      <label className="visually-hidden" htmlFor="first-project">
        Project name
      </label>
      <input
        id="first-project"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Project name, e.g. Studio tour vlog"
        maxLength={200}
      />
      <button className="button-primary" disabled={!name.trim()}>
        Create project
      </button>
    </form>
  );
}
