// 왼쪽 사이드바(폰은 서랍): 현황판 자리 · ＋새 대화(새로·갈래·작업자 맡기기) · 대화 · 작업자 · 지난 대화 · 바닥(연결·알림·옛 화면)
import { useState, useEffect } from 'preact/hooks'
import { get, post, store, USER } from './lib.js'
import { hub, sessions, queue, view, drawer, refresh, toast, tick, clockSkew, conn, setPanel, chat, cpost, openChat } from './state.js'
import { NotifToggle } from './notify.jsx'
import { PlaceName, placeText } from './places.jsx'
import { ConvList } from './convlist.jsx'

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
    <button class={'mx-3 mb-2.5 border rounded-[10px] px-2.5 py-2 text-[13px] text-left w-[calc(100%-24px)] hover:bg-soft border-0 bg-soft px-3 py-2.5 ' + (n ? 'border-l-[3px] border-warn' : '')}
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
// ＋새 대화: 새로(빈 문맥, 격리) · 갈래(지금 보는 대화를 복사해 갈라짐) · 작업자 맡기기(메인이 일을 맡긴 것처럼 따로 프로세스)
// 판 120: 대화 칸의 종류 — 대화(묻고 찾고 건넴, 프로젝트 안 건드림) · 코드(폴더 하나를 맡겨 읽고 고치고 명령)
export const KIND_LABEL = { talk: placeText('talk'), code: placeText('code') }
export const kindTag = c => c?.kind ? KIND_LABEL[c.kind] + (c.tier ? ' · ' + c.tier : '') : ''
const KIND_KEY = 'stewart.newKind'
function lastKind() { try { return localStorage.getItem(KIND_KEY) || 'talk' } catch { return 'talk' } }
function NewChooser({ onClose }) {
  const [worker, setWorker] = useState(false)
  const [err, setErr] = useState('')
  const [kind, setKind] = useState(lastKind())
  const cur = (hub.value?.chats || []).find(c => c.name === chat.value)
  async function go(how) {
    try {
      const body = { how, from: chat.value }
      if (how === 'fresh') { body.kind = kind; try { localStorage.setItem(KIND_KEY, kind) } catch { /* 저장 못 해도 된다 */ } }
      const r = await cpost('/api/chat/new', body); onClose(); openChat(r.chat); refresh()
    } catch (e) { setErr(e.message) }
  }
  if (worker) return <NewSession onClose={onClose} />
  const kbtn = (k, desc) => <button key={k} aria-pressed={kind === k}
    class={'flex-1 text-left border rounded-xl px-3 py-2 ' + (kind === k ? 'border-accent bg-soft' : 'border-line hover:bg-soft')}
    onClick={() => setKind(k)}><div class="font-semibold"><PlaceName k={k} /></div><div class="text-xs text-mute">{desc}</div></button>
  return (
    <div class="fixed inset-0 z-50 bg-black/45 grid place-items-center p-4" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div class="w-full max-w-md bg-panel rounded-2xl p-4 space-y-2 shadow-card">
        <div class="font-semibold text-base mb-1">새 대화</div>
        <div class="flex gap-2 max-md:flex-col">
          {kbtn('talk', '묻고, 찾아 읽고, 글이나 파일을 받는다. 프로젝트 폴더는 건드리지 않는다')}
          {kbtn('code', '폴더 하나를 맡긴다. 읽고, 고치고, 명령을 돌린다')}
        </div>
        <button class="w-full text-left border border-line rounded-xl px-3 py-2.5 hover:bg-soft" onClick={() => go('fresh')}>
          <div class="font-semibold">새로 — <PlaceName k={kind} />, 빈 문맥</div><div class="text-xs text-mute">기억·현황판은 함께 쓰고, 앞 대화는 모른다(서로 격리). 종류는 도중에 바뀌지 않는다</div></button>
        <button class="w-full text-left border border-line rounded-xl px-3 py-2.5 hover:bg-soft disabled:opacity-50" disabled={!!cur?.busy} onClick={() => go('fork')}>
          <div class="font-semibold">이어서(갈래) — {cur?.title || '메인'} 을 복사</div><div class="text-xs text-mute">지금까지의 대화를 가지고 갈라진다(같은 종류{cur?.kind ? ' · ' + KIND_LABEL[cur.kind] : ''}). 이후로는 서로 독립{cur?.busy ? ' · 일하는 중이라 끝난 뒤' : ''}</div></button>
        <button class="w-full text-left border border-line rounded-xl px-3 py-2.5 hover:bg-soft" onClick={() => setWorker(true)}>
          <div class="font-semibold">작업자 맡기기</div><div class="text-xs text-mute">따로 도는 작업자에게 일 하나를 맡긴다(메인에 보고, 대화 칸과 다르다)</div></button>
        {err && <div class="text-bad text-sm">안 됨: {err}</div>}
        <div class="flex justify-end pt-1"><button class="btn" onClick={onClose}>닫기</button></div>
      </div>
    </div>
  )
}

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
        <div class="font-semibold text-base mb-1">작업자 맡기기 <span class="text-xs text-mute font-normal">— 따로 도는 작업자, 보고는 메인에</span></div>
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

