// 가운데 대화: 사람 말 · 모델 답(마크다운) · 작업 카드(도구 단계, 접힘) · 바꾼 파일 카드 · 받는 중인 답
import { useState, useMemo } from 'preact/hooks'
import { mdHtml, fmtK, fmtSecs, estTokens, post } from './lib.js'
import { diffRows } from './diffrows.js'
import { copyText, flash } from './copy.js'
import { ThinkBlock } from './think.jsx'
import { hub, tick, clockSkew, busy, refresh, toast, cpost } from './state.js'

// main_log 줄을 덩어리로: user · assistant · diff · steps(도구·안내 줄 묶음)
export function groupLog(log) {
  const out = []
  let g = null
  const flush = () => { if (g) { out.push(g); g = null } }
  let think = null   // 단계 사이의 생각은 그 다음 단계에 붙인다(판 56)
  for (const m of log) {
    if (m.kind === 'think') {
      if (g) { think = m; continue }
      out.push({ type: 'think', key: 'th' + m.ts + (m.chars || 0), m }); continue
    }
    if (m.kind === 'tool' && /^todo_(write|read)\b/.test(m.text)) {   // 할 일 목록: 작업 카드 밖, 대화 흐름 안에 목록 카드
      flush()
      if (m.text.startsWith('todo_write')) out.push({ type: 'todo', key: 't' + m.ts + m.text.length, m })
      continue
    }
    if (m.kind === 'info' && !g && out.length && out[out.length - 1].type === 'todo') continue   // 목록 도구의 결과 줄
    if (m.kind === 'tool' || m.kind === 'info' || m.kind === 'warn' || m.kind === 'files' || m.kind === 'reflect') {
      if (!g) g = { type: 'steps', key: m.ts + '|' + m.text.slice(0, 30), steps: [], notes: [], files: null }
      if (m.kind === 'tool' || m.kind === 'reflect') { g.steps.push({ ...m, out: [], think }); think = null }
      else if (m.kind === 'files') g.files = m.text
      else if (g.steps.length && m.kind === 'info') g.steps[g.steps.length - 1].out.push(m.text)
      else g.notes.push(m)
      continue
    }
    if (think) { out.push({ type: 'think', key: 'th' + think.ts + (think.chars || 0), m: think }); think = null }
    if (m.kind === 'meta') continue                 // 차례 요약 줄: 대화에선 숨김(작업 내역·토큰 줄에 있다)
    flush()
    if (m.kind === 'turn_end') { if (!['answered', 'backend_error', 'context_overflow'].includes(m.reason)) out.push({ type: 'end', key: 'e' + m.ts + m.text, m }); continue }
    if (m.kind === 'error') { out.push({ type: 'error', key: 'x' + m.ts + m.text, m }); continue }
    if (m.kind === 'tail') { out.push({ type: 'tail', key: 'tl' + m.ts + m.text, m }); continue }
    if (m.kind === 'memory') { out.push({ type: 'memory', key: 'mm' + m.ts + m.text, m }); continue }
    if (m.kind === 'diff') out.push({ type: 'diff', key: 'd' + m.ts + m.text.length, m })
    else out.push({ type: m.kind === 'user' ? 'user' : m.kind === 'assistant' ? 'assistant' : 'note', key: m.kind + m.ts + m.text.slice(0, 20), m })
  }
  flush()
  return out
}

const openMem = new Map()   // 접기·펼치기를 다시 그려도 기억
function useOpen(key, dflt) {
  const [, force] = useState(0)
  const v = openMem.has(key) ? openMem.get(key) : dflt
  return [v, () => { openMem.set(key, !v); force(x => x + 1) }]
}

function Md({ text, className = '' }) {
  const html = useMemo(() => mdHtml(text), [text])
  return <div class={'md ' + className} dangerouslySetInnerHTML={{ __html: html }} />
}

