// 허브와 말하는 길(오류를 종류별로 나눠 화면에 알기 쉽게), 마크다운, 작은 도우미.
import { Marked } from 'marked'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import diff from 'highlight.js/lib/languages/diff'
import ini from 'highlight.js/lib/languages/ini'
import javascript from 'highlight.js/lib/languages/javascript'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'

for (const [n, l] of Object.entries({ python, bash, json, diff, ini, javascript, yaml, markdown })) hljs.registerLanguage(n, l)
hljs.registerAliases(['sh', 'shell', 'zsh', 'console'], { languageName: 'bash' })
hljs.registerAliases(['toml'], { languageName: 'ini' })
hljs.registerAliases(['py'], { languageName: 'python' })
hljs.registerAliases(['js', 'ts', 'jsx', 'tsx'], { languageName: 'javascript' })
hljs.registerAliases(['yml'], { languageName: 'yaml' })
hljs.registerAliases(['md'], { languageName: 'markdown' })

export const CSRF = document.querySelector('meta[name=csrf]')?.content || ''
// 계정 모드(U2-7)면 로그인한 사람 이름, 아니면 빈 글
export const USER = (document.querySelector('meta[name=user]')?.content || '').replace('__USER__', '')

export const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export function highlight(code, lang) {
  try {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
  } catch (e) { /* 색 없이 */ }
  return esc(code)
}

// 모델 답은 믿지 않는 글: 원시 HTML 은 글자로, 링크는 http(s)·상대 경로만
const marked = new Marked({
  gfm: true, breaks: true,
  renderer: {
    html({ text }) { return esc(text) },
    code({ text, lang }) {
      const l = (lang || '').trim().split(/\s+/)[0].toLowerCase()
      return `<pre><code class="hljs">${highlight(text, l)}</code></pre>`
    },
    link({ href, title, tokens }) {
      const inner = this.parser.parseInline(tokens)
      if (!/^(https?:\/\/|\/|#|\.\.?\/)/i.test(href || '')) return inner
      return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer"${title ? ` title="${esc(title)}"` : ''}>${inner}</a>`
    },
    image({ text }) { return esc(text ? `[그림: ${text}]` : '[그림]') },
  },
})
export const mdHtml = text => { try { return marked.parse(String(text || '')) } catch (e) { return `<pre>${esc(text)}</pre>` } }

// ── 허브 요청: 실패를 종류별로 ──
// kind: login(다시 들어가기) · token(화면이 낡음 → 새로고침) · net(허브에 닿지 않음 → 다시 보내기) · busy(409) · big(413) · server · bad
export class HubError extends Error {
  constructor(kind, msg, detail) { super(msg); this.kind = kind; this.detail = detail }
}
const KIND_MSG = {
  login: '로그인이 풀렸다 — 다시 들어가야 한다',
  token: '화면이 낡았다(허브가 다시 떴을 수 있다) — 새로고침',
  net: '허브에 닿지 않는다(허브가 꺼졌거나 망이 끊김) — 다시 보내기',
  big: '너무 크다(글·이미지 크기)',
  server: '허브 안에서 오류가 났다',
}
async function call(path, opt) {
  let r
  try { r = await fetch(path, { credentials: 'same-origin', ...opt }) } catch (e) { throw new HubError('net', KIND_MSG.net) }
  let body = null
  try { body = await r.json() } catch (e) { /* 몸 없음 */ }
  if (r.ok) return body || {}
  const err = body?.error, detail = body?.detail
  if (r.status === 401) { throw new HubError('login', KIND_MSG.login) }
  if (r.status === 403 && err === 'token') throw new HubError('token', KIND_MSG.token)
  if (r.status === 413) throw new HubError('big', KIND_MSG.big)
  if (r.status >= 500) throw new HubError('server', KIND_MSG.server, detail)
  throw new HubError(r.status === 409 ? 'busy' : 'bad', detail || err || `HTTP ${r.status}`, detail)
}
export const get = path => call(path, {})
export const post = (path, body) => call(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Stewart-Token': CSRF }, body: JSON.stringify(body || {}),
})

export const fmtK = n => n == null ? '—' : n >= 100000 ? Math.round(n / 1000) + 'k' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n)
export const fmtSecs = s => {
  s = Math.max(0, Math.round(s))
  return s < 60 ? `${s}초` : s < 3600 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${Math.floor(s / 3600)}시간 ${Math.floor(s % 3600 / 60)}분`
}
export const estTokens = chars => Math.round(chars / 3)

export const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v) } catch (e) { return d } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)) } catch (e) { /* 개인 창 */ } },
}
