// stewart 허브 화면 — 틀: 데스크톱은 [사이드바 | 대화 | (필요할 때) 작업 내역], 폰은 한 화면(서랍·아래 시트)
import './polyfills.js'
import './copy.js'
import { render, Component } from 'preact'
import { useEffect, useRef, useState, useLayoutEffect } from 'preact/hooks'
import './app.css'
import { get, post, fmtK, estTokens } from './lib.js'
import { hub, view, panel, setPanel, drawer, sideOpen, toggleSide, toasts, connect, refresh, sessions, toast, chat, cpost, openChat } from './state.js'
import { Thread, Md, TodoItems } from './thread.jsx'
import { Approvals, AskSheet, Composer } from './dock.jsx'
import { Sidebar, ConnDot, ModelDot } from './side.jsx'
import { Panel } from './panel.jsx'
import { useWorker, WorkerThread, WorkerControls, WorkerInfo } from './worker.jsx'

// 한 칸이 그리다 깨져도 화면 전체가 멈추지 않게: 그 칸만 "못 그렸다 + 다시"로 바꾼다(승인 칸이 안 보이던 일의 재발 막기)
class Guard extends Component {
  state = { err: null }
  componentDidCatch(err) { this.setState({ err }); console.error('화면 칸 오류', this.props.name, err) }
  render() {
    if (!this.state.err) return this.props.children
    return <div class="border border-bad/50 rounded-xl px-3 py-2 my-2 text-[13px]">
      <span class="text-bad">{this.props.name} 칸을 그리다 오류: {String(this.state.err.message || this.state.err)}</span>
      <button class="btn ml-2 !py-0.5" onClick={() => this.setState({ err: null })}>다시 그리기</button>
      <span class="text-mute ml-2">(이 칸 밖은 그대로 쓸 수 있다)</span></div>
  }
}

function CtxBar() {
  const t = hub.value?.tokens
  if (!t || !t.window) return null
  const used = t.last_prompt || 0, pct = Math.min(100, Math.round(used / t.window * 100))
  const mark = t.compact_at ? Math.min(100, t.compact_at / t.window * 100) : null
  return (
    <button class="ml-auto flex items-center gap-2 text-xs text-mute" title={`지금 문맥 ${used}토큰 / 창 ${t.window}` + (t.compact_at ? ` · ${Math.round(t.compact_at)}에서 오래된 도구 결과를 줄인다` : '')}
      onClick={() => setPanel(panel.value === 'tokens' ? null : 'tokens')}>
      <span class="max-md:hidden">문맥 {fmtK(used)} / {fmtK(t.window)}</span>
      <span class="relative w-36 max-md:w-16 h-1.5 rounded-full bg-line overflow-hidden">
        <i class="absolute inset-y-0 left-0 rounded-full" style={`width:${pct}%;background:${pct > 85 ? 'var(--c-bad)' : 'var(--c-accent)'}`} />
        {mark != null && <b class="absolute -inset-y-0.5 w-0.5 bg-warn" style={`left:${mark}%`} />}
      </span>
      <span>{pct}%</span>
    </button>
  )
}

// [새 대화]: 지금 대화는 기록에 남기고(지난 세션에서 다시 연다) 빈 문맥으로 — /clear·/reset 과 같다. 확인은 단추에만
async function newMain() {
  if (!confirm('지금 대화를 닫고 새로 시작할까요?\n(지금 대화는 기록에 남아 지난 세션 목록에서 다시 열 수 있다)')) return
  try { await cpost('/api/main/new', {}); toast('새 대화를 시작했다'); refresh() } catch (e) { toast('새로 못 함: ' + e.message) }
}
function NewMain() {
  const busy = hub.value?.busy
  return <button class="btn btn-ghost !px-2.5 text-xs flex-none" disabled={busy} onClick={newMain}
    title={busy ? '메인이 일하는 중 — 멈춘 뒤' : '새 대화(/clear): 지금 대화는 기록에 남는다'}>새 대화</button>
}

