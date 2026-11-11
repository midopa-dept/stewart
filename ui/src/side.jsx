// 왼쪽 사이드바(폰은 서랍): 현황판 자리 · 새 작업자 · 지금 세션 · 지난 세션 · 바닥(연결·알림·옛 화면)
import { useState, useEffect } from 'preact/hooks'
import { get, post, store, USER } from './lib.js'
import { hub, sessions, queue, view, drawer, refresh, toast, tick, clockSkew, conn, setPanel } from './state.js'
import { NotifToggle } from './notify.jsx'

export const STATUS = {
  starting: ['시작하는 중', 'run'], idle: ['쉬는 중', 'ok'], working: ['일하는 중', 'run'], waiting_approval: ['승인 기다림', 'wait'],
  waiting_main: ['메인 답 기다림', 'wait'], paused: ['멈춤', ''], stopped: ['끝냄', ''], failed: ['실패', 'bad'],
}

function Board() {
  // stewart 현황판(판 49): 사용자 할 일(U-번호, 급한 것부터) + 승인 대기(대기열과 같은 출처) + 진행 중. 누르면 내역 [현황판] 탭
  const q = queue.value, b = hub.value?.board || { open: [], progress: [], done: [] }
  const n = b.open.length + q.length
  const urg = { 돈: 'text-bad', 장애: 'text-bad', 마감: 'text-warn', 막힘: 'text-warn' }
  return (
    <button class={'mx-3 mb-2.5 border rounded-[10px] px-2.5 py-2 text-[13px] text-left w-[calc(100%-24px)] hover:bg-soft ' + (n ? 'border-warn/60' : 'border-line')}
      onClick={() => { setPanel('board'); drawer.value = false }} title="현황판 전체(작업 내역 → 현황판)">
      <div class="flex items-center font-semibold mb-1">현황판{n ? <span class="chip ml-auto !text-warn">사용자 할 일 {n}</span> : <span class="chip ml-auto">할 일 없음</span>}</div>
      {b.open.slice(0, 4).map(t => <div key={t.id} class="py-0.5 truncate"><b class={'mr-1 ' + (urg[t.urgency] || 'text-warn')}>{t.id}</b>{t.title}</div>)}
      {q.slice(0, 3).map((it, i) => <div key={'q' + i} class="py-0.5 truncate"><b class="text-warn mr-1">승인</b>{it.session} #{it.seq}</div>)}
      {b.open.length > 4 && <div class="text-xs text-mute">… {b.open.length - 4}개 더</div>}
      {(b.later || []).length > 0 && <div class="text-xs text-mute">아직 아님 {b.later.length}</div>}
      {b.progress[0] && <div class="text-mute text-xs truncate mt-0.5">진행: {b.progress[0].what}{b.progress.length > 1 ? ` 외 ${b.progress.length - 1}` : ''}</div>}
      {!b.progress[0] && b.done[0] && <div class="text-mute text-xs truncate mt-0.5">최근 끝: {b.done[0].what}</div>}
    </button>
  )
}

const MODES = ['plan', 'ask', 'edits', 'auto']
function NewSession({ onClose }) {
  const main = sessions.value.find(s => s.role === 'main')
  const top = main ? Math.max(0, MODES.indexOf(main.mode)) : 2
  const [f, setF] = useState({ name: '', task: '', workdir: '', repo: '', branch: '', profile: '', mode: MODES[top] })
  const [err, setErr] = useState('')
  const set = k => e => setF({ ...f, [k]: e.currentTarget.value })
  async function go() {
    const body = { name: f.name.trim(), task: f.task.trim(), mode: f.mode }
    if (f.profile.trim()) body.profile = f.profile.trim()
    if (f.repo.trim()) body.worktree = { repo: f.repo.trim(), branch: f.branch.trim() }
    else if (f.workdir.trim()) body.workdir = f.workdir.trim()
    try { const r = await post('/api/session/new', body); onClose(); view.value = r.session; drawer.value = false; refresh() } catch (e) { setErr(e.message) }
  }
  return (
    <div class="fixed inset-0 z-50 bg-black/45 grid place-items-center p-4" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div class="w-full max-w-md bg-panel rounded-2xl p-4 space-y-2 shadow-card">
        <div class="font-semibold text-base mb-1">새 작업자</div>
        <input class="field" placeholder="이름(영숫자·-), 예: w-docs" value={f.name} onInput={set('name')} autoFocus />
        <textarea class="field min-h-24" placeholder="첫 말(이 작업자가 할 일)" value={f.task} onInput={set('task')} />
        <input class="field" placeholder="작업 폴더(비우면 프로젝트 폴더, 프로젝트 안만)" value={f.workdir} onInput={set('workdir')} />
        <div class="flex gap-2"><input class="field" placeholder="새 worktree 저장소(선택)" value={f.repo} onInput={set('repo')} />
          <input class="field" placeholder="가지" value={f.branch} onInput={set('branch')} /></div>
        <div class="flex gap-2"><input class="field" placeholder="프로필(비우면 기본)" value={f.profile} onInput={set('profile')} />
          <select class="field w-36" value={f.mode} onChange={set('mode')}>{MODES.slice(0, top + 1).map(m => <option key={m} value={m}>모드 {m}</option>)}</select></div>
        {err && <div class="text-bad text-sm">안 됨: {err}</div>}
        <div class="flex gap-2 justify-end pt-1"><button class="btn" onClick={onClose}>닫기</button><button class="btn btn-pri" disabled={!f.name.trim() || !f.task.trim()} onClick={go}>열기</button></div>
      </div>
    </div>
  )
}

function SessionItem({ s }) {
  const [label, dot] = STATUS[s.status] || [s.status, '']
  const asks = queue.value.filter(x => x.session === s.name).length
  tick.value
  const isMain0 = s.role === 'main'
  let now = isMain0 ? (hub.value?.busy ? hub.value?.now : null) : s.now   // 메인은 허브 상태의 지금 하는 일(등록부 값은 늦다)
  if (now && now.what.startsWith('사람 승인') && !asks) now = null    // 승인 글은 대기열에 실제 건이 있을 때만(같은 출처로 맞춘다)
  const secs = now ? Math.max(0, Math.round(Date.now() / 1000 - clockSkew.value - now.since)) : 0
  const on = view.value === s.name || (s.role === 'main' && view.value === 'main')
  const isMain = s.role === 'main'
  const st = hub.value
  const mainBusy = isMain && st?.busy
  return (
    <button class={'mx-2 my-px px-2.5 py-2 rounded-[9px] flex flex-col gap-px text-left w-[calc(100%-16px)] hover:bg-soft ' + (on ? 'bg-soft' : '')}
      onClick={() => { view.value = isMain ? 'main' : s.name; drawer.value = false }}>
      <span class="font-[560] truncate w-full">{isMain ? '메인' : s.name}{asks ? <span class="chip ml-1.5 !text-warn">승인 {asks}</span> : null}</span>
      <span class="text-xs text-mute flex gap-1.5 items-center w-full min-w-0">
        <span class={'dot ' + (mainBusy ? 'dot-run' : dot ? 'dot-' + dot : '')} />
        <span class="truncate">{isMain ? (mainBusy ? '일하는 중' : '기다리는 중') : label}{now ? ` · ${now.what.slice(0, 40)} · ${secs}초` : ''}</span>
      </span>
    </button>
  )
}

async function manage(body) {
  const r = await post('/api/sessions/manage', body)
  if (r.failed?.length) toast('못 지운 것: ' + r.failed.map(f => `${f.key} — ${f.detail}`).join(' / '))
  return r
}

function PastRow({ x, sel, onSel, picking, reload }) {
  const [menu, setMenu] = useState(false)
  const item = { kind: x.kind, key: x.key }
  const act = async (fn, okMsg) => { setMenu(false); try { await fn(); if (okMsg) toast(okMsg, 'ok', 4000); refresh(); reload() } catch (e) { toast('안 됨: ' + e.message) } }
  const label = x.title || (x.kind === 'main' ? (x.first || '(첫 말 없음)') : x.name)
  const sub = x.kind === 'main' ? `메인 · ${(x.started || '').replace('T', ' ').slice(5, 16)} · 사람 말 ${x.turns}번` : `작업자 ${x.name} · ${(STATUS[x.status] || [x.status])[0]}` + (x.first && !x.title ? '' : x.first ? ' · ' + x.first : '')
  return (
    <div class={'mx-2 px-2.5 py-1.5 rounded-[9px] hover:bg-soft relative ' + (sel ? 'bg-soft' : '')}>
      <div class="flex items-center gap-1.5">
        {picking && <input type="checkbox" checked={sel} onChange={onSel} class="flex-none" />}
        <button class="truncate font-[560] text-[14px] flex-1 min-w-0 text-left" title={x.kind === 'worker' ? label + ' — 대화 보기' : label}
          onClick={() => { if (x.kind === 'worker' && !picking) { view.value = x.name; drawer.value = false } else if (picking) onSel() }}>{label}</button>
        {!picking && x.kind === 'worker' && x.can_reopen && <button class="btn btn-ghost !px-2 !py-0.5 text-xs flex-none" onClick={() => act(() => post('/api/session/control', { name: x.name, action: 'resume' }))}>{x.status === 'paused' ? '이어 가기' : '다시 열기'}</button>}
        {!picking && x.kind === 'main' && <button class="btn btn-ghost !px-2 !py-0.5 text-xs flex-none" onClick={() => act(() => post('/api/main/reopen', { run_dir: x.run_dir }).then(() => { view.value = 'main' }))}>메인으로</button>}
        {!picking && <button class="btn btn-ghost !px-1.5 !py-0.5 text-xs flex-none" aria-label="메뉴" onClick={() => setMenu(!menu)}>⋯</button>}
      </div>
      <div class="text-xs text-mute truncate">{sub}</div>
      {menu && <div class="absolute right-2 top-8 z-30 bg-panel border border-line rounded-lg shadow-card text-[13px] py-1 min-w-36">
        <button class="block w-full text-left px-3 py-1.5 hover:bg-soft" onClick={() => { const t = prompt('새 이름(비우면 원래대로)', x.title || ''); if (t !== null) act(() => manage({ op: 'rename', items: [item], title: t })) }}>이름 바꾸기</button>
        <button class="block w-full text-left px-3 py-1.5 hover:bg-soft" onClick={() => act(() => manage({ op: x.archived ? 'unarchive' : 'archive', items: [item] }), x.archived ? '보관 풂' : '보관함으로 옮겼다')}>{x.archived ? '보관 풀기' : '보관(목록에서 숨김)'}</button>
        <button class="block w-full text-left px-3 py-1.5 hover:bg-soft text-bad" onClick={() => { if (confirm(`"${label}" 을 휴지통으로 옮길까요?\n대화 기록만 옮기고 작업 폴더의 파일은 그대로다. 휴지통을 비우기 전까지 되돌릴 수 있다.`)) act(() => manage({ op: 'delete', items: [item] }), '휴지통으로 옮겼다 — 아래 휴지통에서 되돌린다') }}>지우기(휴지통)</button>
      </div>}
    </div>
  )
}

function Past() {
  const [p, setP] = useState({ workers: [], mains: [], archived_count: 0, trash: [] })
  const [open, setOpen] = useState(store.get('stewart-past-open', false))
  const [arch, setArch] = useState(false), [picking, setPicking] = useState(false), [sel, setSel] = useState({})
  const [more, setMore] = useState(false), [trashOpen, setTrashOpen] = useState(false)
  const load = () => get('/api/past_sessions' + (arch ? '?archived=1' : '')).then(setP).catch(() => {})
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t) }, [arch])
  const rows = [...p.workers, ...p.mains]
  const n = rows.length
  const picked = rows.filter(x => sel[x.kind + x.key]).map(x => ({ kind: x.kind, key: x.key }))
  const act = async (fn, okMsg) => { try { await fn(); if (okMsg) toast(okMsg, 'ok', 4000); setSel({}); setPicking(false); refresh(); load() } catch (e) { toast('안 됨: ' + e.message) } }
  const toggle = () => { setOpen(!open); store.set('stewart-past-open', !open) }
  const LIMIT = 12
  const shown = more ? rows : rows.slice(0, LIMIT)
  if (!n && !p.archived_count && !p.trash.length && !arch) return null
  return (
    <>
      <div class="px-4 pt-3 pb-1 flex items-center gap-1 text-xs text-mute font-semibold tracking-wide">
        <button class="text-left flex-1" onClick={toggle}>{arch ? '보관함' : '지난 대화'} {n} {open ? '▴' : '▾'}</button>
        {open && <button class="hover:text-fg font-normal" onClick={() => { setPicking(!picking); setSel({}) }}>{picking ? '고르기 끝' : '고르기'}</button>}
      </div>
      {open && picking && <div class="mx-3 mb-1.5 flex flex-wrap gap-1.5 text-xs">
        <button class="btn !py-0.5 !px-2 text-xs" onClick={() => setSel(Object.fromEntries(rows.map(x => [x.kind + x.key, true])))}>모두</button>
        <button class="btn !py-0.5 !px-2 text-xs btn-bad" disabled={!picked.length} onClick={() => { if (confirm(`고른 ${picked.length}개를 휴지통으로 옮길까요? (대화 기록만, 되돌릴 수 있다)`)) act(() => manage({ op: 'delete', items: picked }), `${picked.length}개를 휴지통으로`) }}>고른 {picked.length}개 지우기</button>
        <button class="btn !py-0.5 !px-2 text-xs" disabled={!picked.length} onClick={() => act(() => manage({ op: arch ? 'unarchive' : 'archive', items: picked }))}>{arch ? '보관 풀기' : '보관'}</button>
        <button class="btn !py-0.5 !px-2 text-xs" onClick={() => { const d = prompt('며칠 넘게 손대지 않은 지난 대화를 모두 휴지통으로 옮길까요? (일 수)', '30'); if (d && +d > 0) act(() => manage({ op: 'delete', older_days: +d }), `${d}일 넘은 것을 휴지통으로`) }}>N일 넘은 것 모두…</button>
      </div>}
      {open && shown.map(x => <PastRow key={x.kind + x.key} x={x} picking={picking} sel={!!sel[x.kind + x.key]} reload={load}
        onSel={() => setSel(s => ({ ...s, [x.kind + x.key]: !s[x.kind + x.key] }))} />)}
      {open && !n && <div class="mx-4 text-xs text-mute py-1">{arch ? '보관한 것 없음' : '없음'}</div>}
      {open && rows.length > LIMIT && <button class="mx-4 text-xs text-mute hover:text-fg py-1" onClick={() => setMore(!more)}>{more ? '접기' : `${rows.length - LIMIT}개 더 보기`}</button>}
      {open && <div class="mx-4 mt-1 flex gap-3 text-xs text-mute">
        {(p.archived_count > 0 || arch) && <button class="hover:text-fg" onClick={() => { setArch(!arch); setSel({}) }}>{arch ? '← 지난 대화' : `보관함 ${p.archived_count}`}</button>}
        {p.trash.length > 0 && <button class="hover:text-fg" onClick={() => setTrashOpen(!trashOpen)}>휴지통 {p.trash.length} {trashOpen ? '▴' : '▾'}</button>}
      </div>}
      {open && trashOpen && p.trash.length > 0 && <div class="mx-3 my-1.5 border border-line rounded-lg p-2 text-[13px]">
        {p.trash.map(t => <div key={t.id} class="flex items-center gap-1.5 py-0.5">
          <span class="truncate flex-1">{t.label}<span class="text-xs text-mute"> · {t.kind === 'main' ? '메인' : '작업자'} · {t.deleted_at ? new Date(t.deleted_at * 1000).toLocaleString() : ''}</span></span>
          <button class="btn !py-0.5 !px-2 text-xs" onClick={() => act(() => manage({ op: 'restore', id: t.id }), '되돌렸다')}>되돌리기</button>
        </div>)}
        <button class="btn btn-bad !py-0.5 !px-2 text-xs mt-1.5" onClick={() => { if (confirm(`휴지통의 ${p.trash.length}개를 아주 없앨까요? 되돌릴 수 없다.`)) act(() => manage({ op: 'empty' }), '휴지통을 비웠다') }}>휴지통 비우기</button>
      </div>}
    </>
  )
}

