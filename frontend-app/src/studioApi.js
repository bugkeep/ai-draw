async function request(path, options = {}) {
  const response = await fetch(`/api/studio${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`)
  return data
}

export const studioApi = {
  health: () => request('/health'),
  listSessions: () => request('/sessions'),
  createSession: (title) => request('/sessions', { method: 'POST', body: JSON.stringify({ title }) }),
  getSession: (id) => request(`/sessions/${id}`),
  sendMessage: (id, message) => request(`/sessions/${id}/messages`, {
    method: 'POST', body: JSON.stringify({ message }),
  }),
  saveScene: (id, snapshot) => request(`/documents/${id}/scene`, {
    method: 'PUT', body: JSON.stringify(snapshot),
  }),
  history: (id, direction) => request(`/documents/${id}/history`, {
    method: 'POST', body: JSON.stringify({ direction }),
  }),
  cancelTask: (id) => request(`/tasks/${id}/cancel`, { method: 'POST' }),
  getTask: (id) => request(`/tasks/${id}`),
}

export function eventsUrl(documentId, after = 0) {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/studio/documents/${documentId}/events?after=${after}`
}