function Icon({ ok, run }) {
  if (run) return <span class="w-5 h-5 rounded-md grid place-items-center flex-none bg-soft"><span class="spin" style="width:11px;height:11px" /></span>
  if (ok === false) return <span class="w-5 h-5 rounded-md grid place-items-center flex-none bg-soft text-bad text-[11px]">✕</span>
  if (ok === true) return <span class="w-5 h-5 rounded-md grid place-items-center flex-none bg-soft text-ok text-[11px]">✓</span>
  return <span class="w-5 h-5 rounded-md grid place-items-center flex-none bg-soft text-mute text-[11px]">•</span>
}

function ReflectStep({ s }) {
  const [open, toggle] = useOpen('r' + s.ts + s.label, false)
  const run = s.state === 'running'
  return (
    <div class="flex gap-2.5 items-start px-3 py-2 text-[13.5px] border-t border-soft first:border-t-0 bg-accent/5">
      {run ? <span class="w-5 h-5 rounded-md grid place-items-center flex-none bg-soft"><span class="spin" style="width:11px;height:11px" /></span>
        : <span class="w-5 h-5 rounded-md grid place-items-center flex-none bg-soft text-accent text-[12px]">↻</span>}
      <div class="flex-1 min-w-0">
        <button class="block w-full text-left font-semibold text-[13px]" onClick={s.body ? toggle : undefined}>
          {run ? `되짚는 중 — ${s.label}` : `되짚음 — ${s.label}`}<span class="font-normal text-mute"> · 도구 없이 원인과 다른 길을 적고 이어 간다</span>
          {s.body && <span class="font-normal text-mute"> {open ? '▴' : '▾'}</span>}
        </button>
        {open && s.body && <div class="mt-1 bg-code rounded-lg px-3 py-2 text-[13px]"><Md text={s.body} /></div>}
      </div>
    </div>
  )
}

function Step({ s, running }) {
  if (s.kind === 'reflect') return <ReflectStep s={s} />
  const [open, toggle] = useOpen('o' + s.ts + s.text, s.ok === false)
  const name = s.text.split(' ')[0], rest = s.text.slice(name.length + 1)
  const full = s.result ? s.result.split('\n') : null   // 모델이 받은 결과 전문(허브가 남긴 것), 없으면 화면 줄(끝 15줄)
  const shown = s.ok === false || open ? (full && open ? full : s.out) : []
  const code = name === 'run_shell' ? exitOf(s) : null
  return (
    <div class="flex gap-2.5 items-start px-3 py-2 text-[13.5px] border-t border-soft first:border-t-0">
      <Icon ok={s.ok} run={running} />
      <div class="flex-1 min-w-0">
        <button class="block w-full text-left font-mono text-[13px] truncate" title={s.text} onClick={(full || s.out).length ? toggle : undefined}>
          {name === 'run_shell' ? '$ ' + rest.replace(/^\$\s*/, '') : <><span class="text-mute">{name}</span> {rest}</>}
        </button>
        <div class="text-xs text-mute flex gap-2 flex-wrap">
          {s.elapsed != null && <span>{s.elapsed < 1 ? s.elapsed.toFixed(1) : Math.round(s.elapsed)}초</span>}
          {(full || s.out).length > 0 && !shown.length && <button class="underline-offset-2 hover:underline" onClick={toggle}>출력 {(full || s.out).length}줄 ▾</button>}
          {open && full && <span title="모델에게 간 결과 그대로">모델이 받은 결과 그대로</span>}
          {code != null && code !== 0 && <span class="text-bad font-semibold" title="셸 끝 코드">exit {code}</span>}
          {name === 'run_shell' && <button class="hover:underline" title="명령 복사" onClick={e => { const b = e.currentTarget; copyText(s.command || rest.replace(/^\$\s*/, '')).then(ok => flash(b, ok)) }}>명령 복사</button>}
          {(full || s.out).length > 0 && <button class="hover:underline" title="결과 전문 복사(잘리지 않은 것)" onClick={e => { const b = e.currentTarget; copyText(s.result || s.out.join('\n')).then(ok => flash(b, ok)) }}>결과 복사</button>}
          {running && <span>도는 중</span>}
        </div>
        {s.think && <ThinkBlock text={s.think.text} secs={s.think.secs} chars={s.think.chars} cut={s.think.cut} />}
        {shown.length > 0 && <pre class="mt-1 bg-code rounded-lg px-2.5 py-1.5 font-mono text-xs text-mute whitespace-pre-wrap break-all max-h-60 overflow-auto"
          onClick={s.ok !== false ? toggle : undefined}>{shown.map(l => l.replace(/^ {4}/, '')).join('\n')}</pre>}
      </div>
      {s.rep > 1 && <span class="text-[11px] px-2 py-px rounded-full font-semibold whitespace-nowrap text-bad bg-bad/15" title="같은 도구가 같은 오류를 낸 횟수">같은 오류 {s.rep}번째</span>}
    </div>
  )
}

