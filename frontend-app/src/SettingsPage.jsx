import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { studioApi } from './studioApi.js'

export default function SettingsPage() {
  const [saved, setSaved] = useState(null)
  const [form, setForm] = useState(null)
  const [providers, setProviders] = useState([])
  const [apiKey, setApiKey] = useState('')
  const [parameters, setParameters] = useState('{}')
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [stale, setStale] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const revisionRef = useRef(null)

  function acceptSettings(data) {
    setSaved(data)
    setForm(data.config)
    setProviders(data.providers)
    setParameters(JSON.stringify(data.config.request_parameters, null, 2))
    setApiKey('')
    setBusy(data.busy)
    setStale(false)
    revisionRef.current = data.revision
  }

  useEffect(() => {
    let active = true
    let timer
    async function refresh() {
      try {
        const data = await studioApi.getSettings()
        if (!active) return
        if (revisionRef.current === null) acceptSettings(data)
        else {
          setBusy(data.busy)
          setStale(data.revision !== revisionRef.current)
        }
      } catch (cause) {
        if (active) setError(cause.message)
      } finally {
        if (active) timer = setTimeout(refresh, 1000)
      }
    }
    void refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [])

  const preset = providers.find((item) => item.id === form?.provider)
  const keepsKey = saved?.has_api_key && form?.provider === saved.config.provider && form?.api_base === saved.config.api_base
  const supportsResponses = ['openai', 'azure'].includes(preset?.route)

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
    setNotice('')
  }

  function chooseProvider(id) {
    const next = providers.find((item) => item.id === id)
    setForm((current) => ({ ...current, provider: id, api_base: next.api_base, model: '', api_version: '',
      api_mode: id === 'openai' ? 'responses' : 'chat_completions' }))
    setParameters(id === 'ark' ? '{\n  "thinking": { "type": "disabled" }\n}' : '{}')
    setApiKey('')
    setNotice('')
  }

  function payload() {
    const extra = JSON.parse(parameters)
    if (!extra || Array.isArray(extra) || typeof extra !== 'object') throw new Error('扩展参数应为 JSON 对象。')
    return { ...form, request_parameters: extra, revision: saved.revision, ...(apiKey ? { api_key: apiKey } : {}) }
  }

  async function save(event) {
    event.preventDefault()
    if (busy || saving || testing || stale) return
    setError('')
    setNotice('')
    setSaving(true)
    try {
      const data = await studioApi.saveSettings(payload())
      acceptSettings(data)
      setNotice('配置已保存，后续绘图将使用此模型。')
    } catch (cause) {
      setError(cause.message)
      const data = await studioApi.getSettings().catch(() => null)
      if (data) {
        setBusy(data.busy)
        setStale(data.revision !== revisionRef.current)
      }
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    setError('')
    setNotice('')
    setTesting(true)
    try {
      const result = await studioApi.testSettings(payload())
      setNotice(`连接成功：图片识别、文本响应与工具调用均通过，用时 ${(result.latency_ms / 1000).toFixed(2)} 秒。`)
    } catch (cause) {
      setError(cause.message)
    } finally {
      setTesting(false)
    }
  }

  async function reload() {
    try {
      acceptSettings(await studioApi.getSettings())
      setError('')
      setNotice('')
    } catch (cause) {
      setError(cause.message)
    }
  }

  return (
    <div className="settings-page">
      <header className="topbar">
        <Link className="brand brand-link" to="/"><span className="brand-mark">✦</span><span>Draw Agent</span><small>Studio</small></Link>
        <Link className="return-link" to="/">← 返回画布</Link>
      </header>
      <main className="settings-layout">
        <aside className="settings-nav"><span className="settings-eyebrow">设置</span><span className="settings-nav-item">◈ 模型连接</span>
          <p>选择绘图使用的模型。主 Agent 和子 Agent 共用此连接，各自拥有独立的会话与上下文。</p>
        </aside>
        <section className="settings-content" aria-labelledby="settings-title">
          <div className="settings-heading"><div><span className="settings-eyebrow">模型连接</span><h1 id="settings-title">让想法连接画布</h1>
            <p>配置大模型服务，开始通过文字连续绘图。</p></div><span className="settings-badge">{saved?.configured ? '已配置' : '待配置'}</span></div>
          {error && <div className="settings-alert error-message" role="alert">{error}</div>}
          {notice && <div className="settings-alert success-message" role="status">{notice}</div>}
          {busy && <div className="settings-alert busy-message" role="status">绘图任务正在运行或排队，全部任务结束后可保存配置。</div>}
          {stale && <div className="settings-alert busy-message">配置已在其他页面更新。<button type="button" onClick={reload}>重新读取</button></div>}
          {!form ? <p className="settings-loading">正在读取配置…</p> : <form onSubmit={save}>
            <fieldset className="settings-card" disabled={saving || testing}>
              <div className="settings-card-title"><h2>服务配置</h2><span>{saved.source === 'saved' ? '已保存到此服务' : '当前环境配置'}</span></div>
              <div className="settings-fields">
                <label className="settings-field"><span>模型厂商</span><select aria-label="模型厂商" value={form.provider} onChange={(event) => chooseProvider(event.target.value)}>
                  {providers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select></label>
                <label className="settings-field"><span>模型名称{form.provider === 'azure' ? ' / 部署名' : ''}</span>
                  <input aria-label="模型名称" value={form.model} onChange={(event) => update('model', event.target.value)} required
                    placeholder={form.provider === 'litellm' ? 'provider/model' : '填写厂商提供的模型 ID'} />
                  <small>请选择同时支持图片输入与工具调用（function calling）的模型。</small></label>
                <label className="settings-field full-width"><span>API 地址 <em>Base URL</em></span>
                  <input aria-label="API 地址" type="url" value={form.api_base} onChange={(event) => update('api_base', event.target.value)}
                    placeholder="留空使用厂商默认地址" required={Boolean(preset?.requires_base)} />
                  <small>填写基础地址；Azure 填写资源地址，Ark 可填写推理或 Coding Plan 地址。</small></label>
                <label className="settings-field full-width"><span>API Key {preset?.optional_key && <em>可选</em>}{keepsKey && <span className="key-status">已配置</span>}</span>
                  <input aria-label="API Key" type="password" autoComplete="new-password" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setNotice('') }}
                    placeholder={keepsKey ? '留空保留已保存的 API Key' : '输入此连接的 API Key'} required={!keepsKey && !preset?.optional_key} />
                  <small>密钥由服务端加密保存。主 Agent 与全部子 Agent 使用同一 API Key。</small></label>
              </div>
              {form.provider === 'bedrock' && <p className="settings-help">可填写 Bedrock API Key，或使用服务端已配置的 AWS 凭据；在扩展参数中填写 aws_region_name。</p>}
              {form.provider === 'vertex_ai' && <p className="settings-help">使用服务端 Google Cloud 凭据；在扩展参数中填写 vertex_project 和 vertex_location。</p>}
              {form.provider === 'litellm' && <p className="settings-help">模型名称填写 LiteLLM 的 provider/model 格式，可接入其他支持的厂商。</p>}
              <details className="settings-advanced"><summary>高级设置</summary><div className="settings-fields">
                {supportsResponses && <label className="settings-field"><span>API 类型</span><select aria-label="API 类型" value={form.api_mode} onChange={(event) => update('api_mode', event.target.value)}>
                  <option value="chat_completions">Chat Completions</option><option value="responses">Responses</option>
                </select></label>}
                {form.provider === 'azure' && <label className="settings-field"><span>Azure API Version</span><input aria-label="Azure API Version" value={form.api_version}
                  onChange={(event) => update('api_version', event.target.value)} placeholder="填写资源所需的 API 版本" /></label>}
                <label className="settings-field full-width"><span>扩展请求参数 <em>JSON</em></span><textarea aria-label="扩展请求参数" value={parameters} onChange={(event) => { setParameters(event.target.value); setNotice('') }} spellCheck={false} />
                  <small>用于厂商的思考模式、区域等选项。连接凭据请使用专用字段或服务端云凭据。</small></label>
              </div></details>
            </fieldset>
            <div className="settings-footer"><p>测试当前表单的图片识别、文本响应与工具调用，保存后应用配置。</p><div>
              <button type="button" className="secondary-button" onClick={test} disabled={testing || saving || stale}>{testing ? '正在测试…' : '测试连接'}</button>
              <button type="submit" className="primary-button" disabled={busy || saving || testing || stale}>{saving ? '正在保存…' : '保存配置'}</button>
            </div></div>
          </form>}
        </section>
      </main>
    </div>
  )
}
