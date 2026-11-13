// stewart 허브 화면 — 틀: 데스크톱은 [사이드바 | 대화 | (필요할 때) 작업 내역], 폰은 한 화면(서랍·아래 시트)
import './polyfills.js'
import './copy.js'
import { render, Component } from 'preact'
import { useEffect, useRef, useState, useLayoutEffect } from 'preact/hooks'
import './app.css'
import { get, post, fmtK, fmtSecs, estTokens } from './lib.js'
import { hub, view, panel, setPanel, drawer, sideOpen, toggleSide, toasts, connect, refresh, sessions, toast, chat, cpost, openChat } from './state.js'
import { Thread, Md, TodoItems, UserImgs } from './thread.jsx'
import { Approvals, AskSheet, Composer } from './dock.jsx'
import { Sidebar, ConnDot, ModelDot, ExternalChip, ToolChips, ModeChips, kindTag, NewSession } from './side.jsx'
import { Panel } from './panel.jsx'
import { useWorker, WorkerThread, WorkerControls, WorkerInfo } from './worker.jsx'
import { narrow, convSheet } from './mobile.js'
import { IconMenu, IconNew, IconChev, IconPanel, IconUpS, IconDown2 } from './icons.jsx'
import { TokenLine, SKILLS_RX } from './thread.jsx'
import { detail, setDetail } from './detail.js'
import { TABS as PANEL_TABS } from './panel.jsx'
import { conn } from './state.js'
import { place, PlaceName } from './places.jsx'
import { newChat, workerOpen } from './newchat.jsx'
import { SettingsSheet, settingsOpen } from './settings.jsx'

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
    <button class="ml-auto flex-none flex items-center gap-2 text-xs text-mute" title={`지금 문맥 ${t.estimated ? '약 ' : ''}${used}토큰 / 창 ${t.window}` + (t.model_window && t.model_window !== t.window ? `(모델 창 ${t.model_window} 가운데 실사용 몫)` : '') + (t.compact_at ? ` · ${Math.round(t.compact_at)}에서 오래된 도구 결과를 줄인다` : '')}
      onClick={() => setPanel(panel.value === 'tokens' ? null : 'tokens')}>
      <span class="max-md:hidden">문맥 {t.estimated ? '약 ' : ''}{fmtK(used)} / {fmtK(t.window)}</span>
      <span class="relative w-36 max-md:w-16 h-1.5 rounded-full bg-line overflow-hidden">
        <i class="absolute inset-y-0 left-0 rounded-full" style={`width:${pct}%;background:${pct > 85 ? 'var(--c-bad)' : 'var(--c-accent)'}`} />
        {mark != null && <b class="absolute -inset-y-0.5 w-0.5 bg-warn" style={`left:${mark}%`} />}
      </span>
      <span>{t.estimated ? '~' : ''}{pct}%</span>
    </button>
  )
}

// [새 대화]: 지금 대화는 기록에 남기고(지난 세션에서 다시 연다) 빈 문맥으로 — /clear·/reset 과 같다. 확인은 단추에만
async function newMain() {
  if (!confirm('지금 대화를 닫고 새로 시작할까요?\n(지금 대화는 기록에 남아 지난 세션 목록에서 다시 열 수 있다)')) return
  try { await cpost('/api/main/new', {}); toast('새 대화를 시작했다', 'ok'); refresh() } catch (e) { toast('새로 못 함: ' + e.message) }
}
function NewMain() {
  const busy = hub.value?.busy
  return <button class="btn btn-ghost !px-2.5 text-xs flex-none" disabled={busy} onClick={newMain}
    title={busy ? '메인이 일하는 중 — 멈춘 뒤' : '새 대화(/clear): 지금 대화는 기록에 남는다'}>새 대화</button>
}