// 묶음 제목 요약: "명령 3개 실행 · 파일 2개 읽음 · 파일 1개 고침"(꼴은 OpenHands event-group.tsx, MIT — ui/THIRD_PARTY.md)
const VERB = [[/^run_shell$/, '명령', '실행'], [/^(read_file|view_image)$/, '파일', '읽음'], [/^(write_file|edit_file|multi_edit)$/, '파일', '고침'],
  [/^(list_dir|glob|search|folder_scan|scan_read|scan_stats|peek)$/, '찾아보기', ''], [/^session_/, '작업자 일', ''], [/^memory_|^note_add$/, '기억', ''], [/^web_/, '웹', ''], [/^board$/, '현황판', ''], [/^forge_/, '저장소', '']]
export function stepSummary(steps) {
  const n = new Map()
  for (const s of steps) {
    if (s.kind === 'reflect') continue
    const name = s.text.split(' ')[0]
    const v = VERB.find(([re]) => re.test(name))
    const k = v ? v[1] + '\0' + v[2] : name + '\0'
    n.set(k, (n.get(k) || 0) + 1)
  }
  return [...n].map(([k, c]) => { const [w, verb] = k.split('\0'); return `${w} ${c}개${verb ? ' ' + verb : ''}` }).join(' · ')
}
export const exitOf = s => { const m = /^\[exit (-?\d+)\]/.exec(s.result || (s.out || [])[0] || ''); return m ? +m[1] : null }

function StepsCard({ g, last }) {
  const steps = g.steps
  const fails = steps.filter(s => s.ok === false).length
  const reflects = steps.filter(s => s.kind === 'reflect').length
  const isLive = last && busy.value
  const [open, toggle] = useOpen('g' + g.key, false)
  const show = open ? steps : steps.filter((x, i) => i >= steps.length - 3 || x.kind === 'reflect')   // 되짚기 줄은 접어도 보인다
  const hidden = steps.length - show.length
  if (!steps.length && !g.notes.length && !g.files) return null
  if (!steps.length) return (   // 도구 없이 안내 줄만: 카드 말고 흐린 글
    <div class="my-2 space-y-0.5">{g.notes.map(n => (
      <div key={n.ts + n.text} class={'text-[13px] whitespace-pre-wrap ' + (n.kind === 'warn' ? 'text-warn' : 'text-mute')}>{n.kind === 'warn' ? '⚠ ' : ''}{n.text}</div>
    ))}{g.files && <div class="text-[13px] text-mute">{g.files}</div>}</div>
  )
  return (
    <div class="border border-line rounded-xl bg-panel my-2.5 overflow-hidden shadow-card">
      <button class="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-mute border-b border-line text-left" onClick={toggle}>
        <span>{isLive ? '⏺' : '●'}</span>
        <span>{stepSummary(steps) || `작업 ${steps.length - reflects}단계`}{fails ? ` · 실패 ${fails}` : ''}{reflects ? ` · 되짚기 ${reflects}` : ''}{g.files ? ' · ' + g.files.replace(/^이번 차례에 바뀐 /, '바꾼 ').split(':')[0] : ''}</span>
        <span class="ml-auto">{open ? '접기 ▴' : hidden ? `앞 ${hidden}단계 · 펼치기 ▾` : '펼치기 ▾'}</span>
      </button>
      {show.map((s, i) => <Step key={s.ts + s.text} s={s} running={isLive && i === show.length - 1 && s.ok == null} />)}
      {(open || !steps.length) && g.notes.map(n => (
        <div key={n.ts + n.text} class={'px-3 py-1.5 text-[13px] border-t border-soft whitespace-pre-wrap ' + (n.kind === 'warn' ? 'text-warn' : 'text-mute')}>{n.text}</div>
      ))}
      {!open && steps.length > 0 && g.notes.some(n => n.kind === 'warn') && (
        <div class="px-3 py-1.5 text-[13px] border-t border-soft text-warn truncate">⚠ {g.notes.filter(n => n.kind === 'warn').slice(-1)[0].text}</div>
      )}
    </div>
  )
}

