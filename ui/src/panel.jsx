// 오른쪽 패널(데스크톱, 기본 닫힘) / 아래 시트(폰): 단계 · 계획 · 늘 허용 · 토큰
import { useState, useEffect } from 'preact/hooks'
import { get, post, fmtK, fmtSecs } from './lib.js'
import { hub, panel, setPanel, sessions, toast, refresh, busy, cpost } from './state.js'
import { STATUS } from './side.jsx'

const TABS = [['board', '현황판'], ['memory', '기억'], ['steps', '단계'], ['plan', '계획'], ['rules', '늘 허용'], ['tokens', '토큰']]

function Steps() {
  const log = (hub.value?.main_log || []).filter(m => m.kind !== 'info' || !/^ {4}/.test(m.text))
  if (!log.length) return <div class="text-mute text-sm p-4">아직 없음</div>
  return (
    <div class="px-3.5 py-2 text-[13px]">
      {log.slice().reverse().map((m, i) => (
        <div key={i} class="grid grid-cols-[58px_1fr] gap-2 py-1.5 border-b border-soft">
          <span class="text-mute text-xs pt-px">{m.ts}</span>
          <span class={'min-w-0 break-words ' + (m.kind === 'tool' ? 'font-mono text-[12.5px]' : m.kind === 'warn' ? 'text-warn' : m.kind === 'user' ? 'font-semibold' : m.kind === 'info' ? 'text-mute' : '')}>
            {m.kind === 'user' ? '사람: ' : m.kind === 'assistant' ? '모델: ' : m.kind === 'diff' ? '± ' : ''}
            {m.kind === 'diff' ? `${m.stat?.path || ''} +${m.stat?.plus ?? ''} −${m.stat?.minus ?? ''}` : m.text.slice(0, 300)}
            {m.kind === 'tool' && m.ok != null && <span class={m.ok ? 'text-ok' : 'text-bad'}> {m.ok ? '✓' : '✕'}</span>}
            {m.rep > 1 && <span class="text-bad"> (같은 오류 {m.rep}번째)</span>}
          </span>
        </div>
      ))}
    </div>
  )
}

function Plan() {
  const todos = hub.value?.todos || [], ws = sessions.value.filter(s => s.role !== 'main')
  const n = s => todos.filter(t => t.status === s).length
  return (
    <div class="px-4 py-3 text-[13.5px] space-y-3">
      <div>
        <div class="font-semibold mb-1">메인 할 일</div>
        {!todos.length ? <div class="text-mute">없음</div> : <>
          <div class="text-xs text-mute mb-1">{todos.length}개 · 끝 {n('done')} · 하는 중 {n('in_progress')} · 남음 {n('pending')}</div>
          {todos.map((t, i) => <div key={i} class={'py-0.5 ' + (t.status === 'done' ? 'text-mute line-through' : t.status === 'in_progress' ? 'font-semibold' : '')}>
            {t.status === 'done' ? '✓ ' : t.status === 'in_progress' ? '▶ ' : '○ '}{t.content}</div>)}
        </>}
      </div>
      <div>
        <div class="font-semibold mb-1">작업자</div>
        {!ws.length ? <div class="text-mute">없음</div> : ws.map(w => <div key={w.name} class="py-0.5">⇢ {w.name} · {(STATUS[w.status] || [w.status])[0]}{w.now ? ` · ${w.now.what.slice(0, 60)}` : ''}</div>)}
      </div>
    </div>
  )
}

