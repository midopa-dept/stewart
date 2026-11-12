// 가운데 대화: 사람 말 · 모델 답(마크다운) · 작업 카드(도구 단계, 접힘) · 바꾼 파일 카드 · 받는 중인 답
import { useState, useMemo } from 'preact/hooks'
import { signal } from '@preact/signals'
import { mdHtml, fmtK, fmtSecs, estTokens, post } from './lib.js'
import { diffRows } from './diffrows.js'
import { copyText, flash } from './copy.js'
import { ThinkBlock } from './think.jsx'
import { hub, tick, clockSkew, busy, refresh, toast, cpost, chat, composerFill } from './state.js'

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
      else if (m.kind === 'files') { g.files = m.text; g.paths = m.paths; g.turn = m.turn }
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
    if (m.kind === 'memory_ask') { out.push({ type: 'memask', key: 'ma' + m.ts + m.text, m }); continue }
    if (m.kind === 'memory_plan') { out.push({ type: 'memplan', key: 'mp' + m.ts, m }); continue }
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
          {s.elapsed != null && s.elapsed >= 0.05 && <span>{s.elapsed < 1 ? s.elapsed.toFixed(1) : Math.round(s.elapsed)}초</span>}
          {(full || s.out).length > 0 && !shown.length && <button class="underline-offset-2 hover:underline" onClick={toggle}>출력 {(full || s.out).length}줄 ▾</button>}
          {open && full && <span title="모델에게 간 결과 그대로">모델이 받은 결과 그대로</span>}
          {code != null && code !== 0 && <span class="text-bad font-semibold" title="셸 끝 코드">exit {code}</span>}
          {name === 'run_shell' && <button class="hover:underline" title="명령 복사" onClick={e => { const b = e.currentTarget; copyText(s.command || rest.replace(/^\$\s*/, '')).then(ok => flash(b, ok)) }}>명령 복사</button>}
          {(full || s.out).length > 0 && <button class="hover:underline" title="결과 전문 복사(잘리지 않은 것)" onClick={e => { const b = e.currentTarget; copyText(s.result || s.out.join('\n')).then(ok => flash(b, ok)) }}>결과 복사</button>}
          {running && <span>도는 중</span>}
        </div>
        {s.think && <ThinkBlock text={s.think.text} secs={s.think.secs} chars={s.think.chars} cut={s.think.cut} />}
        {s.imgs?.length > 0 && <StepImgs names={s.imgs} />}
        {shown.length > 0 && <pre class="mt-1 bg-code rounded-lg px-2.5 py-1.5 font-mono text-xs text-mute whitespace-pre-wrap break-all max-h-60 overflow-auto"
          onClick={s.ok !== false ? toggle : undefined}>{shown.map(l => l.replace(/^ {4}/, '')).join('\n')}</pre>}
      </div>
      {s.rep > 1 && <span class="text-[11px] px-2 py-px rounded-full font-semibold whitespace-nowrap text-bad bg-bad/15" title="같은 도구가 같은 오류를 낸 횟수">같은 오류 {s.rep}번째</span>}
    </div>
  )
}

// 도구가 본 그림(판 91): 작업 줄 아래 작은 그림 — 누르면 원래 크기로 새 창
const imgUrl = n => '/api/img?chat=' + encodeURIComponent(chat.value) + '&n=' + encodeURIComponent(n)
function StepImgs({ names }) {
  return (
    <div class="mt-1 flex gap-1.5 flex-wrap">
      {names.slice(-4).map(n => (
        <a key={n} href={imgUrl(n)} target="_blank" rel="noopener" class="block rounded-md overflow-hidden border border-soft"
          title="도구가 본 그림 — 누르면 원래 크기">
          <img src={imgUrl(n)} alt="도구가 본 그림" loading="lazy" class="block max-h-40 max-w-[240px] max-md:max-w-[46vw] object-contain" />
        </a>
      ))}
    </div>
  )
}