export function DiffCard({ text, stat }) {   // 줄 번호 4칸 격자(판 55, 꼴은 llama.cpp tools/ui — diffrows.js)
  const lines = String(text).split('\n')
  stat = stat || {
    path: (lines.find(l => l.startsWith('+++ ')) || '').replace(/^\+\+\+ (b\/)?/, ''),
    plus: lines.filter(l => l.startsWith('+') && !l.startsWith('+++')).length,
    minus: lines.filter(l => l.startsWith('-') && !l.startsWith('---')).length,
  }
  const body = lines.filter(l => !l.startsWith('+++') && !l.startsWith('---'))
  const [open, toggle] = useOpen('diff' + stat.path + text.length, body.length <= 24)
  return (
    <div class="border border-line rounded-xl overflow-hidden my-2 bg-panel">
      <button class="w-full flex gap-2 items-center px-3 py-1.5 text-[13px] bg-soft text-left" onClick={toggle}>
        <span>± {stat.path || '(파일)'}</span><span class="text-ok">+{stat.plus}</span><span class="text-bad">−{stat.minus}</span>
        <span class="ml-auto text-mute">{open ? '접기' : `${body.length}줄 펼치기`}</span>
      </button>
      {open && <div class="py-1 font-mono text-xs leading-normal overflow-auto max-h-[28rem]"><div class="dg-wrap">{diffRows(text).map((r, i) => r.kind === 'hunk'
        ? <div key={i} class="dg-hunk px-3 py-0.5">{r.of > 1 ? `묶음 ${r.n}/${r.of}` : '묶음'}{r.text ? ' · ' + r.text : ''}</div>
        : r.kind === 'note'
          ? <div key={i} class="px-3 text-mute">{r.text}</div>
          : <div key={i} class={'dg ' + (r.kind === 'add' ? 'dl-add' : r.kind === 'del' ? 'dl-del' : '')}>
              <span class="dg-n">{r.old ?? ''}</span><span class="dg-m">{r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : ''}</span>
              <span class="dg-n">{r.new ?? ''}</span><span class="dg-t">{r.text || ' '}</span></div>
      )}</div></div>}
    </div>
  )
}

function Live() {
  const st = hub.value
  if (!st?.busy) return null
  const lv = st.live || {}, now = st.now
  tick.value
  const secs = now ? Date.now() / 1000 - clockSkew.value - now.since : 0
  const chars = (lv.text || '').length
  const rate = lv.since && chars ? chars / Math.max(0.5, Date.now() / 1000 - clockSkew.value - lv.since) / 3 : 0
  return (
    <div>
      {lv.think_text && !lv.text ? <ThinkBlock streaming text={lv.think_text} chars={lv.think} since={lv.think_since ? lv.think_since + clockSkew.value : null} /> : null}
      {lv.text ? <div class="my-4"><Md text={lv.text + ' ▍'} /></div> : null}
      <div class="flex items-center gap-2.5 text-[13px] text-mute my-3.5">
        <span class="spin" />
        <span>{st.stopping ? '멈추는 중…' : (now?.what || '일하는 중')}
          {lv.think && !lv.text ? ` · 생각 ${lv.think}자` : ''}
          {chars ? ` · 받은 ≈${estTokens(chars)} 토큰` : ''}{rate ? ` · ${Math.round(rate)} tok/s` : ''}
          {now ? ` · ${fmtSecs(secs)}` : ''}</span>
      </div>
    </div>
  )
}