function Rules() {
  const [rules, setRules] = useState(null), [tests, setTests] = useState(null)
  const load = () => {
    get('/api/rules').then(r => setRules(r.rules || [])).catch(() => setRules([]))
    get('/api/test_commands').then(r => setTests(r.offers || [])).catch(() => setTests([]))
  }
  useEffect(load, [])
  const act = async fn => { try { await fn(); load() } catch (e) { toast('안 됨: ' + e.message) } }
  const [pat, setPat] = useState(''), [scope, setScope] = useState('project')
  async function add() {
    try {
      const r = await post('/api/rules/add', { pattern: pat.trim(), scope })
      setPat(''); load()
      toast('더함: ' + r.pattern + (r.warn ? ' — ' + r.warn : ''), r.warn ? 'bad' : 'ok')
    } catch (e) { toast('못 더함: ' + e.message) }
  }
  return (
    <div class="px-4 py-3 text-[13.5px] space-y-4">
      <div>
        <div class="font-semibold mb-1">늘 허용 더하기</div>
        <input class="field font-mono" placeholder="npm test · uv run pytest * · python3 -c * · Bash(npm run test:*)" value={pat}
          onInput={e => setPat(e.currentTarget.value)} onKeyDown={e => { if (e.key === 'Enter' && pat.trim()) add() }} />
        <div class="flex items-center gap-3 mt-1.5 flex-wrap text-[13px]">
          <label class="flex items-center gap-1"><input type="radio" checked={scope === 'project'} onChange={() => setScope('project')} />이 프로젝트 늘</label>
          <label class="flex items-center gap-1"><input type="radio" checked={scope === 'session'} onChange={() => setScope('session')} />이 세션만</label>
          <button class="btn !py-1 ml-auto" disabled={!pat.trim()} onClick={add}>더하기</button>
        </div>
        <div class="text-xs text-mute mt-1">정확한 명령 · "앞부분 *"(낱말 경계) · "글*"(글 앞부분). python3 -c * 처럼 넓은 꼴도 받지만 그 프로그램으로 도는 모든 코드를 묻지 않고 돌린다. sudo·git push·curl·rm -rf 같은 위험 명령은 받지 않는다(한 번 허용만).</div>
      </div>
      <div>
        <div class="font-semibold mb-1">늘 허용(셸 꼴)</div>
        {rules === null ? <div class="text-mute">읽는 중…</div> : !rules.length ? <div class="text-mute">없음 — 승인 칸의 [늘 허용 ▾]로 더한다</div> :
          rules.map(r => <div key={r.id} class="flex items-center gap-2 py-1 border-b border-soft">
            <span class="flex-1 min-w-0"><code class="font-mono text-[12.5px] break-all">{r.pattern.length > 200 ? r.pattern.slice(0, 200) + '…' : r.pattern}</code>
              {r.warn && <span class="block text-xs text-bad">⚠ {r.warn}</span>}</span>
            <span class="chip">{r.scope === 'session' ? '이 세션만' : '프로젝트'}</span>
            <button class="btn btn-ghost btn-bad !px-2 !py-0.5 text-xs" onClick={() => act(() => post('/api/rules/delete', { id: r.id }))}>지우기</button>
          </div>)}
      </div>
      <div>
        <div class="font-semibold mb-1">프로젝트 시험 명령</div>
        {tests === null ? <div class="text-mute">읽는 중…</div> : !tests.length ? <div class="text-mute">찾은 것 없음(AGENTS.md·README 의 `시험 명령`)</div> :
          tests.map((o, i) => <div key={i} class="py-1 border-b border-soft">
            <code class="font-mono text-[12.5px] break-all">{o.command}</code>
            <div class="mt-0.5">{o.allowed ? <span class="chip">허용됨</span> : !o.pattern ? <span class="text-xs text-mute">{o.reason}</span> :
              <button class="btn !py-0.5 text-xs" onClick={() => act(() => post('/api/rules/test_allow', { pattern: o.pattern, scope: 'project' }))}>늘 허용: {o.pattern}</button>}</div>
          </div>)}
      </div>
    </div>
  )
}