// 판 104(SW-45 ④·A): 작업 폴더 파일 열기·내려받기 — /api/file(읽기 전용, @파일과 같은 경계, HTML·SVG 는 내려받기만)
const fileUrl = (p, dl) => '/api/file?chat=' + encodeURIComponent(chat.value) + '&path=' + encodeURIComponent(p) + (dl ? '&dl=1' : '')
const fileView = signal(null)
const VIEW_TEXT = /\.(md|txt|csv|tsv|json|toml|ya?ml|py|js|ts|jsx|tsx|sh|log|ini|cfg|rs|go|java|c|h|cpp|css|sql|rst|tex)$/i
const VIEW_IMG = /\.(png|jpe?g|gif|webp)$/i
export function openFile(path) {
  if (VIEW_IMG.test(path) || /\.pdf$/i.test(path)) { window.open(fileUrl(path), '_blank', 'noopener'); return }
  if (!VIEW_TEXT.test(path)) { window.location.href = fileUrl(path, true); return }
  fileView.value = { path, text: null }
  fetch(fileUrl(path), { credentials: 'same-origin' }).then(r => r.ok ? r.text() : Promise.reject(new Error(r.status === 404 ? '열 수 없는 파일' : 'HTTP ' + r.status)))
    .then(t => { fileView.value = { path, text: t } }, e => { fileView.value = null; toast(path + ': ' + e.message) })
}
function FileViewer() {
  const v = fileView.value
  if (!v) return null
  const md = /\.md$/i.test(v.path)
  return (
    <div class="fixed inset-0 z-50 bg-black/40 flex items-end md:items-center justify-center" onClick={() => { fileView.value = null }}>
      <div class="bg-panel w-full md:w-[min(900px,92vw)] max-h-[88vh] rounded-t-2xl md:rounded-2xl shadow-card flex flex-col" onClick={e => e.stopPropagation()}>
        <div class="flex items-center gap-2 px-4 py-2.5 border-b border-line">
          <span class="font-mono text-[13px] truncate flex-1" title={v.path}>{v.path}</span>
          <a class="btn !py-1 !px-2.5 text-sm" href={fileUrl(v.path, true)}>내려받기</a>
          <button class="btn !py-1 !px-2.5 text-sm" onClick={() => { fileView.value = null }}>닫기</button>
        </div>
        <div class="overflow-auto px-4 py-3 text-[14px]">
          {v.text == null ? <span class="text-mute">여는 중…</span>
            : md ? <Md text={v.text} /> : <pre class="font-mono text-xs whitespace-pre-wrap break-all">{v.text.slice(0, 400000)}</pre>}
        </div>
      </div>
    </div>
  )
}
// 판 105(SW-45 ①): 차례 되감기 — 파일만 되돌리기 / 차례를 지우고 내 말을 입력 칸에
function undoTurn(turn, cut) {
  const ask = cut ? `차례 ${turn} 을 지우고 그 말을 입력 칸에 되돌릴까요? 그 차례 뒤로 도구가 바꾼 파일도 되돌린다(셸 명령이 바꾼 것은 못 되돌린다).`
    : `차례 ${turn} 시작 때로 파일을 되돌릴까요? 대화는 그대로다(셸 명령이 바꾼 것은 못 되돌린다).`
  if (!window.confirm(ask)) return
  cpost('/api/main/undo_turn', { turn, cut }).then(r => {
    if (cut && r.text != null) composerFill.value = { target: 'main', text: r.text }
    refresh()
  }, e => toast('안 됨: ' + e.message))
}
function FilesCard({ paths, turn }) {
  return (
    <div class="my-2 border border-line rounded-xl px-3 py-2 bg-panel text-[13px]">
      <div class="font-semibold mb-1 flex items-center gap-2"><span class="flex-1">이번 차례에 만든·바꾼 파일 {paths.length}개</span>
        {turn && !busy.value && <button class="btn !py-0.5 !px-2 text-xs" title="이 차례 시작 때로 파일을 되돌린다(셸이 바꾼 파일은 못 되돌린다)"
          onClick={() => undoTurn(turn, false)}>↶ 이 차례 되돌리기</button>}</div>
      {paths.map(p => <div key={p} class="flex items-center gap-2 py-0.5">
        <button class="font-mono truncate flex-1 text-left hover:underline" title={p} onClick={() => openFile(p)}>{p}</button>
        <button class="btn !py-0.5 !px-2 text-xs" onClick={() => openFile(p)}>열기</button>
        <a class="btn !py-0.5 !px-2 text-xs" href={fileUrl(p, true)}>받기</a>
      </div>)}
    </div>
  )
}
// 답 글 안 `경로` 꼴 코드 조각을 누르면 연다(없거나 못 내주는 파일이면 알림만)
const PATH_RX = /^[\w.\-\/가-힣]+\.[A-Za-z0-9]{1,6}$/
function onAnswerClick(e) {
  const t = e.target
  if (t && t.tagName === 'CODE' && !t.closest('pre') && PATH_RX.test(t.textContent || '') && !/^https?:/.test(t.textContent)) openFile(t.textContent)
}