// 점 둘: 허브(이 브라우저 ↔ 허브)와 모델 서버(허브 ↔ 모델 서버, 허브가 실제로 찔러 본 결과). 하나만 보고 "연결됨" 이라 읽지 않게 이름을 붙인다
export function ConnDot({ withText }) {
  const c = conn.value
  const [cls, txt] = c.state === 'ok' ? ['dot-ok', '허브 연결됨'] : c.state === 'login' ? ['dot-bad', '허브 로그인 필요'] :
    c.state === 'retry' ? [Date.now() - c.since > 15000 ? 'dot-bad' : 'dot-wait', `허브 다시 붙는 중(${c.tries})`] : ['dot-wait', '허브 붙는 중']
  tick.value
  return <span class="chip" title={'이 브라우저와 허브 사이의 연결(모델 서버와는 따로).' + (c.state === 'retry' ? ' 끊겼다: 허브가 꺼졌거나(터미널 확인) 망이 끊김. 저절로 다시 붙는다' : '')}>
    <span class={'dot ' + cls} />{withText !== false && txt}</span>
}

const MCLS = { ok: 'dot-ok', loading: 'dot-wait', down: 'dot-bad', auth: 'dot-bad', error: 'dot-bad', unknown: '' }
export function modelName(m) {
  // 서버가 실제로 올린 이름(+ 파드 별칭·어댑터). 설정 이름과 다르면 둘 다
  if (!m) return ''
  const served = (m.served || []).join(', ')
  const pod = m.pod ? [m.pod.model, m.pod.adapter].filter(Boolean).join(' / ') : ''
  let s = served || m.configured || ''
  if (pod && !s.includes(pod)) s += ` (${pod})`
  if (m.mismatch) s += ` · 설정 이름 ${m.configured}`
  return s
}
export function ModelDot({ withText }) {
  const m = hub.value?.model
  if (!m) return null
  const when = m.checked ? new Date(m.checked * 1000).toLocaleTimeString() : ''
  const tip = `모델 서버(${m.backend}): ${m.label}${m.detail ? ' — ' + m.detail : ''}\n서버가 올린 모델: ${(m.served || []).join(', ') || '(없음)'}` +
    (m.pod ? `\n장비 별칭·어댑터: ${[m.pod.model, m.pod.adapter].filter(Boolean).join(' / ')}` : '') + `\n설정 이름: ${m.configured}` + (when ? `\n확인: ${when}` : '')
  return <span class="chip" title={tip}><span class={'dot ' + (MCLS[m.state] || '')} />{withText !== false && `모델 ${m.label}`}</span>
}