function BoardTab() {
  const b = hub.value?.board || { open: [], later: [], closed: [], progress: [], done: [], decision: [] }
  const later = b.later || []
  const q = hub.value?.queue || []
  const [out, setOut] = useState({})
  async function close(id, status) {
    try { await post('/api/board/close', { id, status, outcome: out[id] || '' }); refresh() } catch (e) { toast('못 닫음: ' + e.message) }
  }
  const H = ({ children }) => <div class="font-semibold mb-1 mt-3 first:mt-0">{children}</div>
  return (
    <div class="px-4 py-3 text-[13.5px]">
      <H>사용자 할 일 {b.open.length + q.length}</H>
      {!b.open.length && !q.length && <div class="text-mute">없음</div>}
      {q.map((it, i) => <div key={'q' + i} class="py-1 border-b border-soft"><b class="text-warn">승인</b> {it.session} #{it.seq} · {it.args_summary?.tool || it.kind}
        <div class="text-xs text-mute truncate">{(it.args_summary?.command || it.prompt || '').slice(0, 120)}</div></div>)}
      {b.open.map(t => <div key={t.id} class="py-1.5 border-b border-soft">
        <div><b class="text-warn mr-1">{t.id}</b>{t.title} <span class="chip">{t.type}</span>{t.why ? <span class="chip !text-bad ml-1">{t.why}</span> : t.urgency !== '보통' && <span class="chip !text-bad ml-1">{t.urgency}</span>}{t.source && <span class="chip ml-1">{t.source} 보고</span>}</div>
        {t.detail && <div class="text-xs text-mute whitespace-pre-wrap mt-0.5">{t.detail}</div>}
        <div class="flex gap-1.5 mt-1 items-center">
          <input class="field !py-0.5 text-xs" placeholder="결과 한 줄(선택)" value={out[t.id] || ''} onInput={e => setOut({ ...out, [t.id]: e.currentTarget.value })} />
          <button class="btn btn-ok !py-0.5 !px-2 text-xs flex-none" onClick={() => close(t.id, 'done')}>했음</button>
          <button class="btn !py-0.5 !px-2 text-xs flex-none" onClick={() => close(t.id, 'dropped')}>안 함</button>
        </div>
      </div>)}
      {later.length > 0 && <><H>아직 아님 {later.length}</H>{later.map(t => <div key={t.id} class="py-1 border-b border-soft text-mute">
        <b class="mr-1">{t.id}</b>{t.title} <span class="chip">{t.later}</span>
        {t.detail && <div class="text-xs whitespace-pre-wrap mt-0.5">{t.detail}</div>}</div>)}</>}
      <H>진행 중</H>
      {!b.progress.length ? <div class="text-mute">없음</div> : b.progress.map((p, i) => <div key={i} class="py-0.5">▶ {p.what}{p.next && <span class="text-mute"> → {p.next}</span>}</div>)}
      <H>끝난 일</H>
      {!b.done.length ? <div class="text-mute">없음</div> : b.done.map((x, i) => <div key={i} class="py-0.5 text-mute"><span class="text-ok">✓</span> {x.what} <span class="text-xs">{x.at.slice(5)}</span></div>)}
      <H>정한 것</H>
      {!b.decision.length ? <div class="text-mute">없음</div> : b.decision.map((x, i) => <div key={i} class="py-0.5 border-b border-soft">
        <span class="chip">{x.by}</span> {x.what}{!x.reversible && <span class="chip !text-bad ml-1">되돌릴 수 없음</span>}
        {x.why && <div class="text-xs text-mute">{x.why}</div>}</div>)}
      {b.closed.length > 0 && <><H>닫은 할 일</H>{b.closed.map(t => <div key={t.id} class="py-0.5 text-mute line-through">{t.id} {t.title}<span class="no-underline"> · {t.status === 'done' ? '했음' : '안 함'}{t.outcome ? ' — ' + t.outcome : ''}</span></div>)}</>}
      <div class="mt-3"><a class="btn !py-0.5 !px-2 text-xs" href="/api/board/ledger" download="stewart-ledger.md">결정 장부 내려받기(.md)</a></div>
      <div class="text-xs text-mute mt-2">판은 메인이 board 도구로 쓰고, 작업자 보고의 '사용자 할 일:' 절도 저절로 올라온다. 순서: 돈·장애 → 마감(이틀 안) → 남을 막음 → 나머지. 새 사용자 할 일(풀려 이제 할 수 있게 된 것 포함)은 폰(ntfy)으로 — 23:00~07:30 은 돈·장애만. 차례 끝 답에 열린 할 일 꼬리표가 붙는다.</div>
    </div>
  )
}

const MKIND = { policy: '방침', profile: '프로필', knowledge: '자료',
  explicit: '기억해 달라 함', feedback: '지적·지시', user: '사용자', project: '이 프로젝트', reference: '참고' }
const KIND3 = [['policy', '방침', '어떻게 일할지 — 늘 지시문에'], ['profile', '프로필', '사용자에 대한 짧은 사실 — 늘 지시문에(600바이트)'],
  ['knowledge', '자료', '고쳐 준 사실·참고 — 지시문에 싣지 않고 doc_search 로 찾음']]