export function TokenLine() {
  const t = hub.value?.tokens
  if (!t) return null
  return (
    <div class="flex gap-3 flex-wrap text-xs text-mute mt-1">
      {t.turn && <span>이번 차례: 입력 {fmtK(t.turn.prompt)} · 출력 {fmtK(t.turn.completion)} · 모델 {fmtSecs(t.turn.model_s || 0)}</span>}
      <span>대화 누계: 입력 {fmtK(t.prompt_total)} · 출력 {fmtK(t.completion_total)} · 단계 {t.steps}{t.compactions ? ` · 줄이기 ${t.compactions}번` : ''}</span>
    </div>
  )
}

function MemLine({ m }) {
  const [gone, setGone] = useState(false)
  async function undo() { try { await post('/api/memory/forget', { id: m.mem_id }); setGone(true) } catch (e) { toast('못 되돌림: ' + e.message) } }
  return (
    <div class={'text-[13px] my-1.5 flex items-start gap-1.5 ' + (gone ? 'text-mute line-through' : 'text-accent')}>
      <span>🧠</span><span class="flex-1">{m.text}<span class="text-mute no-underline"> · {m.mem_op === 'drop' ? '다음 대화부터 빠진다' : '다음 대화부터 지킨다'}
        ({m.mem_scope === 'project' ? '이 프로젝트' : '모든 프로젝트'}{m.mem_source === 'model' ? ' · 모델이 알아챔' : ''})</span></span>
      {!gone && m.mem_id && (m.mem_op || 'add') === 'add' && <button class="btn btn-ghost !py-0 !px-2 text-xs flex-none" onClick={undo}>되돌리기</button>}
      {gone && <span class="text-xs text-mute no-underline">되돌림</span>}
    </div>
  )
}

const END = { stopped: '화면에서 멈췄다', interrupted: '끊겼다', max_steps: '단계 상한에 닿아 멈췄다', consecutive_errors: '오류가 이어져 멈췄다',
  repeat_stop: '같은 실패를 되풀이해 멈췄다', asked: '사람에게 물었다' }

function ErrorCard({ m, last }) {
  const [open, setOpen] = useState(false), [sent, setSent] = useState(false)
  const st = hub.value
  async function retry() {
    setSent(true)
    try { await cpost('/api/main', { text: m.retry }); refresh() } catch (e) { setSent(false); toast('못 보냈다: ' + e.message) }
  }
  const model = st?.model
  return (
    <div class="border border-bad/40 bg-bad/10 rounded-xl px-3 py-2 my-2.5 text-[13.5px]">
      <div class="flex items-start gap-2">
        <span class="text-bad">⚠</span>
        <span class="flex-1">{m.text}
          {last && model && model.state !== 'unknown' && <span class="text-mute"> · 지금 모델 서버: {model.label}</span>}</span>
        {last && m.retry && !st?.busy && <button class="btn !py-1 flex-none" disabled={sent} onClick={retry}>{sent ? '보냄' : '다시 보내기'}</button>}
      </div>
      {m.detail && <button class="text-xs text-mute mt-1 hover:underline" onClick={() => setOpen(!open)}>자세히 {open ? '▴' : '▾'}</button>}
      {open && <pre class="mt-1 font-mono text-xs text-mute whitespace-pre-wrap break-all">{m.detail}</pre>}
    </div>
  )
}

const MARK = { pending: '☐', in_progress: '◐', done: '☑' }
export function TodoItems({ todos, compact }) {
  const ss = hub.value?.sessions || []
  return (
    <div class={compact ? 'text-[13px]' : 'text-[14px]'}>
      {todos.map((t, i) => {
        const w = ss.find(s => s.role !== 'main' && (t.content.startsWith(s.name + ':') || t.content.includes(s.name + ' ')))
        return (
          <div key={i} class={'flex items-start gap-2 py-0.5 ' + (t.status === 'done' ? 'text-mute line-through' : t.status === 'in_progress' ? 'font-semibold' : '')}>
            <span class={'flex-none w-4 text-center ' + (t.status === 'in_progress' ? 'text-accent' : t.status === 'done' ? 'text-ok' : 'text-mute')}>{MARK[t.status] || '☐'}</span>
            <span class="flex-1 min-w-0 break-words">{t.content}</span>
            {w && <span class="chip flex-none !no-underline"><span class={'dot ' + ({ working: 'dot-run', waiting_approval: 'dot-wait', failed: 'dot-bad', stopped: 'dot-ok' }[w.status] || '')} />{w.name}</span>}
          </div>
        )
      })}
    </div>
  )
}
const todoCount = ts => `${ts.filter(t => t.status === 'done').length}/${ts.length}`