// 판 106(SW-45 ⑫): ask_user 선택지 단추 — 누르면 그 글이 다음 사람 말로 간다("직접 쓰기"는 입력 칸)
function AskOptions({ opts }) {
  const send = t => cpost('/api/main', { text: t }).then(refresh, e => toast('안 됨: ' + e.message))
  return <div class="mt-2 flex gap-1.5 flex-wrap">
    {opts.map(o => <button key={o} class="btn !py-1.5 !px-3 text-sm" onClick={() => send(o)}>{o}</button>)}
    <button class="btn btn-ghost !py-1.5 !px-3 text-sm" onClick={() => document.querySelector('textarea')?.focus()}>직접 쓰기</button>
  </div>
}

// 판 109(SW-43 ③): 동시 작업자 띠 — 상태·걸린 시간, 누르면 그 작업자 결과를 안 거둔 것인지 보인다
const WST = { queued: '⏳ 줄', running: '● 도는 중', done: '✓ 끝', cancelled: '✕ 취소' }
function WorkerStrip() {
  const ws = hub.value?.workers || []
  const live = ws.filter(w => w.status === 'running' || w.status === 'queued' || (w.status === 'done' && !w.collected))
  if (!live.length) return null
  return <div class="sticky top-0 z-10 my-2 flex gap-1.5 flex-wrap text-[12.5px]">
    {live.map(w => <span key={w.id} class={'chip ' + (w.status === 'running' ? '!text-ok' : '')} title={w.task}>
      {w.id} {WST[w.status] || w.status} · {w.secs}초{w.status === 'done' && !w.collected ? ' · 결과 안 거둠' : ''}</span>)}
  </div>
}