const MSRC = { said: '사람 말에서', model: '모델이 알아챔', human: '사람이 넣음' }
function MemoryTab() {
  const [d, setD] = useState(null), [edit, setEdit] = useState({}), [add, setAdd] = useState(''), [kind, setKind] = useState('policy')
  const load = () => get('/api/memory').then(setD).catch(() => setD({ on: false, items: [], deny: [] }))
  useEffect(load, [])
  const act = async (path, body) => { try { await post(path, body); load() } catch (e) { toast('안 됨: ' + e.message) } }
  if (!d) return <div class="text-mute text-sm p-4">읽는 중…</div>
  if (!d.on) return <div class="text-mute text-sm p-4">기억이 꺼져 있다(STEWART_MEMORY=0 또는 프로필 memory = false).</div>
  return (
    <div class="px-4 py-3 text-[13.5px]">
      <div class="font-semibold mb-1">기억 더하기</div>
      <div class="flex gap-1.5">
        <select class="field !w-32 flex-none" value={kind} onChange={e => setKind(e.currentTarget.value)}>
          {(d.md ? KIND3.map(([k, v]) => [k, v]) : Object.entries(MKIND).slice(3)).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <input class="field" placeholder="다음 대화부터 지킬 것 한 문장" value={add} onInput={e => setAdd(e.currentTarget.value)}
          onKeyDown={e => { if (e.key === 'Enter' && add.trim()) { act('/api/memory/add', { text: add.trim(), kind }); setAdd('') } }} />
        <button class="btn flex-none" disabled={!add.trim()} onClick={() => { act('/api/memory/add', { text: add.trim(), kind }); setAdd('') }}>더하기</button>
      </div>
      <div class="text-xs text-mute mt-1 mb-3">손으로 쓴 규칙 파일(~/.config/stewart/STEWART.md · 프로젝트 AGENTS.md·STEWART.md)이 정본이다 — 기억과 부딪히면 규칙 파일이 이긴다.</div>
      {(d.pending || []).length > 0 && <><div class="font-semibold mb-1">물어보는 중 {d.pending.length}</div>
        {d.pending.map(x => <div key={x.id} class="py-1.5 border-b border-soft">
          <div class="break-words">{x.text}</div>
          <div class="flex gap-2 mt-1 text-xs">
            <button class="text-ok hover:underline" onClick={() => act('/api/memory/confirm', { id: x.id, keep: true })}>허용(방침으로)</button>
            <button class="text-mute hover:text-fg" onClick={() => act('/api/memory/confirm', { id: x.id, keep: false })}>이번만</button></div>
        </div>)}<div class="mb-3" /></>}
      {d.md && d.inject && <div class="text-xs text-mute mb-2">지시문에 실림: 방침 {d.inject.policy?.[0] ?? 0}/{d.inject.policy?.[1] ?? 0} · 프로필 {d.inject.profile?.[0] ?? 0}/{d.inject.profile?.[1] ?? 0} · 자료 {d.inject.knowledge ?? 0}건은 싣지 않고 찾아 씀
        {d.inject.profile_over && <span class="text-warn"> · 프로필이 600바이트를 넘어 오래된 것이 빠졌다 — 줄여 달라</span>}
        {d.dir && <div>파일(직접 열어 고쳐도 된다): {d.dir}/policy.md · profile.md · knowledge/</div>}</div>}
      <div class="font-semibold mb-1">기억 {d.items.length}</div>
      {!d.items.length && <div class="text-mute">아직 없음 — 대화 중 "앞으로는 …", "… 하지 마라", "기억해: …" 를 알아채 남긴다</div>}
      {d.items.map(x => <div key={x.id} class="py-1.5 border-b border-soft">
        <div class="flex gap-1 flex-wrap items-center text-xs text-mute">
          {d.md ? <select class="chip !py-0" value={x.kind} title="갈래 바꾸기" onChange={e => act('/api/memory/update', { id: x.id, text: x.text, kind: e.currentTarget.value })}>
            {KIND3.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select> : <span class="chip">{MKIND[x.kind] || x.kind}</span>}
          <span class="chip">{x.scope === 'project' ? '이 프로젝트' + (x.where ? ' · ' + x.where : '') : '모든 프로젝트'}</span>
          <span>{MSRC[x.source] || x.source} · {String(x.ts || '').slice(5, 16).replace('T', ' ')}</span>
        </div>
        {edit[x.id] != null
          ? <div class="flex gap-1.5 mt-1"><input class="field" value={edit[x.id]} onInput={e => setEdit({ ...edit, [x.id]: e.currentTarget.value })} />
            <button class="btn btn-ok !py-0.5 !px-2 text-xs flex-none" onClick={() => { act('/api/memory/update', { id: x.id, text: edit[x.id] }); setEdit({ ...edit, [x.id]: null }) }}>저장</button>
            <button class="btn !py-0.5 !px-2 text-xs flex-none" onClick={() => setEdit({ ...edit, [x.id]: null })}>그만</button></div>
          : <div class="mt-0.5 break-words">{x.text}</div>}
        {edit[x.id] == null && <div class="flex gap-2 mt-1 text-xs">
          <button class="text-mute hover:text-fg" onClick={() => setEdit({ ...edit, [x.id]: x.text })}>고치기</button>
          <button class="text-mute hover:text-fg" onClick={() => act('/api/memory/forget', { id: x.id })}>지우기</button>
          <button class="text-bad hover:underline" onClick={() => act('/api/memory/deny', { id: x.id })}>이건 기억하지 마</button>
        </div>}
      </div>)}
      {d.deny.length > 0 && <><div class="font-semibold mb-1 mt-3">기억하지 말라 한 것 {d.deny.length}</div>
        {d.deny.map((t, i) => <div key={i} class="py-0.5 text-mute line-through">{t}</div>)}</>}
      <div class="text-xs text-mute mt-3">{d.md ? `방침·프로필은 모든 프로젝트 것과 이 대상(${d.place}) 것이 지시문에 들어간다(새것부터 방침 1.5KB·프로필 600바이트). 자료는 doc_search 로만.`
        : `사용자 기억은 모든 프로젝트, 프로젝트 기억은 이 대상(${d.place})에서만 지시문에 들어간다(새것부터 2KB).`}</div>
    </div>
  )
}

async function compactNow(mode) {
  try { await cpost('/api/main/compact', { mode }); refresh() } catch (e) { toast('못 줄임: ' + e.message) }
}

function Tokens() {
  const t = hub.value?.tokens
  if (!t) return <div class="text-mute text-sm p-4">아직 없음</div>
  const row = (k, v) => <div class="flex py-1 border-b border-soft"><span class="text-mute flex-1">{k}</span><span class="font-mono">{v}</span></div>
  return (
    <div class="px-4 py-3 text-[13.5px]">
      {row('지난 단계 입력(지금 문맥)', fmtK(t.last_prompt))}
      {row('문맥 창', fmtK(t.window))}
      {t.compact_at && row('줄이기 시작 선', fmtK(t.compact_at))}
      {row('대화 누계 입력', fmtK(t.prompt_total))}
      {row('대화 누계 출력', fmtK(t.completion_total))}
      {row('단계', t.steps)}
      {row('줄인 횟수', t.compactions)}
      {row('모델 시간 누계', fmtSecs(t.model_s || 0))}
      {t.turn && <>
        <div class="font-semibold mt-3 mb-1">이번 차례</div>
        {row('입력', fmtK(t.turn.prompt))}{row('출력', fmtK(t.turn.completion))}{row('단계', t.turn.steps)}{row('모델 시간', fmtSecs(t.turn.model_s || 0))}
      </>}
      {row('요약해 접은 횟수', t.summaries || 0)}
      <div class="flex gap-2 mt-3">
        <button class="btn !py-1 text-xs" disabled={busy.value} onClick={() => compactNow('clip')} title="오래된 도구 결과를 줄인다(원문은 기록에)">줄이기</button>
        <button class="btn !py-1 text-xs" disabled={busy.value} onClick={() => compactNow('summary')} title="최근 두 차례 앞의 대화를 요약 한 쪽으로 접는다(원문은 기록에)">요약해 접기</button>
      </div>
      <div class="text-xs text-mute mt-2">자동: 문맥이 줄이기 선(창의 50%)을 넘으면 오래된 도구 결과를 줄이고, 그래도 70% 를 넘으면 앞 대화를 요약해 접는다. 일하는 중에는 누를 수 없다.</div>
      <div class="text-xs text-mute mt-3">입력은 서버가 알려 준 prompt_tokens(같은 앞부분은 서버 캐시로 다시 읽지 않을 수 있다).</div>
    </div>
  )
}

export function Panel({ extra }) {
  const p = panel.value
  const tabs = extra ? [...TABS, extra.tab] : TABS
  const body = p === 'board' ? <BoardTab /> : p === 'memory' ? <MemoryTab /> : p === 'steps' ? <Steps /> : p === 'plan' ? <Plan /> : p === 'rules' ? <Rules /> : p === 'tokens' ? <Tokens /> : extra && p === extra.tab[0] ? extra.body : <Steps />
  return (
    <>
      <div class="flex items-center gap-2 px-4 py-3 border-b border-line font-[650]">작업 내역
        <button class="chip ml-auto" onClick={() => setPanel(null)}>닫기 ✕</button></div>
      <div class="flex gap-1 px-3 py-2 border-b border-line overflow-x-auto">
        {tabs.map(([k, label]) => <button key={k} class={'text-[13px] px-2.5 py-1 rounded-lg whitespace-nowrap ' + (p === k ? 'bg-soft text-fg font-semibold' : 'text-mute')} onClick={() => setPanel(k)}>{label}</button>)}
      </div>
      <div class="flex-1 overflow-auto scroll-thin">{body}</div>
    </>
  )
}
