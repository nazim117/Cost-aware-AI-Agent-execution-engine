const BASE = '/api';

async function request(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export const listProjects = () => request('GET', '/projects');
export const createProject = (name) => request('POST', '/projects', { name });
export const patchProject = (id, patch) => request('PATCH', `/projects/${id}`, patch);
export const deleteProject = (id) => request('DELETE', `/projects/${id}`);

export const chat = (projectId, sessionId, message) =>
  request('POST', '/chat', { project_id: projectId, session_id: sessionId, message });

export const ingestText = (projectId, source, text) =>
  request('POST', '/ingest', { project_id: projectId, source, text });

export const memorySearch = (projectId, q, k = 5) =>
  request('GET', `/memory/search?project_id=${encodeURIComponent(projectId)}&q=${encodeURIComponent(q)}&k=${k}`);

export const syncProject = (id) => request('POST', `/projects/${id}/sync`);
export const getSyncStatus = (id) => request('GET', `/projects/${id}/sync`);

export const listActions = (projectId, status) => {
  const qs = status ? `?status=${status}` : '';
  return request('GET', `/projects/${projectId}/actions${qs}`);
};
export const approveAction = (actionId) => request('POST', `/actions/${actionId}/approve`);
export const rejectAction = (actionId) => request('POST', `/actions/${actionId}/reject`);
export const retryAction = (actionId) => request('POST', `/actions/${actionId}/retry`);