function SessionItem({ s, label: label0 }) {
  const [label, dot] = STATUS[s.status] || [s.status, '']
  const asks = queue.value.filter(x => x.session === s.name).length
  tick.value
  const conv = s.role === 'main' || s.role === 'chat'   // 대화(메인·사람이 연 대화) — 바쁨은 허브 상태의 대화 목록에서
  const cinfo = (hub.value?.chats || []).find(c => c.name === s.name)
  const isMain0 = conv && chat.value === s.name
  let now = isMain0 ? (hub.value?.busy ? hub.value?.now : null) : conv ? null : s.now   // 보는 대화는 허브 상태의 지금 하는 일(등록부 값은 늦다)
  if (now && now.what.startsWith('사람 승인') && !asks) now = null    // 승인 글은 대기열에 실제 건이 있을 때만(같은 출처로 맞춘다)
  const secs = now ? Math.max(0, Math.round(Date.now() / 1000 - clockSkew.value - now.since)) : 0
  const on = view.value === s.name || (s.role === 'main' && view.value === 'main')
  const name = label0 || (s.role === 'main' ? '메인' : s.role === 'chat' ? (cinfo?.title || s.title || s.name) : s.name)
  const mainBusy = conv && !!cinfo?.busy
  return (
    <button class={'mx-2 my-px px-2.5 py-2 rounded-[9px] flex flex-col gap-px text-left w-[calc(100%-16px)] hover:bg-soft ' + (on ? 'bg-soft' : '')}
      onClick={() => { if (conv) openChat(s.role === 'main' ? 'main' : s.name); else { view.value = s.name; drawer.value = false } }}>
      <span class="font-[560] truncate w-full">{name}{conv && cinfo?.tier ? <span class="text-xs text-mute font-normal ml-1.5">{cinfo.tier}</span> : null}</span>
      {/* 판 124: 상태 줄은 도는 중·승인 대기·오류일 때만, 사람 말로(한가한 대화는 이름·종류만) */}
      {(() => {
        const failed = s.status === 'failed'
        const busyNow = conv ? mainBusy : ['working', 'waiting_approval'].includes(s.status)
        const txt = asks ? `승인 ${asks}건 대기` : failed ? '오류로 멈춤' : busyNow ? `일하는 중${now ? ` · ${secs < 60 ? secs + '초' : Math.floor(secs / 60) + '분'}` : ''}` : (!conv ? label : '')
        if (!txt) return null
        return <span class={'text-xs flex gap-1.5 items-center w-full min-w-0 ' + (asks ? 'text-warn' : failed ? 'text-bad' : 'text-mute')}>
          {(busyNow || asks) && <span class={'dot ' + (asks ? 'dot-wait' : 'dot-run')} />}
          <span class="truncate">{txt}{s.role === 'chat' && cinfo?.origin === 'fork' ? ' · 갈래' : ''}</span>
        </span>
      })()}
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
        {!picking && x.kind === 'main' && <button class="btn btn-ghost !px-2 !py-0.5 text-xs flex-none" title="메인 칸을 이 대화로 바꾼다(지금 메인 대화는 기록에 남는다)" onClick={() => act(() => post('/api/main/reopen', { run_dir: x.run_dir }).then(() => openChat('main')))}>메인으로</button>}
        {!picking && x.kind === 'main' && <button class="btn btn-ghost !px-2 !py-0.5 text-xs flex-none" title="새 대화 칸으로 연다(메인은 그대로)" onClick={() => act(() => post('/api/chat/new', { how: 'reopen', run_dir: x.run_dir }).then(r => openChat(r.chat)))}>새 칸</button>}
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
  const rows = [...p.workers, ...(arch ? p.mains : [])]   // 판 124.2: 지난 대화는 위 목록에 — 여기는 작업자·보관함·휴지통
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
        <button class="text-left flex-1" onClick={toggle}>{arch ? '보관함' : n ? '지난 작업자' : '보관함·휴지통'}{n ? ' ' + n : ''} {open ? '▴' : '▾'}</button>
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
        {(p.archived_count > 0 || arch) && <button class="hover:text-fg" onClick={() => { setArch(!arch); setSel({}) }}>{arch ? '← 지난 작업자' : `보관함 ${p.archived_count}`}</button>}
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

// 바깥 API(회사 서버로 작업 내용이 나간다): 늘 보이게 — 제공자 이름
// 판 103(SW-45 ②): 머리줄 모드·생각 — 켤 때 정한 모드보다 넓히지 못한다
const MODE_KO = { plan: '계획', ask: '묻기', edits: '편집', auto: '자동' }
const RANK = { plan: 0, ask: 1, edits: 2, auto: 3 }
export function ModeChips() {
  const st = hub.value
  if (!st?.mode) return null
  const modes = Object.keys(RANK).filter(m => RANK[m] <= RANK[st.mode_max || 'edits'])
  const setMode = mode => cpost('/api/main/mode', { mode }).then(refresh, e => toast('안 됨: ' + e.message))
  const nextThink = st.think == null ? true : st.think ? false : null
  const setThink = () => cpost('/api/main/think', { think: nextThink }).then(refresh, e => toast('안 됨: ' + e.message))
  const setBudget = () => {
    const v = window.prompt('이 대화의 문맥 예산(토큰, 예 24000). 비우면 백엔드 설정 그대로.', String(st.budget || ''))
    if (v === null) return
    const n = v.trim() === '' ? null : parseInt(v.replace(/[^0-9]/g, ''), 10)
    if (n !== null && !(n > 0)) { toast('숫자를 넣는다'); return }
    cpost('/api/main/budget', { tokens: n }).then(refresh, e => toast('안 됨: ' + e.message))
  }
  return <span class="flex gap-1 items-center flex-none">
    <select class="chip flex-none !pr-1" value={st.mode} title="모드: 계획(읽기만)·묻기(바꾸기 전 묻기)·편집(파일은 바로, 셸은 묻기)·자동"
      onChange={e => setMode(e.currentTarget.value)} aria-label="모드">
      {modes.map(m => <option key={m} value={m}>{MODE_KO[m]}</option>)}
    </select>
    <button class={'chip flex-none ' + (st.think ? '!text-ok' : '')} onClick={setThink}
      title={'생각(thinking): ' + (st.think == null ? '백엔드 기본' : st.think ? '켬' : '끔') + ' — 누르면 ' + (nextThink == null ? '기본' : nextThink ? '켬' : '끔')}>
      생각 {st.think == null ? '기본' : st.think ? '켬' : '끔'}</button>
    {st.budget && <button class={'chip flex-none ' + (st.budget_set ? '!text-warn' : '')} onClick={setBudget}
      title="이 대화의 문맥 예산(압축 문턱을 재는 창) — 누르면 바꾼다. 서버 창보다 크게는 못 한다. 줄이면 압축·요약이 일찍 돈다">
      문맥 {Math.round(st.budget / 1000)}k</button>}
  </span>
}

// 판 99: 도구 켬·끔 손잡이(프로필 toggle_tools) — 꺼진 도구는 모델에게 안 보인다
export function ToolChips() {
  const t = hub.value?.tool_toggles || []
  const [open, setOpen] = useState(false)
  if (!t.length) return null
  const toggle = (name, on) => cpost('/api/tools/toggle', { name, on }).then(refresh, e => toast('안 됨: ' + e.message))
  const chip = x => <button key={x.name} class={'chip flex-none ' + (x.on ? '!text-ok' : '')} aria-pressed={x.on}
    title={x.on ? `${x.name} 켜짐 — 누르면 끈다(모델에게 안 보임)` : `${x.name} 꺼짐 — 누르면 켠다`}
    onClick={() => toggle(x.name, !x.on)}>{x.on ? '●' : '○'} {x.name}</button>
  if (t.length <= 4) return <span class="flex gap-1 items-center flex-none">{t.map(chip)}</span>
  // 판 113: 칩이 많으면(얇은 메인) 켠 것만 보이고 나머지는 [도구 N ▾] 로 접는다
  const on = t.filter(x => x.on), off = t.filter(x => !x.on)
  return <span class="flex gap-1 items-center flex-none relative">
    {on.map(chip)}
    <button class="chip flex-none" aria-expanded={open} title="꺼 둔 도구 — 눌러 펼치고 칩을 눌러 켠다"
      onClick={() => setOpen(!open)}>도구 {off.length} {open ? '▴' : '▾'}</button>
    {open && <span class="absolute top-full right-0 z-20 mt-1 p-2 bg-panel border border-line rounded-lg shadow-card flex flex-wrap gap-1 w-72">
      {off.map(chip)}</span>}
  </span>
}

export function ExternalChip({ plain }) {
  const b = hub.value?.backend, ext = b?.external
  if (!ext) return null
  const u = b.used || {}
  const k = n => n >= 1000000 ? (n / 1000000).toFixed(1) + 'M' : n >= 1000 ? Math.round(n / 1000) + 'k' : String(n || 0)
  const short = ext.replace(/\(.*\)/, '').trim()
  const cache = u.cache_seen ? Math.round(100 * (u.cache_hit || 0) / u.cache_seen) : null   // 캐시 칸을 주는 API 만(판 86)
  return <span class={plain ? 'text-warn' : 'chip !text-warn flex-none'} title={`바깥 API: ${ext} — 이 대화의 글·파일 내용·도구 결과가 그 회사 서버로 나간다(그 회사 약관이 적용 — 학습에 쓰일 수 있다)\n이 허브가 켜진 뒤 누계: 호출 ${u.calls || 0}번 · 입력 ${u.prompt || 0} · 출력 ${u.completion || 0} 토큰` + (u.cost_usd ? ` · $${u.cost_usd}` : '')}>
    {!plain && <span class="max-md:hidden">바깥 API: </span>}{short}<span> · 호출 {u.calls || 0} · 토큰 {k((u.prompt || 0) + (u.completion || 0))}</span>
    {cache != null && <span> · 캐시 {cache}%</span>}{u.cost_usd ? <span> · ${u.cost_usd < 0.01 ? u.cost_usd.toFixed(4) : u.cost_usd.toFixed(2)}</span> : null}</span>
}

export function Sidebar() {
  const [newOpen, setNewOpen] = useState(false)
  const ss = sessions.value
  const main = ss.find(s => s.role === 'main')
  const chats = ss.filter(s => s.role === 'chat')
  const now = ss.filter(s => s.role !== 'main' && s.role !== 'chat' && !['paused', 'stopped', 'failed'].includes(s.status))
  const st = hub.value
  return (
    <aside class="bg-bg border-r border-line flex flex-col min-w-0 h-full">
      <div class="flex items-center gap-2 px-4 py-3.5 font-[650]">
        stewart
        <button class="ibtn ml-auto md:hidden text-mute" onClick={() => { drawer.value = false }} aria-label="서랍 닫기">✕</button>
      </div>
      <Board />
      <button class="mx-3 mb-2 px-3 py-2 rounded-[10px] font-semibold text-left min-h-[44px] hover:bg-soft" onClick={() => setNewOpen(true)}>＋ 새 대화</button>
      <div class="flex-1 overflow-auto scroll-thin pb-2">
        {/* 판 124.2: 열린 칸과 지난 대화를 한 목록에(자리 묶음 안 날짜순) — convlist.jsx */}
        <ConvList Item={SessionItem} />
        {now.length > 0 && <div class="px-4 pt-2.5 pb-1 text-xs text-mute font-semibold tracking-wide" title="메인(또는 사람)이 일을 맡긴 작업자 — 보고를 메인에 낸다">작업자 · 맡긴 일</div>}
        {now.map(s => <SessionItem key={s.name} s={s} />)}
        <Past />
      </div>
      <div class="px-4 py-3 border-t border-line text-[12.5px] text-mute space-y-1">
        {/* 판 124: 상태 알약 대신 보조 글 한 줄 — 정상인 연결·모델 상태는 적지 않는다 */}
        <div class="truncate" title="서버가 실제로 올린 모델">모델 {modelName(st?.model) || st?.backend?.model}
          {st?.model && !['ok', 'unknown'].includes(st.model.state) ? <span class="text-warn"> · {st.model.label}</span> : null}
          {st?.web ? ' · ' + (st.web.search ? '웹 검색·읽기' : st.web.fetch ? '웹 읽기만' : '웹 꺼짐') : ''}</div>
        {conn.value.state !== 'ok' && <div class="text-warn"><ConnDot /></div>}
        <div class="flex gap-3 items-center">
          <NotifToggle />
          {USER && <><span>{USER}</span>
            <button class="hover:underline" onClick={() => post('/logout', {}).then(() => { location.href = '/login' })}>나가기</button></>}
          <a class="hover:underline" href="/?ui=old" title="예전 화면(되돌릴 길)">옛 화면</a>
        </div>
      </div>
      {newOpen && <NewChooser onClose={() => setNewOpen(false)} />}
    </aside>
  )
}