function Prior() {
  // 판 124.2: 이어 받은 앞 대화는 허브가 대화 줄(사람 말·답·도구 한 줄)로 다시 그린다.
  // 판 126: 원문(도구 결과·사진·환경 정보, 처음부터 끝까지)도 접힌 줄 하나로 남긴다 — 과정은 다 닿게. 펼치면 뒤에서부터 80줄씩
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
    <div class="mb-3 text-[13px] text-mute">
      {p && <button class="text-left hover:text-fg" onClick={() => setOpen(!open)}>{open ? '▾' : '▸'} 이어 받은 앞 대화 원문 {p.total}줄(도구 결과 포함) — {open ? '접기' : '펼쳐 보기'}</button>}
      {note && <div class="mt-0.5">ⓘ {note}</div>}
      {open && p && <div class="mt-2 text-fg text-[14px] border-l-2 border-line pl-3 max-h-[60vh] overflow-auto">
        {p.start > 0 && <button class="text-mute hover:text-fg text-[13px] mb-2" onClick={more}>앞 {p.start}줄 더 보기</button>}
        {p.items.map((it, i) => it.kind === 'assistant' ? <div key={i} class="my-2"><Md text={it.text} /></div>
          : <div key={i} class={'my-1 whitespace-pre-wrap break-words ' + (it.kind === 'user' ? 'font-semibold' : 'text-mute text-[13px]')}>{it.kind === 'user' ? '사람: ' : it.kind === 'tool' ? '⏺ ' : ''}{it.text.slice(0, 2000)}
            {it.imgs?.length > 0 && <UserImgs names={it.imgs} />}</div>)}
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

const safeColor = c => (/^#?[0-9a-fA-F]{3,8}$/.test(c) || /^[a-z]+$/.test(c)) ? (c.match(/^[0-9a-fA-F]{3,8}$/) ? '#' + c : c) : 'var(--c-warn)'
// 허브가 여럿일 때 가름: 머리 이름표·색 띠·창 제목([hub] label·color)
function useTitle() {
  const lab = hub.value?.hub_label
  useEffect(() => { document.title = lab ? `${lab} — stewart hub` : 'stewart hub' }, [lab])
}

function Center() {
  useTitle()
  const v = view.value
  const conv = (hub.value?.chats || []).find(c => c.name === v)
  const isMain = v === 'main' || !!conv          // 대화(메인·사람이 연 대화)는 모두 같은 채팅 칸
  const st = hub.value
  const w = useWorker(isMain ? '' : v)
  const [ref, onScroll] = useStick()
  useEffect(() => { if (isMain && chat.value !== v) openChat(v) }, [v])
  const title = v === 'main' ? '메인' : conv ? ((/^(새 대화|대화 \d+)$/.test(conv.title || '') && conv.first) || conv.title) : `작업자 ${v}`   // 판 125: 새 대화는 첫 말을 이름으로
  const main = sessions.value.find(s => s.role === 'main')
  return (
    <main class="flex flex-col min-w-0 h-full relative">
      {st?.hub_color && <div class="h-1.5 w-full flex-none" style={`background:${safeColor(st.hub_color)}`} title={st.hub_label || ''} />}
      <MobileHead title={title} isMain={isMain} conv={conv} w={!isMain ? v : null} />
      {workerOpen.value && <NewSession onClose={() => { workerOpen.value = false }} />}
      {settingsOpen.value && <SettingsSheet />}
      <div class="flex-1 overflow-auto scroll-thin" ref={ref} onScroll={onScroll}>
        <div class="max-w-[760px] mx-auto px-6 max-md:px-4 pt-6 max-md:pt-3 pb-10 [overflow-wrap:anywhere]">
          <Guard key={v} name="대화">{isMain ? <Thread log={st?.main_log || []} prior={<Prior />} /> : <WorkerThread name={v} {...w} />}</Guard>
        </div>
      </div>
      <div class="pb-4 max-md:pb-0 safe-b pt-1">
        <div class="max-w-[760px] mx-auto px-6 max-md:px-3">
          {isMain && <Guard name="금지"><BanStrip /></Guard>}
          {isMain && <Guard name="할 일"><TodoStrip /></Guard>}
          <Guard name="승인">{/* 승인 칸이 깨지면 최소 칸(허용·거부)은 Approvals 안에서 건마다 따로 */}<Approvals /></Guard>
          <Guard key={'c' + v} name="입력"><Composer target={isMain ? 'main' : v} /></Guard>
        </div>
      </div>
      <Right worker={!isMain ? w : null} />
    </main>
  )
}

// 판 124 머리(폰·데스크톱): 한 줄(48px) — ☰ · 대화 이름(누르면 "이 대화" 판) · 새 대화(+ 데스크톱은 작업 내역). 나머지는 판으로, 상태는 비정상일 때만
function MobileHead({ title, isMain, conv, w }) {
  const st = hub.value
  const t = st?.tokens
  const pct = t && t.window ? Math.min(100, Math.round((t.last_prompt || 0) / t.window * 100)) : 0
  return (
    <div class="flex-none bg-bg">
      <div class="h-12 flex items-center gap-1 px-1.5">
        <button class="ibtn" onClick={() => { if (narrow.value) drawer.value = true; else toggleSide() }} aria-label="세션 서랍" title="옆 기둥 접기·펴기"><IconMenu /></button>
        <button class="conv-title flex-1 md:flex-none min-w-0 h-11 flex items-center justify-center md:justify-start gap-1 font-semibold text-[16px] md:px-2 md:rounded-xl md:hover:bg-soft" onClick={() => { convSheet.value = !convSheet.value }}
          aria-label="이 대화 — 설정·작업 내역">
          {st?.hub_label && <span class="text-mute font-normal truncate max-w-[30%]">{st.hub_label} ·</span>}
          {isMain && <PlaceMark conv={conv} />}
          <span class="truncate">{title}</span><span class="text-mute w-4 h-4 rotate-90 flex-none"><IconChev /></span>
        </button>
        <span class="flex-1 max-md:hidden" />
        <span class="max-md:hidden"><StatusNote inline /></span>
        {isMain ? <button class="ibtn" onClick={newChat} aria-label="새 대화" title="새 대화"><IconNew /></button>
          : <span class="w-11" />}
        <button class={'ibtn max-md:hidden ' + (panel.value ? 'bg-soft' : '')} onClick={() => setPanel(panel.value ? null : (isMain ? 'steps' : 'session'))}
          aria-label="작업 내역" title="작업 내역(오른쪽 칸)"><IconPanel /></button>
      </div>
      {pct >= 70 && <div class="h-0.5 w-full bg-line"><div class="h-full" style={`width:${pct}%;background:${pct > 85 ? 'var(--c-bad)' : 'var(--c-warn)'}`} /></div>}
      <span class="md:hidden"><StatusNote /></span>
      {!isMain && w && <div class="px-3 pb-1.5 flex gap-2 overflow-x-auto"><WorkerControls name={w} /></div>}
    </div>
  )
}

// 판 124: 자리 표시 — parlando 는 기울인 "parlando", 코드는 프로젝트 폴더 이름(보조 글 색)
export function chatKind(conv) {
  const st = hub.value
  return (conv || (st?.chats || []).find(c => c.name === 'main') || {}).kind || 'code'
}
function PlaceMark({ conv }) {
  const k = chatKind(conv)
  const wd = sessions.value.find(s => s.role === 'main')?.workdir
  return <span class="text-mute font-normal truncate max-md:max-w-[40%] flex-none">{place(k).folder && wd ? wd.split('/').slice(-1)[0] : <PlaceName k={k} short={narrow.value} />}<span class="mx-1">·</span></span>
}

// 정상이면 아무것도 없다. 허브가 끊기거나 모델이 준비 안 됐을 때만 얇은 한 줄. 바깥 API 는 늘 한 줄(대화 내용이 그 회사로 나간다)
function StatusNote({ inline }) {
  const c = conn.value, m = hub.value?.model, ext = hub.value?.backend?.external
  const bad = []
  if (c.state !== 'ok') bad.push(c.state === 'login' ? '허브 로그인 필요' : c.state === 'retry' ? `허브 다시 붙는 중(${c.tries})` : '허브 붙는 중')
  if (m && m.state && !['ok', 'unknown'].includes(m.state)) bad.push(`모델 ${m.label}`)
  if (!bad.length && !ext) return null
  return (
    <div class={(inline ? 'pr-2' : 'px-4 pb-1.5') + ' text-[13px] flex gap-3 items-center'}>
      {bad.length > 0 && <span class="text-warn truncate">● {bad.join(' · ')}</span>}
      {ext && <span class="text-mute truncate ml-auto">바깥 API · {ext.replace(/\(.*\)/, '').trim()}</span>}
    </div>
  )
}

// "이 대화" 판의 방식·생각·문맥 크기 — 알약 셋 대신 줄 셋(왼쪽 이름, 오른쪽 값·고르기)
const MODE_KO = { plan: '계획(읽기만)', ask: '묻기(바꾸기 전 묻기)', edits: '편집(파일은 바로, 셸은 묻기)', auto: '자동' }
const RANK = { plan: 0, ask: 1, edits: 2, auto: 3 }
function ModeRows({ st, talk }) {
  const modes = Object.keys(RANK).filter(m => RANK[m] <= RANK[st.mode_max || 'edits'])
  const setMode = mode => cpost('/api/main/mode', { mode }).then(refresh, e => toast('안 됨: ' + e.message))
  const nextThink = st.think == null ? true : st.think ? false : null
  const setThink = () => cpost('/api/main/think', { think: nextThink }).then(refresh, e => toast('안 됨: ' + e.message))
  const setBudget = () => {
    const v = window.prompt('이 대화의 문맥 크기(토큰, 예 24000). 비우면 백엔드 설정 그대로.', String(st.budget || ''))
    if (v === null) return
    const n = v.trim() === '' ? null : parseInt(v.replace(/[^0-9]/g, ''), 10)
    if (n !== null && !(n > 0)) { toast('숫자를 넣는다'); return }
    cpost('/api/main/budget', { tokens: n }).then(refresh, e => toast('안 됨: ' + e.message))
  }
  return <>
    {!talk && <label class="row-btn"><span>편집 방식</span>
      <select class="sub bg-transparent text-right outline-none" value={st.mode} onChange={e => setMode(e.currentTarget.value)} aria-label="편집 방식">
        {modes.map(m => <option key={m} value={m}>{MODE_KO[m]}</option>)}
      </select></label>}
    <button class="row-btn" onClick={setThink} title={'누르면 ' + (nextThink == null ? '백엔드 기본' : nextThink ? '켬' : '끔')}>
      <span>생각</span><span class="sub">{st.think == null ? '백엔드 기본' : st.think ? '켬' : '끔'}</span></button>
    {st.budget && <button class="row-btn" onClick={setBudget}><span>문맥 크기</span><span class={'sub ' + (st.budget_set ? '!text-warn' : '')}>{Math.round(st.budget / 1000)}k 토큰</span></button>}
  </>
}

// 데스크톱: 판을 머리의 대화 이름 바로 아래에 붙인다(폰은 아래 시트라 비운다)
function anchorStyle() {
  if (narrow.value) return ''
  const r = document.querySelector('.conv-title')?.getBoundingClientRect()
  return r ? `left:${Math.round(r.left)}px;margin-left:0;top:${Math.round(r.bottom + 4)}px` : ''
}

// "이 대화" 시트: 머리에서 뺀 것 — 프로젝트·종류·방식·생각·문맥·도구·누계·작업 내역·갈래·닫기
function ConvSheet() {
  const [tabsOpen, setTabsOpen] = useState(false), [modelOpen, setModelOpen] = useState(false), [skillsOpen, setSkillsOpen] = useState(false)
  if (!convSheet.value) return null
  const st = hub.value, v = view.value
  const conv = (st?.chats || []).find(c => c.name === v)
  const isMain = v === 'main' || !!conv
  const main = sessions.value.find(s => s.role === 'main')
  const t = st?.tokens
  const close = () => { convSheet.value = false }
  const kind = isMain && kindTag(conv || (st?.chats || []).find(c => c.name === 'main'))
  // 판 126: 과정은 다 닿게 — 스킬 줄(대화에선 접음)·모델 상태 자세히·작업 내역 탭 목록·parlando 자리 폴더를 이 판에서 한 번에
  const skills = (st?.main_log || []).find(m => SKILLS_RX.test(m.text || ''))?.text || ''
  const m = st?.model
  const talkWd = conv && chatKind(conv) === 'talk' ? sessions.value.find(x => x.name === conv.name)?.workdir : ''
  return (
    <>
      <div class="sheet-back" onClick={close} />
      <div class="sheet" role="dialog" aria-label="이 대화" style={anchorStyle()}>
        <div class="text-[13px] text-mute mb-1">이 대화</div>
        {isMain && main?.workdir && chatKind(conv) !== 'talk' && <div class="row-btn"><span>프로젝트</span><span class="sub truncate max-w-[60%]" title={main.workdir}>{main.workdir.split('/').slice(-1)[0]}</span></div>}
        {kind && <div class="row-btn"><span>자리</span><span class="sub"><PlaceName k={chatKind(conv)} />{conv?.tier ? ' · ' + conv.tier : ''}</span></div>}
        {talkWd && <div class="row-btn"><span>자리 폴더</span><span class="sub truncate max-w-[60%]" title={talkWd}>{talkWd.split('/').slice(-1)[0]}</span></div>}
        {isMain && st?.mode && <ModeRows st={st} talk={chatKind(conv) === 'talk'} />}
        {isMain && t?.window && <div class="row-btn"><span>문맥 사용</span><span class="sub">{t.estimated ? '약 ' : ''}{fmtK(t.last_prompt || 0)} / {fmtK(t.window)} · {Math.min(100, Math.round((t.last_prompt || 0) / t.window * 100))}%{t.model_window && t.model_window !== t.window ? ` · 모델 창 ${fmtK(t.model_window)}` : ''}</span></div>}
        {(st?.tool_toggles || []).length > 0 && <div class="py-2.5 border-b border-line"><div class="mb-1.5">도구</div><div class="flex gap-1.5 flex-wrap"><ToolChips /></div></div>}
        {st?.backend?.external && <div class="row-btn"><span>바깥 API</span><span class="sub text-warn text-right max-w-[72%]"><ExternalChip plain /></span></div>}
        {t && <div class="py-2.5 border-b border-line text-[12.5px] text-mute leading-relaxed">
          {t.turn && <div>이번 차례 · 입력 {fmtK(t.turn.prompt)} · 출력 {fmtK(t.turn.completion)} · 모델 {fmtSecs(t.turn.model_s || 0)}</div>}
          <div>이 대화 · 입력 {fmtK(t.prompt_total)} · 출력 {fmtK(t.completion_total)} · 단계 {t.steps}{t.compactions ? ` · 줄이기 ${t.compactions}번` : ''}</div>
        </div>}
        {skills && <button class="row-btn" aria-expanded={skillsOpen} onClick={() => setSkillsOpen(!skillsOpen)}><span>스킬</span><span class="sub truncate max-w-[65%]">{(skills.match(/스킬 (\d+)개/) || [])[1] || ''}개 {skillsOpen ? '▴' : '▾'}</span></button>}
        {skillsOpen && <div class="py-2 border-b border-line text-[12.5px] text-mute whitespace-pre-wrap break-words">{skills.trim()}</div>}
        <label class="row-btn cursor-pointer"><span>자세히 보기</span><span class="sub flex items-center gap-2">{detail.value ? '켬 — 과정을 다 펼침' : '끔'}
          <input type="checkbox" checked={detail.value} onChange={e => setDetail(e.currentTarget.checked)} /></span></label>
        <button class="row-btn" aria-expanded={tabsOpen} onClick={() => setTabsOpen(!tabsOpen)}><span>작업 내역</span><span class="sub w-4 h-4">{tabsOpen ? <IconUpS /> : <IconDown2 />}</span></button>
        {tabsOpen && <div class="pb-1 border-b border-line">{[...(isMain ? [] : [['session', '작업자']]), ...PANEL_TABS].map(([k, label]) =>
          <button key={k} class="row-btn !border-0 !min-h-[40px] pl-4" onClick={() => { close(); setPanel(k) }}><span class="text-[14.5px]">{label}</span><span class="sub w-4 h-4"><IconChev /></span></button>)}</div>}
        {isMain && <button class="row-btn" disabled={st?.busy} onClick={() => { close(); forkChat() }}><span>갈래 — 지금 대화를 복사해 새 대화로</span></button>}
        {conv && v !== 'main' && <button class="row-btn text-bad" onClick={() => { close(); closeChat(v) }}><span>이 대화 닫기</span></button>}
        <button class="pt-2.5 text-[12.5px] text-mute text-left w-full truncate" aria-expanded={modelOpen} onClick={() => setModelOpen(!modelOpen)}
          title={st?.web?.why || ''}>모델 {m ? (m.served || [])[0] || m.configured : st?.backend?.model}
          {st?.web ? ' · ' + (st.web.search ? '웹 검색·읽기' : st.web.fetch ? '웹 읽기만' : '웹 꺼짐') : ''} {modelOpen ? '▴' : '▾'}</button>
        {modelOpen && <div class="text-[12.5px] text-mute leading-relaxed pt-1">
          {m && <div>상태 {m.label}{m.detail ? ' — ' + m.detail : ''}{m.checked ? ` · 확인 ${new Date(m.checked * 1000).toLocaleTimeString()}` : ''}</div>}
          {m && <div>서버가 올린 모델: {(m.served || []).join(', ') || '(없음)'} · 설정 이름 {m.configured} · 백엔드 {m.backend}</div>}
          {m?.pod && <div>장비 별칭·어댑터: {[m.pod.model, m.pod.adapter].filter(Boolean).join(' / ')}</div>}
          {st?.web?.why && <div>웹: {st.web.why}</div>}
        </div>}
      </div>
    </>
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
      <ConvSheet />
      <Toasts />
    </div>
  )
}

render(<App />, document.getElementById('app'))
connect()
refresh()