// 사람이 보낸 사진(판 98): 말 줄 아래 작은 그림 — 누르면 원본(새 탭)
export function UserImgs({ names }) {
  return (
    <div class="mt-1.5 flex gap-1.5 flex-wrap justify-end">
      {names.map(n => <a key={n} href={imgUrl(n)} target="_blank" rel="noopener" class="block rounded-lg overflow-hidden border border-line" title="보낸 사진 — 누르면 원본">
        <img src={imgUrl(n)} alt="보낸 사진" loading="lazy" class="block max-h-44 max-w-[220px] max-md:max-w-[42vw] object-cover" />
      </a>)}
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

// 오래 지킬 지시를 남기기 전에 묻는 카드(판 83): [허용] = 방침으로 남김, [이번만] = 이번 차례에만
// 기억 정리 제안 카드(판 88): 항목마다 [받기]·[두기], [모두 받기]·[고른 것 적용]. 폰에서 누르기 쉬운 크기
function MemPlan({ m }) {
  const plan = m.plan || []
  const [pick, setPick] = useState({}), [done, setDone] = useState(null)
  async function go(accept) {
    try { const r = await cpost(m.api || '/api/memory/consolidate', { accept }); setDone(r); refresh() }
    catch (e) { toast('안 됨: ' + e.message) }
  }
  const chosen = plan.filter(p => pick[p.n] === true).map(p => p.n)
  return (
    <div class="text-[13.5px] my-2 border border-line rounded-xl px-3.5 py-3 bg-panel shadow-card">
      <div class="font-semibold mb-1">🧠 {m.text}</div>
      {plan.map(p => <div key={p.n} class="py-2 border-t border-soft first:border-t-0">
        <div class="break-words">{p.desc}</div>
        {!done && <div class="flex gap-2 mt-1.5">
          <button class={'btn !py-1.5 !px-3 text-sm ' + (pick[p.n] === true ? 'btn-ok' : '')} onClick={() => setPick({ ...pick, [p.n]: true })}>받기</button>
          <button class={'btn !py-1.5 !px-3 text-sm ' + (pick[p.n] === false ? 'btn-ghost line-through' : '')} onClick={() => setPick({ ...pick, [p.n]: false })}>두기</button>
        </div>}
      </div>)}
      {done ? <div class="text-xs text-mute mt-2">{done.applied?.length ? `${done.applied.length}개 받음 — 되돌리기: ${m.revert || 'stewart memory consolidate --revert'} ${String(done.backup || '').split('/').pop()}` : '받은 것이 없어 바꾸지 않았다'}</div>
        : <div class="flex gap-2 mt-2 flex-wrap">
          <button class="btn btn-ok !py-2 !px-3.5" onClick={() => go('all')}>모두 받기</button>
          <button class="btn !py-2 !px-3.5" disabled={!chosen.length} onClick={() => go(chosen)}>고른 것 적용({chosen.length})</button>
        </div>}
    </div>
  )
}

function MemAsk({ m }) {
  const [done, setDone] = useState('')
  async function answer(keep) {
    try { await post('/api/memory/confirm', { id: m.mem_id, keep }); setDone(keep ? '방침으로 남겼다' : '이번만 — 남기지 않았다'); refresh() }
    catch (e) { toast('안 됨: ' + e.message) }
  }
  return (
    <div class="text-[13px] my-1.5 border border-line rounded-lg px-3 py-2 bg-panel">
      <div class="flex items-start gap-1.5"><span>🧠</span><span class="flex-1">{m.text}
        <span class="text-mute"> ({m.mem_scope === 'project' ? '이 프로젝트' : '모든 프로젝트'}{m.mem_source === 'model' ? ' · 모델이 알아챔' : ''})</span></span></div>
      {done ? <div class="text-xs text-mute mt-1">{done}</div>
        : <div class="flex gap-1.5 mt-1.5">
          <button class="btn btn-ok !py-0.5 !px-2.5 text-xs" onClick={() => answer(true)}>허용 — 다음 대화부터 지킨다</button>
          <button class="btn !py-0.5 !px-2.5 text-xs" onClick={() => answer(false)}>이번만</button></div>}
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
        {!latest && <button class="ml-auto hover:underline" onClick={toggle}>접기 ▴</button>}
        {latest && hub.value?.todos_at && <span class="ml-auto text-xs" title="모델이 목록을 마지막으로 고친 때 — 목록은 모델이 고칠 때만 바뀐다">
          목록 갱신 {new Date(hub.value.todos_at * 1000).toTimeString().slice(0, 5)}</span>}</div>
      <TodoItems todos={todos} />
    </div>
  )
}

// 학습 표시(판 89): 차례의 마지막 답 줄 아래 👍👎 — 터치 기기는 늘 보이고 마우스는 줄 위에 올릴 때(app.css .tbar).
// "교정됨"은 사람이 다음 말로 고쳤을 때 엔진이 붙인 꼬리표 — 누르면 뗀다. 표시는 기록에만 남고 대화·모델 문맥은 그대로다.
function TrainBar({ m }) {
  const send = label => cpost('/api/train/label', { turn: m.turn, label }).then(refresh, e => toast('표시하지 못했다: ' + e.message))
  const lb = m.label
  return (
    <div class={'tbar flex gap-1.5 items-center mt-1 text-[13px]' + (lb || m.corrected ? ' tbar-on' : '')}>
      <button class={'tb' + (lb === 'good' ? ' tb-sel' : '')} title={lb === 'good' ? '좋음 표시 떼기' : '좋은 예로 표시(학습 자료 고르기)'}
        aria-pressed={lb === 'good'} onClick={() => send(lb === 'good' ? 'clear' : 'good')}>👍</button>
      <button class={'tb' + (lb === 'bad' ? ' tb-sel' : '')} title={lb === 'bad' ? '나쁨 표시 떼기' : '나쁜 예로 표시'}
        aria-pressed={lb === 'bad'} onClick={() => send(lb === 'bad' ? 'clear' : 'bad')}>👎</button>
      {m.corrected && <button class="text-[11px] px-2 py-px rounded-full font-semibold whitespace-nowrap text-warn bg-warn/15"
        title="다음 말이 고치는 꼴이라 붙은 꼬리표 — 누르면 뗀다" onClick={() => send('uncorrect')}>교정됨 ✕</button>}
    </div>
  )
}

export function Thread({ log, prior }) {
  const groups = useMemo(() => groupLog(log), [log])
  let lastSteps = -1, lastTodo = -1
  groups.forEach((g, i) => { if (g.type === 'steps') lastSteps = i; if (g.type === 'todo') lastTodo = i })
  const lastOfTurn = new Set()   // 차례마다 마지막 답 줄(그 아래에만 👍👎)
  const seenTurn = new Set()
  for (let i = groups.length - 1; i >= 0; i--) {
    const g = groups[i]
    if (g.type === 'assistant' && g.m.turn && !seenTurn.has(g.m.turn)) { seenTurn.add(g.m.turn); lastOfTurn.add(i) }
  }
  const curTurn = busy.value ? Math.max(0, ...groups.filter(g => g.type === 'assistant' && g.m.turn).map(g => g.m.turn)) : 0
  const liveTodos = hub.value?.todos || []
  const tail = groups.length - 1
  return (
    <>
      {prior}
      <FileViewer />
      <WorkerStrip />
      {!groups.length && !busy.value && (
        <div class="text-center text-mute py-16">
          <div class="text-lg font-semibold text-fg mb-1">{(hub.value?.chat || 'main') === 'main' ? '메인에게 할 일을 말하십시오' : '새 대화 — 메인과 같은 채팅입니다'}</div>
          <div class="text-sm">작업자를 맡기고, 승인을 묻고, 보고를 모읍니다.</div>
        </div>
      )}
      {groups.map((g, i) => {
        if (g.type === 'user') return <div key={g.key} class="my-4 tline"><div class="flex justify-end"><div class="bg-soft px-3.5 py-2.5 rounded-[16px_16px_4px_16px] max-w-[78%] max-md:max-w-[88%] whitespace-pre-wrap break-words">{g.m.text}</div></div>
          {g.m.turn && !busy.value && <div class="tbar flex justify-end mt-0.5"><button class="tb text-[12px]" title="이 차례를 지우고 내 말을 입력 칸에 되돌린다(고쳐 다시 보내기)"
            onClick={() => undoTurn(g.m.turn, true)}>↶ 되감기</button></div>}
          {g.m.imgs?.length > 0 && <UserImgs names={g.m.imgs} />}</div>
        if (g.type === 'assistant') return <div key={g.key} class="my-4 tline" onClick={onAnswerClick}><Md text={g.m.text} />
          {g.m.options?.length > 0 && i === tail && !busy.value && <AskOptions opts={g.m.options} />}
          {lastOfTurn.has(i) && g.m.turn !== curTurn && <TrainBar m={g.m} />}</div>
        if (g.type === 'diff') return <DiffCard key={g.key} text={g.m.text} stat={g.m.stat} />
        if (g.type === 'think') return <ThinkBlock key={g.key} text={g.m.text} secs={g.m.secs} chars={g.m.chars} cut={g.m.cut} />
        if (g.type === 'steps') return <div key={g.key}><StepsCard g={g} last={i === lastSteps && i >= tail - 1} />
          {g.paths?.length > 0 && <FilesCard paths={g.paths} turn={g.turn} />}</div>
        if (g.type === 'todo') return <TodoCard key={g.key} ts={g.m.ts + g.m.text.length} latest={i === lastTodo}
          todos={i === lastTodo ? liveTodos : (g.m.todos || [])} />   // 가장 최근 카드는 지금 목록으로 그 자리에서 고쳐진다
        if (g.type === 'memory') return <MemLine key={g.key} m={g.m} />
        if (g.type === 'memask') return <MemAsk key={g.key} m={g.m} />
        if (g.type === 'memplan') return <MemPlan key={g.key} m={g.m} />
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
