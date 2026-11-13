// 판 124.2: 대화 목록 — 열린 칸과 지난 대화를 한 목록에. 자리 묶음(stewart parlando · stewart) 안에서 마지막으로 손댄 순,
// 날짜 머리(오늘·어제·지난 7일·더 전). 지난 대화를 누르면 알아서 다시 열고(새 칸), 길게 누르기(데스크톱은 ⋯·오른쪽 단추)로 이름·보관·지우기.
// 맨 위 찾기 칸은 제목을 바로 거르고, 두 글자 넘으면 내용도 찾는다. 자리 고르기: 전체 · stewart · parlando.
import { useState, useEffect, useRef } from 'preact/hooks'
import { signal } from '@preact/signals'
import { get, post, store } from './lib.js'
import { hub, sessions, view, drawer, refresh, toast, openChat, chat } from './state.js'
import { PlaceName, placeText } from './places.jsx'
import { IconMore } from './icons.jsx'

export const past = signal({ workers: [], mains: [], archived_count: 0, trash: [] })
export const loadPast = () => get('/api/past_sessions').then(r => { past.value = r }).catch(() => {})

const DEFAULT_TITLE = /^(메인|대화 \d+|갈래 \d+|다시 연 대화 \d+)$/
const BUCKETS = ['오늘', '어제', '지난 7일', '더 전']
function bucket(t) {
  if (!t) return 0
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(new Date()) - day(new Date(t * 1000))) / 86400000)
  return diff <= 0 ? 0 : diff === 1 ? 1 : diff < 7 ? 2 : 3
}

// 길게 누르기 — 눌림이 끝난 뒤 따라오는 click 은 한 번 삼킨다(메뉴만 열리고 대화가 열리지 않게)
function usePress(onTap, onHold) {
  const r = useRef({ t: null, held: false })
  const cancel = () => { if (r.current.t) { clearTimeout(r.current.t); r.current.t = null } }
  return {
    onTouchStart: () => { r.current.held = false; cancel(); r.current.t = setTimeout(() => { r.current.t = null; r.current.held = true; onHold() }, 480) },
    onTouchEnd: cancel, onTouchMove: cancel,
    onContextMenu: e => { e.preventDefault(); cancel(); r.current.held = true; onHold() },
    onClick: () => { if (r.current.held) { r.current.held = false; return } onTap() },
  }
}

function Menu({ items, onClose }) {
  return <>
    <div class="fixed inset-0 z-20" onClick={onClose} />
    <div class="absolute right-2 top-10 z-30 bg-panel border border-line rounded-xl shadow-card text-[14px] py-1 min-w-44" role="menu">
      {items.map(([label, fn, bad]) => <button key={label} role="menuitem" class={'block w-full text-left px-3.5 py-2.5 hover:bg-soft ' + (bad ? 'text-bad' : '')}
        onClick={() => { onClose(); fn() }}>{label}</button>)}
    </div>
  </>
}

async function manage(body) {
  const r = await post('/api/sessions/manage', body)
  if (r.failed?.length) throw new Error(r.failed.map(f => f.detail).join(' / '))
  return r
}

// 줄 하나(열린 칸·지난 대화 같은 꼴). mark: 자리가 섞여 보일 때(찾기 결과) 줄 끝의 작은 자리 표시
function Row({ label, on, sub, mark, onOpen, menu, title }) {
  const [m, setM] = useState(false)
  const press = usePress(onOpen, () => menu?.length && setM(true))
  return <div class="group relative mx-2 my-px">
    <button class={'px-2.5 py-2 rounded-[9px] w-full text-left hover:bg-soft flex flex-col gap-px ' + (on ? 'bg-soft' : '')} title={title || label} {...press}>
      <span class="flex items-baseline gap-2 w-full min-w-0 pr-7 md:pr-0 md:group-hover:pr-7">
        <span class="font-[560] truncate flex-1 min-w-0">{label}</span>{mark}</span>
      {sub}
    </button>
    {menu?.length > 0 && <button class="max-md:hidden absolute right-1.5 top-1.5 w-8 h-8 grid place-items-center rounded-lg text-mute hover:bg-line/60 opacity-0 group-hover:opacity-100 focus:opacity-100"
      aria-label="대화 메뉴" onClick={() => setM(!m)}><span class="block w-[18px] h-[18px]"><IconMore /></span></button>}
    {m && <Menu items={menu} onClose={() => setM(false)} />}
  </div>
}

