// 화면 상태: 허브 /api/stream(SSE, 1초마다 바뀐 것만)을 받아 신호(signal)에 둔다. 화면은 신호만 읽는다.
import { signal, computed } from '@preact/signals'
import { get, post, store } from './lib.js'

export const hub = signal(null)              // /api/state 마지막 값
export const conn = signal({ state: 'connecting', since: Date.now(), tries: 0, msg: '' })   // ok · connecting · retry · login
export const clockSkew = signal(0)           // 내 시계 − 허브 시계(초)
export const tick = signal(Date.now())       // 1초마다(경과 시간 표시용)
export const view = signal('main')           // 가운데 칸: 'main' · 대화 이름(chat-N) · 작업자 이름
export const chat = signal('main')           // 지금 보는 대화(SW-29): 상태 흐름·/api/main* 이 이 대화를 가리킨다
export const panel = signal(store.get('stewart-panel', null))   // 오른쪽(폰은 아래 시트) 패널 탭: null(닫힘)·steps·plan·rules·session
export const drawer = signal(false)          // 폰 세션 서랍
export const sideOpen = signal(store.get('stewart-side', true))  // 데스크톱 사이드바 접기
export const toasts = signal([])

export const queue = computed(() => hub.value?.queue || [])
export const sessions = computed(() => hub.value?.sessions || [])
export const busy = computed(() => !!hub.value?.busy)

export function setPanel(p) { panel.value = p; store.set('stewart-panel', p) }
export function toggleSide() { sideOpen.value = !sideOpen.value; store.set('stewart-side', sideOpen.value) }

let toastId = 0
export function toast(text, kind = 'bad', ms = 7000) {
  const id = ++toastId
  toasts.value = [...toasts.value, { id, text, kind }]
  setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id) }, ms)
}

function accept(st) {
  if (!st || st.error) { if (st?.error === 'login') goLogin(); return }
  if (st.chat_gone) {   // 보던 대화가 닫혔다 → 메인으로
    if (view.value === chat.value) view.value = 'main'
    chat.value = 'main'; connect(); return
  }
  if (st.chat && st.chat !== chat.value) return   // 바꾸기 전 대화의 늦게 온 상태는 버린다
  hub.value = st
  if (st.server_time) clockSkew.value = Date.now() / 1000 - st.server_time
}
function goLogin() { conn.value = { state: 'login', since: Date.now(), tries: 0, msg: '로그인이 필요하다' }; setTimeout(() => { location.href = '/login' }, 800) }

export async function refresh() {
  try { accept(await get('/api/state?chat=' + encodeURIComponent(chat.value))) } catch (e) {
    if (e.kind === 'login') goLogin()
  }
}

// SSE 가 끊기면 브라우저가 스스로 다시 붙는다. 그동안 점을 노랑으로, 오래 못 붙으면 빨강 + 까닭
let es = null, lastMsg = 0
export function connect() {
  if (es) es.close()
  es = new EventSource('/api/stream?chat=' + encodeURIComponent(chat.value))
  es.onopen = () => { conn.value = { state: 'ok', since: Date.now(), tries: 0, msg: '' } }
  es.onmessage = ev => {
    lastMsg = Date.now()
    if (conn.value.state !== 'ok') conn.value = { state: 'ok', since: Date.now(), tries: 0, msg: '' }
    try { accept(JSON.parse(ev.data)) } catch (e) { /* 깨진 조각은 버린다 */ }
  }
  es.onerror = () => {
    const c = conn.value
    const tries = c.state === 'ok' ? 1 : c.tries + 1
    conn.value = { state: 'retry', since: c.state === 'ok' ? Date.now() : c.since, tries, msg: '허브와 연결이 끊겨 다시 붙는 중' }
    // 로그인이 풀렸는지 한 번 물어본다(SSE 는 401 을 알려 주지 않는다)
    get('/api/state?chat=' + encodeURIComponent(chat.value)).then(accept).catch(e => {
      if (e.kind === 'login') { es.close(); goLogin() }
    })
  }
}
// 허브는 바뀐 것이 없으면 SSE 를 보내지 않는다. 1시간(3600번)마다 허브가 스트림을 닫으므로 브라우저가 다시 붙는다
setInterval(() => {
  tick.value = Date.now()
  if (es && es.readyState === 2) connect()                       // 닫힌 채로 남았으면 다시
}, 1000)
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh() })
export const sinceLastMsg = () => Date.now() - lastMsg

// 대화 열기(메인·사람이 연 대화): 상태 흐름을 그 대화로 다시 붙인다
export function openChat(name) {
  const n = name || 'main'
  view.value = n; drawer.value = false
  if (chat.value !== n) { chat.value = n; hub.value = hub.value ? { ...hub.value, main_log: [], busy: false, live: null, tokens: null, todos: [], pending: [] } : hub.value; connect(); refresh() }
}
// 지금 보는 대화에 보내기(/api/main·/api/main/* 에 chat 을 붙인다)
export const cpost = (path, body) => post(path, { ...(body || {}), chat: chat.value })
export const isChatName = name => name === 'main' || (hub.value?.chats || []).some(c => c.name === name)