function Prior() {
  // 이어 받은(다시 연) 메인의 앞 대화: 접힌 채로, 펼치면 뒤에서부터 80줄씩
  const run = hub.value?.main_run
  const [p, setP] = useState(null), [open, setOpen] = useState(false)
  useEffect(() => {
    setP(null); setOpen(false)
    if (run) get('/api/session/conversation?name=' + encodeURIComponent(chat.value) + '&prior=1&limit=80').then(r => setP(r.total ? r : null)).catch(() => {})
  }, [run])
  const note = hub.value?.resume_note
  if (!p && !note) return null
  async function more() {
    const r = await get(`/api/session/conversation?name=${encodeURIComponent(chat.value)}&prior=1&before=${p.start}&limit=80`)
    setP({ ...p, start: r.start, items: [...r.items, ...p.items] })
  }
  return (
    <div class="border border-dashed border-line rounded-[10px] px-3 py-2 mb-4 text-[13px] text-mute">
      {p && <button class="w-full text-left" onClick={() => setOpen(!open)}>{open ? '▾' : '▸'} 앞 대화 {p.total}줄(이어 받음) — {open ? '접기' : '펼쳐 보기'}</button>}
      {note && <div class="mt-0.5">ⓘ {note}</div>}
      {open && p && <div class="mt-2 text-fg text-[14px] border-t border-line pt-2 max-h-[60vh] overflow-auto">
        {p.start > 0 && <button class="btn btn-ghost w-full mb-2" onClick={more}>앞 {p.start}줄 더 보기</button>}
        {p.items.map((it, i) => it.kind === 'assistant' ? <div key={i} class="my-2"><Md text={it.text} /></div>
          : <div key={i} class={'my-1 whitespace-pre-wrap ' + (it.kind === 'user' ? 'font-semibold' : 'text-mute text-[13px]')}>{it.kind === 'user' ? '사람: ' : it.kind === 'tool' ? '⏺ ' : ''}{it.text.slice(0, 2000)}</div>)}
      </div>}
    </div>
  )
}

// 맨 아래에 있었으면 새 글이 와도 맨 아래를 따라간다
function useStick(dep) {
  const ref = useRef(null), atEnd = useRef(true)
  const onScroll = () => { const b = ref.current; atEnd.current = b.scrollTop + b.clientHeight >= b.scrollHeight - 40 }
  useLayoutEffect(() => { const b = ref.current; if (b && atEnd.current) b.scrollTop = b.scrollHeight })
  return [ref, onScroll]
}

// 갈래(지금 대화를 복사해 새 대화로, 이후 서로 독립)·닫기(사람이 연 대화만, 기록은 지난 대화에)
async function forkChat() {
  try { const r = await cpost('/api/chat/new', { how: 'fork', from: chat.value }); openChat(r.chat); toast(`갈래를 열었다: ${r.title}`, 'ok', 4000) } catch (e) { toast('갈래 못 엶: ' + e.message) }
}
async function closeChat(name) {
  if (!confirm('이 대화를 닫을까요?\n(기록은 남아 지난 대화에서 다시 열 수 있다)')) return
  try { await post('/api/chat/close', { chat: name }); openChat('main') } catch (e) { toast('못 닫음: ' + e.message) }
}

