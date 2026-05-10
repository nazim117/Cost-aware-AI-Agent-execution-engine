import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import * as api from './api.js';

function fmtDate(iso) {
  if (!iso) return 'never';
  return new Date(iso).toLocaleString();
}

// ─── Toast ────────────────────────────────────────────────────────────────

function Toast({ message, url, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 6000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div className={`toast ${url ? 'success' : ''}`}>
      <span className="toast-msg">{message}</span>
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="toast-link">
          open ↗
        </a>
      )}
      <button className="toast-close" onClick={onDismiss}>✕</button>
    </div>
  );
}

// ─── Sidebar ──────────────────────────────────────────────────────────────

function ProjectPane({ projects, activeId, onSelect, onRefresh, setToast }) {
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [editId, setEditId] = useState(null);
  const [editRefs, setEditRefs] = useState({ jira_project_key: '', github_repo: '' });

  async function handleCreate(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.createProject(newName.trim());
      setNewName('');
      await onRefresh();
    } catch (err) {
      setToast({ message: `Create failed: ${err.message}` });
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id, name) {
    if (!window.confirm(`Delete project "${name}"? This removes all its messages and memory.`)) return;
    try {
      await api.deleteProject(id);
      await onRefresh();
    } catch (err) {
      setToast({ message: `Delete failed: ${err.message}` });
    }
  }

  function openEdit(p) {
    setEditId(p.id);
    const refs = p.external_refs || {};
    setEditRefs({ jira_project_key: refs.jira_project_key || '', github_repo: refs.github_repo || '' });
  }

  async function saveEdit(id) {
    const patch = {};
    if (editRefs.jira_project_key || editRefs.github_repo) {
      patch.external_refs = {};
      if (editRefs.jira_project_key) patch.external_refs.jira_project_key = editRefs.jira_project_key.trim();
      if (editRefs.github_repo) patch.external_refs.github_repo = editRefs.github_repo.trim();
    }
    try {
      await api.patchProject(id, patch);
      setEditId(null);
      await onRefresh();
    } catch (err) {
      setToast({ message: `Save failed: ${err.message}` });
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="sidebar-logo">🧠</span>
        <span className="sidebar-title">Project Brain</span>
      </div>

      <div className="sidebar-list">
        {projects.length === 0 && (
          <p className="sidebar-empty">No projects yet.<br />Create one below.</p>
        )}

        {projects.map(p => (
          <div key={p.id}>
            <div
              className={`project-item ${activeId === p.id ? 'active' : ''}`}
              onClick={() => onSelect(p.id)}
            >
              <span className="project-name">{p.name}</span>
              <div className="item-actions">
                <button
                  className="icon-btn"
                  title="Edit integrations"
                  onClick={e => { e.stopPropagation(); openEdit(p); }}
                >⚙</button>
                <button
                  className="icon-btn danger"
                  title="Delete project"
                  onClick={e => { e.stopPropagation(); handleDelete(p.id, p.name); }}
                >✕</button>
              </div>
            </div>

            {editId === p.id && (
              <div className="edit-panel">
                <div className="edit-label">Integrations</div>
                <label className="field-label">Jira project key</label>
                <input
                  className="input"
                  value={editRefs.jira_project_key}
                  onChange={e => setEditRefs(r => ({ ...r, jira_project_key: e.target.value }))}
                  placeholder="e.g. KAN"
                />
                <label className="field-label">GitHub repo</label>
                <input
                  className="input"
                  value={editRefs.github_repo}
                  onChange={e => setEditRefs(r => ({ ...r, github_repo: e.target.value }))}
                  placeholder="e.g. org/repo"
                />
                <div style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
                  <button className="btn btn-primary btn-sm" onClick={() => saveEdit(p.id)}>Save</button>
                  <button className="btn btn-secondary btn-sm" onClick={() => setEditId(null)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      <form className="sidebar-footer" onSubmit={handleCreate}>
        <input
          className="input"
          style={{ flex: 1 }}
          value={newName}
          onChange={e => setNewName(e.target.value)}
          placeholder="New project…"
        />
        <button
          type="submit"
          className="btn btn-primary btn-sm"
          disabled={creating || !newName.trim()}
        >+</button>
      </form>
    </aside>
  );
}

// ─── Chat ─────────────────────────────────────────────────────────────────

function ChatPane({ projectId, onActionDrafted, setToast }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => { setMessages([]); }, [projectId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || busy || !projectId) return;
    setInput('');
    setBusy(true);
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    try {
      const res = await api.chat(projectId, 'default', text);
      setMessages(prev => [...prev, { role: 'assistant', content: res.reply }]);
      if (res.reply?.includes('Drafted action')) onActionDrafted();
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: `⚠ Error: ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  if (!projectId) {
    return (
      <div className="chat-pane chat-empty-state">
        <div className="chat-empty-icon">🧠</div>
        <div className="chat-empty-text">Select or create a project to start chatting.</div>
      </div>
    );
  }

  return (
    <div className="chat-pane">
      <div className="pane-header">
        <span className="pane-title">Chat</span>
      </div>

      <div className="chat-messages">
        {messages.map((m, i) => (
          <div key={i} className={`message ${m.role === 'user' ? 'user' : ''}`}>
            <div className={`avatar ${m.role === 'user' ? 'user' : 'ai'}`}>
              {m.role === 'user' ? '👤' : '🧠'}
            </div>
            <div className={`bubble ${m.role === 'user' ? 'user' : 'ai'}`}>
              <div className="prose">
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
            </div>
          </div>
        ))}

        {busy && (
          <div className="message">
            <div className="avatar ai">🧠</div>
            <div className="typing-indicator">
              <div className="dot" />
              <div className="dot" />
              <div className="dot" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-area">
        <textarea
          className="chat-textarea"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask anything… (Enter to send, Shift+Enter for newline)"
          rows={1}
          disabled={busy}
        />
        <button
          className={`btn send-btn ${busy || !input.trim() ? 'btn-secondary' : 'btn-primary'}`}
          onClick={send}
          disabled={busy || !input.trim()}
        >
          {busy ? '…' : 'Send →'}
        </button>
      </div>
    </div>
  );
}

// ─── Tools panel ──────────────────────────────────────────────────────────

function ToolsPane({ projectId, actionsKey, setToast }) {
  const [syncStatus, setSyncStatus] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [actions, setActions] = useState([]);
  const [ingestSource, setIngestSource] = useState('');
  const [ingestText, setIngestText] = useState('');
  const [ingesting, setIngesting] = useState(false);
  const [searchQ, setSearchQ] = useState('');
  const [searchK, setSearchK] = useState(5);
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (!projectId) { setSyncStatus(null); setActions([]); return; }
    api.getSyncStatus(projectId).then(setSyncStatus).catch(() => setSyncStatus(null));
    api.listActions(projectId).then(setActions).catch(() => setActions([]));
  }, [projectId, actionsKey]);

  useEffect(() => {
    setSearchResults([]);
    setSearchQ('');
  }, [projectId]);

  async function handleSync() {
    setSyncing(true);
    try {
      await api.syncProject(projectId);
      const s = await api.getSyncStatus(projectId);
      setSyncStatus(s);
      setToast({ message: 'Sync complete.' });
    } catch (err) {
      setToast({ message: `Sync failed: ${err.message}` });
    } finally {
      setSyncing(false);
    }
  }

  async function handleApprove(actionId) {
    try {
      const res = await api.approveAction(actionId);
      const url = res?.result?.url;
      setToast({ message: 'Comment posted.', url });
      setActions(prev => prev.filter(a => a.id !== actionId));
    } catch (err) {
      setToast({ message: `Approve failed: ${err.message}` });
    }
  }

  async function handleReject(actionId) {
    try {
      await api.rejectAction(actionId);
      setActions(prev => prev.filter(a => a.id !== actionId));
    } catch (err) {
      setToast({ message: `Reject failed: ${err.message}` });
    }
  }

  async function handleRetry(actionId) {
    try {
      await api.retryAction(actionId);
      const updated = await api.listActions(projectId);
      setActions(updated);
      setToast({ message: 'Action reset to pending — you can now approve it.' });
    } catch (err) {
      setToast({ message: `Retry failed: ${err.message}` });
    }
  }

  async function handleIngest(e) {
    e.preventDefault();
    if (!ingestText.trim() || !ingestSource.trim()) return;
    setIngesting(true);
    try {
      const res = await api.ingestText(projectId, ingestSource.trim(), ingestText.trim());
      setToast({ message: `Ingested ${res.chunks} chunks from "${ingestSource}".` });
      setIngestSource('');
      setIngestText('');
    } catch (err) {
      setToast({ message: `Ingest failed: ${err.message}` });
    } finally {
      setIngesting(false);
    }
  }

  async function handleSearch(e) {
    e.preventDefault();
    if (!searchQ.trim()) return;
    setSearching(true);
    try {
      const res = await api.memorySearch(projectId, searchQ.trim(), searchK);
      setSearchResults(res.results || []);
    } catch (err) {
      setToast({ message: `Search failed: ${err.message}` });
    } finally {
      setSearching(false);
    }
  }

  if (!projectId) return null;

  return (
    <aside className="tools-pane">
      <div className="tools-scroll">

        <div className="section">
          <div className="section-title">⟳ Sync</div>
          <p className="sync-meta">Last sync: {syncStatus?.last_synced_at ? fmtDate(syncStatus.last_synced_at) : 'never'}</p>
          <button className="btn btn-secondary btn-block" onClick={handleSync} disabled={syncing}>
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
        </div>

        <div className="section">
          <div className="section-title">
            ✎ Pending Actions
            {actions.filter(a => ['pending', 'failed'].includes(a.status)).length > 0 && (
              <span className="section-badge">{actions.filter(a => ['pending', 'failed'].includes(a.status)).length}</span>
            )}
          </div>
          {actions.filter(a => ['pending', 'failed'].includes(a.status)).length === 0 ? (
            <p className="no-actions">No pending actions.</p>
          ) : (
            actions.filter(a => ['pending', 'failed'].includes(a.status)).map(a => (
              <div key={a.id} className={`action-card action-card--${a.status}`}>
                <div className="action-type">{a.action_type} <span className="action-status">[{a.status}]</span></div>
                <div className="action-meta">{a.payload?.item_id} · {a.payload?.ref_key}</div>
                {a.status === 'failed' && a.payload?.error && (
                  <div className="action-error">{a.payload.error}</div>
                )}
                <div className="action-body">{a.payload?.body}</div>
                <div className="action-btns">
                  {a.status === 'pending' && (
                    <>
                      <button className="btn-approve" onClick={() => handleApprove(a.id)}>Approve</button>
                      <button className="btn-reject" onClick={() => handleReject(a.id)}>Reject</button>
                    </>
                  )}
                  {a.status === 'failed' && (
                    <button className="btn-approve" onClick={() => handleRetry(a.id)}>Retry</button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        <div className="section">
          <div className="section-title">↑ Ingest Text</div>
          <form onSubmit={handleIngest} style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
            <input
              className="input"
              value={ingestSource}
              onChange={e => setIngestSource(e.target.value)}
              placeholder="Source label (e.g. notes.md)"
            />
            <textarea
              className="input textarea"
              value={ingestText}
              onChange={e => setIngestText(e.target.value)}
              placeholder="Paste text to index…"
              rows={4}
            />
            <button
              type="submit"
              className="btn btn-primary btn-block"
              disabled={ingesting || !ingestText.trim() || !ingestSource.trim()}
            >
              {ingesting ? 'Ingesting…' : 'Ingest'}
            </button>
          </form>
        </div>

        <div className="section">
          <div className="section-title">⌕ Memory Search</div>
          <form onSubmit={handleSearch} style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
            <input
              className="input"
              value={searchQ}
              onChange={e => setSearchQ(e.target.value)}
              placeholder="Search query…"
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-3)', whiteSpace: 'nowrap' }}>Top k: {searchK}</span>
              <input type="range" min="1" max="10" value={searchK}
                onChange={e => setSearchK(+e.target.value)} style={{ flex: 1 }} />
            </div>
            <button
              type="submit"
              className="btn btn-secondary btn-block"
              disabled={searching || !searchQ.trim()}
            >
              {searching ? 'Searching…' : 'Search'}
            </button>
          </form>

          {searchResults.length > 0 && (
            <div className="search-results">
              {searchResults.map((hit, i) => (
                <div key={i} className="search-hit">
                  <div className="search-hit-meta">{hit.source} · {hit.score?.toFixed(3)}</div>
                  <div className="search-hit-text">{hit.text}</div>
                </div>
              ))}
            </div>
          )}
        </div>

      </div>
    </aside>
  );
}

// ─── App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [projects, setProjects] = useState([]);
  const [activeId, setActiveId] = useState(() => localStorage.getItem('projectId') || null);
  const [toast, setToast] = useState(null);
  const [actionsKey, setActionsKey] = useState(0);

  async function refreshProjects() {
    const list = await api.listProjects();
    setProjects(list);
    if (activeId && !list.find(p => p.id === activeId)) {
      setActiveId(null);
      localStorage.removeItem('projectId');
    }
  }

  useEffect(() => { refreshProjects(); }, []);

  function selectProject(id) {
    setActiveId(id);
    localStorage.setItem('projectId', id);
  }

  return (
    <div className="app">
      <ProjectPane
        projects={projects}
        activeId={activeId}
        onSelect={selectProject}
        onRefresh={refreshProjects}
        setToast={setToast}
      />
      <ChatPane
        projectId={activeId}
        onActionDrafted={() => setActionsKey(k => k + 1)}
        setToast={setToast}
      />
      <ToolsPane
        projectId={activeId}
        actionsKey={actionsKey}
        setToast={setToast}
      />
      {toast && (
        <Toast
          message={toast.message}
          url={toast.url}
          onDismiss={() => setToast(null)}
        />
      )}
    </div>
  );
}