function TodoCard({ todos, latest, ts }) {
  const [open, toggle] = useOpen('todo' + ts, false)
  if (!todos || !todos.length) return latest ? null : <div class="text-[13px] text-mute my-1.5">☑ 할 일 목록을 비웠다</div>
  if (!latest && !open) return (   // 앞 목록은 접힌 한 줄
    <button class="block text-[13px] text-mute my-1.5 hover:underline text-left" onClick={toggle}>☑ 할 일 목록 고침 · 끝 {todoCount(todos)} ▾</button>
  )
  return (
    <div class="border border-line rounded-xl bg-panel my-2.5 px-3.5 py-2.5 shadow-card">
      <div class="flex items-center text-[13px] text-mute mb-1"><span class="font-semibold text-fg mr-1.5">할 일</span>{todoCount(todos)}
        {!latest && <button class="ml-auto hover:underline" onClick={toggle}>접기 ▴</button>}</div>
      <TodoItems todos={todos} />
    </div>
  )
}

export function Thread({ log, prior }) {
  const groups = useMemo(() => groupLog(log), [log])
  let lastSteps = -1, lastTodo = -1
  groups.forEach((g, i) => { if (g.type === 'steps') lastSteps = i; if (g.type === 'todo') lastTodo = i })
  const liveTodos = hub.value?.todos || []
  const tail = groups.length - 1
  return (
    <>
      {prior}
      {!groups.length && !busy.value && (
        <div class="text-center text-mute py-16">
          <div class="text-lg font-semibold text-fg mb-1">{(hub.value?.chat || 'main') === 'main' ? '메인에게 할 일을 말하십시오' : '새 대화 — 메인과 같은 채팅입니다'}</div>
          <div class="text-sm">작업자를 맡기고, 승인을 묻고, 보고를 모읍니다.</div>
        </div>
      )}
      {groups.map((g, i) => {
        if (g.type === 'user') return <div key={g.key} class="flex justify-end my-4"><div class="bg-soft px-3.5 py-2.5 rounded-[16px_16px_4px_16px] max-w-[78%] max-md:max-w-[88%] whitespace-pre-wrap break-words">{g.m.text}</div></div>
        if (g.type === 'assistant') return <div key={g.key} class="my-4"><Md text={g.m.text} /></div>
        if (g.type === 'diff') return <DiffCard key={g.key} text={g.m.text} stat={g.m.stat} />
        if (g.type === 'think') return <ThinkBlock key={g.key} text={g.m.text} secs={g.m.secs} chars={g.m.chars} cut={g.m.cut} />
        if (g.type === 'steps') return <StepsCard key={g.key} g={g} last={i === lastSteps && i >= tail - 1} />
        if (g.type === 'todo') return <TodoCard key={g.key} ts={g.m.ts + g.m.text.length} latest={i === lastTodo}
          todos={i === lastTodo ? liveTodos : (g.m.todos || [])} />   // 가장 최근 카드는 지금 목록으로 그 자리에서 고쳐진다
        if (g.type === 'memory') return <MemLine key={g.key} m={g.m} />
        if (g.type === 'tail') return <div key={g.key} class="text-[13px] my-2 text-warn">📌 {g.m.text}</div>
        if (g.type === 'error') return <ErrorCard key={g.key} m={g.m} last={i >= tail - 1} />
        if (g.type === 'end') return <div key={g.key} class="text-[13px] text-mute my-2">— 차례 끝: {END[g.m.reason] || g.m.reason} —</div>
        return <div key={g.key} class="text-[13px] text-mute my-2 whitespace-pre-wrap">{g.m.text}</div>
      })}
      <Live />
      <TokenLine />
    </>
  )
}

export { Md }