function Center() {
  const v = view.value
  const conv = (hub.value?.chats || []).find(c => c.name === v)
  const isMain = v === 'main' || !!conv          // 대화(메인·사람이 연 대화)는 모두 같은 채팅 칸
  const st = hub.value
  const w = useWorker(isMain ? '' : v)
  const [ref, onScroll] = useStick()
  useEffect(() => { if (isMain && chat.value !== v) openChat(v) }, [v])
  const title = v === 'main' ? '메인' : conv ? conv.title : `작업자 ${v}`
  const main = sessions.value.find(s => s.role === 'main')
  return (
    <main class="flex flex-col min-w-0 h-full relative">
      <div class="flex items-center gap-2.5 px-5 max-md:px-3 py-2.5 border-b border-line bg-bg/85 backdrop-blur">
        <button class="btn btn-ghost !px-2 md:hidden" onClick={() => { drawer.value = true }} aria-label="세션 서랍">☰</button>
        <button class="btn btn-ghost !px-2 max-md:hidden" onClick={toggleSide} title="사이드바 접기·펴기">☰</button>
        <span class="font-[650] truncate">{title}</span>
        {isMain && main?.workdir && <span class="chip max-lg:hidden truncate max-w-64" title={main.workdir}>{main.workdir.split('/').slice(-1)[0]}</span>}
        <span class="max-md:hidden flex gap-1.5"><ConnDot /><ModelDot /></span>
        <span class="md:hidden flex gap-1"><ConnDot withText={false} /><ModelDot withText={false} /></span>
        {!isMain && <WorkerControls name={v} />}
        {isMain ? <CtxBar /> : <span class="ml-auto" />}
        {isMain && <NewMain />}
        {isMain && <button class="btn btn-ghost !px-2.5 text-xs flex-none max-md:hidden" disabled={st?.busy} onClick={forkChat} title="지금 대화를 복사해 새 대화로(이후 서로 독립)">갈래</button>}
        {conv && v !== 'main' && <button class="btn btn-ghost !px-2.5 text-xs flex-none" onClick={() => closeChat(v)} title="이 대화 닫기(기록은 남는다)">닫기</button>}
        <button class={'btn btn-ghost !px-2.5 ' + (hub.value?.tokens?.window ? '' : 'ml-auto')} onClick={() => setPanel(panel.value ? null : (isMain ? 'steps' : 'session'))}
          title="작업 내역 패널">{panel.value ? '내역 ▸' : '◂ 내역'}</button>
      </div>
      <div class="flex-1 overflow-auto scroll-thin" ref={ref} onScroll={onScroll}>
        <div class="max-w-[820px] mx-auto px-6 max-md:px-3.5 pt-6 pb-10 [overflow-wrap:anywhere]">
          <Guard key={v} name="대화">{isMain ? <Thread log={st?.main_log || []} prior={<Prior />} /> : <WorkerThread name={v} {...w} />}</Guard>
        </div>
      </div>
      <div class="pb-4 max-md:pb-0 safe-b pt-1">
        <div class="max-w-[820px] mx-auto px-6 max-md:px-2.5">
          {isMain && <Guard name="금지"><BanStrip /></Guard>}
          {isMain && <Guard name="할 일"><TodoStrip /></Guard>}
          <Guard name="승인">{/* 승인 칸이 깨지면 최소 칸(허용·거부)은 Approvals 안에서 건마다 따로 */}<Approvals /></Guard>
          <Guard key={'c' + v} name="입력"><Composer target={isMain ? 'main' : v} /></Guard>
          <button class="md:hidden w-full text-center text-xs text-mute pt-1.5" onClick={() => setPanel(panel.value ? null : 'steps')}>▴ 작업 내역</button>
        </div>
      </div>
      <Right worker={!isMain ? w : null} />
    </main>
  )
}

function Right({ worker }) {
  if (!panel.value) return null
  const extra = worker ? { tab: ['session', '작업자'], body: <WorkerInfo detail={worker.detail} /> } : null
  if (!extra && panel.value === 'session') setTimeout(() => setPanel('steps'))
  // 데스크톱: 오른쪽 칸(부모 grid 밖으로 고정) · 폰: 아래 시트
  return (
    <>
      <div class="md:hidden fixed inset-0 z-30 bg-black/45" onClick={() => setPanel(null)} />
      <aside class="bg-panel flex flex-col min-w-0 z-30
        md:fixed md:top-0 md:right-0 md:bottom-0 md:w-[380px] md:border-l md:border-line
        max-md:fixed max-md:inset-x-0 max-md:bottom-0 max-md:h-[72vh] max-md:rounded-t-2xl max-md:shadow-card">
        <Guard name="작업 내역"><Panel extra={extra} /></Guard>
      </aside>
    </>
  )
}