export function Sidebar() {
  const [newOpen, setNewOpen] = useState(false)
  const ss = sessions.value
  const main = ss.find(s => s.role === 'main')
  const now = ss.filter(s => s.role !== 'main' && !['paused', 'stopped', 'failed'].includes(s.status))
  const st = hub.value
  return (
    <aside class="bg-panel border-r border-line flex flex-col min-w-0 h-full">
      <div class="flex items-center gap-2 px-4 py-3.5 font-[650]">
        <span class="w-[22px] h-[22px] rounded-md" style="background:linear-gradient(135deg,var(--c-accent),#9b6cf6)" />stewart hub
        <button class="btn btn-ghost !px-2 ml-auto md:hidden" onClick={() => { drawer.value = false }}>✕</button>
      </div>
      <Board />
      <button class="mx-3 mb-2 px-3 py-2 border border-line rounded-[10px] bg-soft font-semibold text-left" onClick={() => setNewOpen(true)}>＋ 새 작업자</button>
      <div class="flex-1 overflow-auto scroll-thin pb-2">
        <div class="px-4 pt-2.5 pb-1 text-xs text-mute font-semibold tracking-wide">지금</div>
        {main && <SessionItem s={main} />}
        {now.map(s => <SessionItem key={s.name} s={s} />)}
        <Past />
      </div>
      <div class="px-4 py-3 border-t border-line text-xs text-mute flex flex-wrap gap-2 items-center">
        <div class="w-full flex items-center gap-1.5 min-w-0"><ModelDot /><span class="truncate" title="서버가 실제로 올린 모델">{modelName(st?.model) || st?.backend?.model}</span></div>
        <ConnDot />
        <span class="flex-1" />
        <NotifToggle />
        {USER && <><span class="chip" title="로그인한 사람(계정 모드)">{USER}</span>
          <button class="underline-offset-2 hover:underline" onClick={() => post('/logout', {}).then(() => { location.href = '/login' })}>나가기</button></>}
        <a class="underline-offset-2 hover:underline" href="/?ui=old" title="예전 화면(되돌릴 길)">옛 화면</a>
      </div>
      {newOpen && <NewSession onClose={() => setNewOpen(false)} />}
    </aside>
  )
}