const Mark = ({ k, folder }) => <span class="text-[12px] text-mute flex-none max-w-[40%] truncate">{k === 'talk' ? <i>parlando</i> : (folder || placeText('code'))}</span>

function reopen(m, label) {
  return post('/api/chat/new', { how: 'reopen', run_dir: m.run_dir, title: (label || '').slice(0, 80) })
    .then(r => { openChat(r.chat); refresh(); loadPast() }, e => toast('안 됨: ' + e.message))
}

function pastMenu(m, label) {
  const item = { kind: 'main', key: m.run_dir }
  const act = (fn, ok) => fn().then(() => { if (ok) toast(ok, 'ok', 4000); refresh(); loadPast() }, e => toast('안 됨: ' + e.message))
  return [
    ['이름 바꾸기', () => { const t = prompt('새 이름(비우면 첫 말로)', m.title || ''); if (t !== null) act(() => manage({ op: 'rename', items: [item], title: t })) }],
    ['보관(목록에서 숨김)', () => act(() => manage({ op: 'archive', items: [item] }), '보관함으로 옮겼다 — 아래 "보관함" 에서 되돌린다')],
    ['지우기', () => { if (confirm(`"${label}" 을 휴지통으로 옮길까요?\n대화 기록만 옮기고 작업 폴더의 파일은 그대로다. 휴지통을 비우기 전까지 되돌릴 수 있다.`)) act(() => manage({ op: 'delete', items: [item] }), '휴지통으로 옮겼다 — 아래 휴지통에서 되돌린다') }, true],
  ]
}