// 일하는 동안 입력 칸 바로 위 고정 줄: "◐ 지금: <하는 중> · 3/7" — 누르면 전체 목록
function TodoStrip() {
  const st = hub.value
  const todos = st?.todos || []
  const [open, setOpen] = useState(false)
  if (!todos.length) return null
  const doing = todos.find(t => t.status === 'in_progress')
  const done = todos.filter(t => t.status === 'done').length
  if (!st.busy && done === todos.length) return null
  return (
    <div class="mb-2 border border-line rounded-xl bg-panel shadow-card text-[13px]">
      <button class="w-full flex items-center gap-2 px-3 py-1.5 text-left" onClick={() => setOpen(!open)}>
        <span class="text-accent">{doing ? '◐' : '☐'}</span>
        <span class="truncate flex-1 min-w-0">{doing ? <><span class="text-mute">지금:</span> <b>{doing.content}</b></> : <span class="text-mute">다음: {(todos.find(t => t.status === 'pending') || {}).content}</span>}</span>
        <span class="text-mute flex-none">{done}/{todos.length} {open ? '▾' : '▴'}</span>
      </button>
      {open && <div class="px-3 pb-2 max-h-[40vh] overflow-auto border-t border-soft pt-1.5"><TodoItems todos={todos} compact /></div>}
    </div>
  )
}

// 되짚기가 건 금지(다시 하지 않기로 한 호출): 보이고, 사람이 푼다
function BanStrip() {
  const bans = hub.value?.bans || []
  const [open, setOpen] = useState(false)
  if (!bans.length) return null
  async function unban(id) { try { await cpost('/api/main/unban', { id }); refresh() } catch (e) { toast('못 풂: ' + e.message) } }
  return (
    <div class="mb-2 border border-bad/30 rounded-xl bg-panel text-[13px]">
      <button class="w-full flex items-center gap-2 px-3 py-1.5 text-left" onClick={() => setOpen(!open)}>
        <span class="text-bad">⛔</span><span class="flex-1 truncate"><span class="text-mute">되짚기 금지 {bans.length}:</span> {bans[bans.length - 1].text}</span>
        <span class="text-mute">{open ? '▾' : '▴'}</span>
      </button>
      {open && <div class="px-3 pb-2 border-t border-soft pt-1.5 space-y-1">
        {bans.map(b => <div key={b.id} class="flex items-center gap-2">
          <code class="font-mono text-[12px] flex-1 min-w-0 truncate" title={b.text}>{b.tool !== '*' ? b.tool + ' ' : ''}{b.text}</code>
          <span class="text-xs text-mute flex-none">{b.source === 'repeat' ? '되풀이' : '되짚기 글'}{b.hits ? ` · 막음 ${b.hits}` : ''}</span>
          <button class="btn !py-0.5 !px-2 text-xs flex-none" onClick={() => unban(b.id)}>풀기</button>
        </div>)}
        <div class="text-xs text-mute">되짚기에서 다시 하지 않기로 한 호출이다. 엔진이 돌리지 않고 모델에 다른 길로 가라고 돌려준다.</div>
      </div>}
    </div>
  )
}

function Toasts() {
  return (
    <div class="fixed bottom-24 right-4 max-md:left-3 max-md:right-3 z-50 space-y-2">
      {toasts.value.map(t => <div key={t.id} class="bg-panel border border-line rounded-xl px-3 py-2 text-sm shadow-card flex gap-2 items-center">
        <span class={'dot ' + (t.kind === 'ok' ? 'dot-ok' : 'dot-bad')} />{t.text}</div>)}
    </div>
  )
}

function App() {
  const side = sideOpen.value, pan = !!panel.value
  if (!hub.value) return <div class="h-full grid place-items-center text-mute"><div class="flex items-center gap-2"><span class="spin" />허브에 붙는 중…</div></div>
  return (
    <div class="h-full grid grid-cols-1 md:[grid-template-columns:var(--cols)]" style={`--cols:${side ? '268px ' : ''}minmax(0,1fr)${pan ? ' 380px' : ''}`}>
      {side && <div class="max-md:hidden h-full min-h-0"><Guard name="사이드바"><Sidebar /></Guard></div>}
      {drawer.value && <div class="md:hidden fixed inset-0 z-40 flex"><div class="w-[84%] max-w-80 h-full shadow-card"><Sidebar /></div>
        <div class="flex-1 bg-black/45" onClick={() => { drawer.value = false }} /></div>}
      <div class="h-full min-h-0 min-w-0"><Center /></div>
      {pan && <div class="max-md:hidden" />}
      <AskSheet />
      <Toasts />
    </div>
  )
}

render(<App />, document.getElementById('app'))
connect()
refresh()
