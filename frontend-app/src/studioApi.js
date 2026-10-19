async function request(path, options = {}) {
  const response = await fetch(`/api/studio${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await response.json()
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail :
      Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join('；') : `HTTP ${response.status}`
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  return data
}

export const studioApi = {
  health: () => request('/health'),
  getSettings: () => request('/settings'),
  saveSettings: (settings) => request('/settings', { method: 'PUT', body: JSON.stringify(settings) }),
  testSettings: (settings) => request('/settings/test', { method: 'POST', body: JSON.stringify(settings) }),
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