export function ConvList({ Item }) {
  const st = hub.value
  const ss = sessions.value
  const [placeF, setPlaceF] = useState(store.get('stewart-place-filter', 'all'))
  const [q, setQ] = useState(''), [hits, setHits] = useState(null)
  const [more, setMore] = useState({})
  const runsKey = (st?.chats || []).map(c => c.run_dir).join('|')
  useEffect(() => { loadPast(); const t = setInterval(loadPast, 30000); return () => clearInterval(t) }, [])
  useEffect(() => { loadPast() }, [runsKey])   // 칸을 열고 닫을 때마다(켜자마자도) 다시 받는다
  useEffect(() => {
    const s = q.trim()
    if (s.length < 2) { setHits(null); return }
    const t = setTimeout(() => get('/api/past_search?q=' + encodeURIComponent(s)).then(r => setHits(r.hits || []), () => setHits([])), 350)
    return () => clearTimeout(t)
  }, [q])
  const setF = v => { setPlaceF(v); store.set('stewart-place-filter', v) }

  const main = ss.find(s => s.role === 'main')
  const chats = ss.filter(s => s.role === 'chat')
  const info = name => (st?.chats || []).find(c => c.name === name) || {}
  const openRows = [...(main ? [main] : []), ...chats].map(s => {
    const c = info(s.role === 'main' ? 'main' : s.name)
    const title = c.title || s.title || (s.role === 'main' ? '메인' : s.name)
    return { open: true, s, kind: c.kind || 'code', last: c.last || 0, label: (!DEFAULT_TITLE.test(title) && title) || c.first || title, run: c.run_dir }
  })
  const pastRows = (past.value.mains || []).map(m => ({ open: false, m, kind: m.place || 'code', last: m.last || 0, label: m.title || m.first || '대화', run: m.run_dir }))
  const needle = q.trim().toLowerCase()
  const all = [...openRows, ...pastRows].filter(r => !needle || r.label.toLowerCase().includes(needle))
  const kinds = placeF === 'all' ? ['talk', 'code'] : [placeF]
  const hitRuns = new Set(all.map(r => r.run))

  const row = r => r.open
    ? <Item key={'o' + r.s.name} s={r.s} label={r.label} />
    : <Row key={'p' + r.run} label={r.label} title={r.m.first} onOpen={() => reopen(r.m, r.label)} menu={pastMenu(r.m, r.label)}
        mark={r.kind === 'code' && !r.m.here && r.m.folder ? <Mark k="code" folder={r.m.folder} /> : null} />

  return <>
    <div class="mx-3 mb-1 flex flex-col gap-1.5">
      <input class="w-full text-[14px] px-3 py-2 rounded-[10px] bg-soft placeholder:text-mute outline-none focus:ring-1 focus:ring-line" placeholder="대화 찾기"
        value={q} onInput={e => setQ(e.currentTarget.value)} aria-label="대화 찾기(제목·내용)" />
      <div class="flex gap-3 px-1 text-[13px]" role="tablist" aria-label="자리 고르기">
        {[['all', '전체'], ['code', placeText('code')], ['talk', null]].map(([v, t]) =>
          <button key={v} role="tab" aria-selected={placeF === v} class={placeF === v ? 'text-fg font-semibold' : 'text-mute hover:text-fg'}
            onClick={() => setF(v)}>{t ?? <i>parlando</i>}</button>)}
      </div>
    </div>
    {kinds.map(k => {
      const list = all.filter(r => r.kind === k).sort((a, b) => (b.last || 0) - (a.last || 0))
      if (!list.length) return null
      const LIM = 30, shown = more[k] ? list : list.slice(0, LIM)
      let prev = -1
      return <div key={k}>
        {placeF === 'all' && <div class="px-4 pt-3 pb-0.5 text-[12.5px] text-mute" title={k === 'talk' ? '묻고, 찾아 읽고, 글이나 파일을 받는다. 프로젝트 폴더는 건드리지 않는다' : '폴더 하나를 맡긴다. 읽고, 고치고, 명령을 돌린다'}>
          <PlaceName k={k} /></div>}
        {shown.map(r => {
          const b = bucket(r.last)
          const head = b !== prev ? <div key={'b' + k + b} class="px-4 pt-2 pb-0.5 text-[11.5px] text-mute/80">{BUCKETS[b]}</div> : null
          prev = b
          return [head, row(r)]
        })}
        {list.length > LIM && <button class="mx-4 py-1 text-xs text-mute hover:text-fg" onClick={() => setMore({ ...more, [k]: !more[k] })}>{more[k] ? '접기' : `${list.length - LIM}개 더 보기`}</button>}
      </div>
    })}
    {needle && !all.some(r => kinds.includes(r.kind)) && !(hits || []).length && <div class="mx-4 py-2 text-[13px] text-mute">맞는 대화 없음</div>}
    {hits && hits.filter(h => !hitRuns.has(h.run_dir) && kinds.includes(h.kind || 'code')).length > 0 && <div>
      <div class="px-4 pt-3 pb-0.5 text-[12.5px] text-mute">내용에서</div>
      {hits.filter(h => !hitRuns.has(h.run_dir) && kinds.includes(h.kind || 'code')).map(h => {
        const open = (st?.chats || []).find(c => c.run_dir === h.run_dir)
        return <Row key={'h' + h.run_dir} label={h.first || '(첫 말 없음)'} mark={<Mark k={h.kind || 'code'} folder={h.folder} />}
          sub={<span class="text-xs text-mute truncate w-full">{h.snippet}</span>}
          onOpen={() => open ? openChat(open.name) : reopen(h, h.first)} />
      })}
    </div>}
  </>
}
