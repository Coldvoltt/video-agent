import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, errorMessage, upgradeHint } from './lib/api';
import { assetColorMap, isActive } from './lib/format';
import TopBar from './components/TopBar';
import MediaBin from './components/MediaBin';
import Stage from './components/Stage';
import TimelineStrip from './components/TimelineStrip';
import ChatPanel from './components/ChatPanel';
import CaptionEditor from './components/CaptionEditor';
import ColourPanel from './components/ColourPanel';
import SoundPanel from './components/SoundPanel';
import StickerPanel from './components/StickerPanel';
import EffectPanel from './components/EffectPanel';
import ToolTabs from './components/ToolTabs';
import StartScreen from './components/StartScreen';
import { AtAGlance, ExportsPanel, HistoryPanel, LayersPanel, ScriptPanel } from './components/ProjectInfo';

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
  const [starting, setStarting] = useState(() => new URLSearchParams(window.location.search).has('new'));
  const [user, setUser] = useState(undefined); // undefined = checking, null = signed out
  const [billing, setBilling] = useState(null); // plan and vid-coins
  const [upgrade, setUpgrade] = useState(null); // plan that would unlock what the last error was about
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

  const loadProjects = useCallback(() => {
    api
      .listProjects()
      .then((list) => {
        setProjects(list);
        setProjectId((current) => (current && list.some((p) => p.id === current) ? current : list[0]?.id ?? null));
      })
      .catch((e) => {
        if (e?.response?.status === 401) {
          // signed out and the server needs an account: start fresh; signing in comes before editing
          setProjects([]);
          setProjectId(null);
        } else {
          setError(errorMessage(e));
        }
      });
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(false));
    api.looks().then(setLooks).catch(() => {});
    // who's here first: when the server needs an account, a signed-out visitor has no projects to list
    api
      .me()
      .then((r) => {
        setUser(r.user);
        if (r.user || !r.require_account) {
          loadProjects();
          loadBilling();
        }
      })
      .catch(() => {
        setUser(null);
        loadProjects();
      });
  }, [loadProjects]);

  const signedIn = (u) => {
    setUser(u);
    loadBilling();
    // Work started before signing in now belongs to the account: refresh the list, but don't switch
    // projects here (the start screen opens the new project itself once its upload is done).
    api.listProjects().then(setProjects).catch(() => {});
  };
  const signOut = async () => {
    await api.signOut().catch(() => {});
    window.location.href = '/';
  };

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

  const loadBilling = useCallback(() => {
    api.billing().then(setBilling).catch(() => setBilling(null));
  }, []);

  /** Run an API call, surface errors (with an upgrade link when a plan limit stopped it), then refresh. */
  const act = useCallback(
    async (fn) => {
      try {
        const out = await fn();
        await refresh();
        loadBilling();
        return out;
      } catch (e) {
        setError(errorMessage(e));
        setUpgrade(upgradeHint(e));
        return null;
      }
    },
    [refresh, loadBilling],
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
        jobs={jobs}
        user={user}
        billing={billing}
        onSignOut={signOut}
        onSelect={setProjectId}
        onCreate={createProject}
        onNew={() => setStarting(true)}
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
          {upgrade && (
            <a className="button-primary notice-upgrade" href={`/dashboard?upgrade=${upgrade}#plan`}>
              Upgrade to {upgrade === 'pro' ? 'Pro' : 'Basic'}
            </a>
          )}
          <button className="link-button" onClick={() => { setError(null); setUpgrade(null); }}>
            Dismiss
          </button>
        </p>
      )}

      {!projectId || starting ? (
        <StartScreen
          projects={projects}
          act={act}
          user={user}
          onSignedIn={signedIn}
          onOpen={(p) => {
            setProjects((list) => (list.some((x) => x.id === p.id) ? list : [p, ...list]));
            setStarting(false);
            setProjectId(p.id);
          }}
          onCancel={projectId ? () => setStarting(false) : null}
        />
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
            billing={billing}
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
              billing={billing}
            />
            <AtAGlance project={project} draft={draft} />
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
            <ToolTabs
              tabs={((data) => {
                // tools that change the timeline wait while there are unsaved hand edits
                const tool = (node) => (draft ? (
                  <p className="notice notice-inline caption-paused">
                    Save or discard your timeline edits first; then this tool works on the saved edit.
                  </p>
                ) : node);
                return [
                  { id: 'script', label: 'Script', tone: 'paper',
                    node: <ScriptPanel project={project} draft={draft} playhead={() => playhead} onSeek={seek} /> },
                  { id: 'captions', label: 'Captions', tone: 'yellow', count: data?.captions ? 'On' : null,
                    node: tool(<CaptionEditor project={project} act={act} onSeek={seek} billing={billing} />) },
                  { id: 'colour', label: 'Colour', tone: 'tangerine', count: data?.grade ? 'On' : null,
                    node: tool(<ColourPanel project={project} looks={looks} act={act} />) },
                  { id: 'sound', label: 'Sound', tone: 'blue', count: data?.audio?.length || null,
                    node: tool(<SoundPanel project={project} act={act} playhead={() => playhead} />) },
                  { id: 'stickers', label: 'Stickers', tone: 'pink', count: data?.stickers?.length || null,
                    node: tool(<StickerPanel project={project} act={act} playhead={() => playhead} />) },
                  { id: 'effects', label: 'Effects', tone: 'mint', count: data?.effects?.length || null,
                    node: tool(<EffectPanel project={project} act={act} playhead={() => playhead} />) },
                  { id: 'layers', label: 'Layers', tone: 'paper',
                    count: ['text', 'overlays', 'audio', 'broll', 'zooms', 'stickers', 'effects']
                      .reduce((n, k) => n + ((draft || data)?.[k]?.length || 0), 0) || null,
                    node: <LayersPanel project={project} draft={draft} onSeek={seek} onSelect={setSelectedClip} /> },
                  { id: 'history', label: 'History', tone: 'paper', count: project?.timeline_versions?.length || null,
                    node: <HistoryPanel project={project} act={act} /> },
                  { id: 'exports', label: 'Exports', tone: 'paper',
                    count: jobs.filter((j) => j.kind === 'render' && j.status === 'succeeded').length || null,
                    node: <ExportsPanel project={project} jobs={jobs} act={act} /> },
                ];
              })(project?.timeline?.data)}
            />
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

