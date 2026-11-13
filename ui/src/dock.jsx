// 아래 고정: 승인 띠(대기 건마다 카드) · 대기 줄 · 입력 칸(/명령 · @파일 · 이미지 · 보낸 말 ↑↓ · 멈춤)
import { Component } from 'preact'
import { useState, useRef, useEffect } from 'preact/hooks'
import { signal } from '@preact/signals'
import { post, get, store } from './lib.js'
import { copyText, flash } from './copy.js'
import { prepImage, pastedFiles } from './imgprep.js'
import { hub, queue, busy, refresh, toast, view, sessions, chat, cpost, composerFill } from './state.js'
import { narrow, touch, plusSheet, convSheet } from './mobile.js'
import { IconPlus, IconSend, IconStop, IconMore, IconFile, IconImage, IconWarn, IconSliders, IconNew } from './icons.jsx'
import { workerOpen } from './newchat.jsx'

const KIND = { exact: '이 명령 그대로', subcommand: '하위 명령까지', code_prefix: '이 앞머리', first_word: '이 프로그램 전부' }
export const sheetAsk = signal(null)      // 폰: 승인 전문 시트에 띄운 건(session#seq)

const what = it => {
  const s = it.args_summary || {}
  return s.command ? s.command : s.paths ? s.paths.join('\n') : (it.prompt || '')
}

// 같은 건을 두 번(또는 허용 뒤 거부를) 보내지 않게 — 보낸 건은 대기열에서 빠질 때까지 단추를 막는다
// (꼴은 OpenHands conversation-confirmation-buttons.tsx, MIT — ui/THIRD_PARTY.md)
export const sentAsk = signal(new Set())
const askKey = it => it.session + '#' + it.seq

async function answer(it, ans, extra) {
  const k = askKey(it)
  if (sentAsk.value.has(k)) return false
  sentAsk.value = new Set([...sentAsk.value, k])
  try {
    await post('/api/answer', { session: it.session, seq: it.seq, answer: ans, ...(extra || {}) })
    if (sheetAsk.value === it.session + '#' + it.seq) sheetAsk.value = null
    refresh()
    return true
  } catch (e) {
    const n = new Set(sentAsk.value); n.delete(k); sentAsk.value = n   // 못 보냈으면 다시 누를 수 있게
    toast('승인 답을 못 보냈다: ' + e.message); return false
  }
}

const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
const KEY_OK = IS_MAC ? '⌘↵' : 'Ctrl+Enter', KEY_NO = IS_MAC ? '⇧⌘⌫' : 'Shift+Ctrl+Backspace'

// 키보드: ⌘/Ctrl+Enter = 맨 앞 건 허용, ⇧⌘/Ctrl+Backspace = 거부. 글 칸에 쓰던 글이 있으면 그 칸의 일(보내기·지우기)이 먼저다
document.addEventListener('keydown', e => {
  if (e.repeat || e.defaultPrevented || !(e.metaKey || e.ctrlKey)) return
  const ok = e.key === 'Enter' && !e.shiftKey, no = e.key === 'Backspace' && e.shiftKey
  if (!ok && !no) return
  const a = document.activeElement
  if (a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT') && String(a.value || '').trim()) return
  const it = (queue.value || []).find(x => !sentAsk.value.has(askKey(x)))
  if (!it) return
  e.preventDefault()
  answer(it, ok ? 'y' : 'n')
})

// 늘 허용 꼴 고르기: 조각마다 꼴 하나(타자 없음) + 범위(이 세션만 / 이 프로젝트 늘)
function Picker({ it, onDone }) {
  const a = it.always
  const need = a.pieces.filter(p => !p.allowed)
  const [chosen, setChosen] = useState(() => need.map(p => p.options.length === 1 ? p.options[0].pattern : null))
  const [scope, setScope] = useState('session')
  const key = it.session + '-' + it.seq
  return (
    <div class="mt-2 border border-line rounded-lg p-2.5 bg-bg text-[13px] space-y-2">
      {a.pieces.map((p, idx) => {
        if (p.allowed) return <div key={idx} class="text-mute">조각 <code class="font-mono">{p.piece}</code> — 이미 허용됨</div>
        const i = need.indexOf(p)
        return (
          <div key={idx}>
            <div class="text-mute mb-1">조각: <code class="font-mono text-fg break-all">{p.piece}</code></div>
            {p.options.map(o => (
              <label key={o.pattern} class="flex items-start gap-2 py-0.5 cursor-pointer">
                <input type="radio" name={key + '-' + i} checked={chosen[i] === o.pattern} class="mt-1"
                  onChange={() => setChosen(c => c.map((x, j) => j === i ? o.pattern : x))} />
                <span><span class="text-mute">{KIND[o.kind] || o.kind}:</span> <code class="font-mono break-all">{o.pattern.length > 160 ? o.pattern.slice(0, 160) + '…' : o.pattern}</code>
                  {o.warn && <span class={'block ' + (o.broad ? 'text-bad' : 'text-warn')}>⚠ {o.warn}</span>}</span>
              </label>
            ))}
          </div>
        )
      })}
      <div class="flex gap-4 flex-wrap">
        <label class="flex items-center gap-1.5"><input type="radio" name={key + '-scope'} checked={scope === 'session'} onChange={() => setScope('session')} />이 세션만(허브를 끄면 사라짐)</label>
        <label class="flex items-center gap-1.5"><input type="radio" name={key + '-scope'} checked={scope === 'project'} onChange={() => setScope('project')} />이 프로젝트 늘</label>
      </div>
      <button class="btn btn-ok" disabled={chosen.some(c => !c)} onClick={() => answer(it, 'y', { always: chosen, scope }).then(ok => ok && onDone())}>늘 허용하고 지금 실행</button>
    </div>
  )
}

function SayBox({ it }) {
  const [v, setV] = useState('')
  return (
    <div class="flex gap-2 mt-2">
      <input class="field" placeholder="모델에 전할 말(이번 건은 거부로 처리)" value={v} onInput={e => setV(e.currentTarget.value)}
        onKeyDown={e => { if (e.key === 'Enter' && v.trim()) answer(it, v.trim()) }} autoFocus />
      <button class="btn" disabled={!v.trim()} onClick={() => answer(it, v.trim())}>보내기</button>
    </div>
  )
}

function AskCard({ it, full }) {
  const [mode, setMode] = useState(null)   // null · pick · say
  const s = it.args_summary || {}
  const canAlways = it.kind === 'confirm' && s.tool === 'run_shell' && it.always
  const text = what(it)
  const key = it.session + '#' + it.seq
  const sent = sentAsk.value.has(key)
  const first = (queue.value || []).find(x => !sentAsk.value.has(askKey(x))) === it   // 키보드가 고르는 건
  if (!full) return (   // 판 124: 입력 칸 바로 위 시트 — 사람 말 제목, 명령, 허용(먹색)·거부(글자)·⋯(폰·데스크톱 같은 꼴, 단축키는 그대로)
    <div class="bg-panel rounded-2xl px-3.5 pt-2.5 pb-2.5 shadow-card border border-line">
      <div class="text-[15.5px] font-semibold flex items-center gap-2"><span class="w-[17px] h-[17px] text-warn flex-none"><IconWarn /></span>{s.tool === 'run_shell' || s.command ? '이 명령을 실행할까요?' : s.paths ? '이 파일을 바꿀까요?' : '승인할까요?'}</div>
      {it.prompt && it.prompt !== text && !s.command && <div class="text-[14px] mt-1 whitespace-pre-wrap">{it.prompt}</div>}
      <pre class="font-mono text-[13px] leading-normal mt-1.5 mb-2 bg-code rounded-[10px] px-3 py-1.5 whitespace-pre-wrap break-all max-h-[3.6em] overflow-hidden"
        onClick={() => { sheetAsk.value = key }}>{text}</pre>
      {s.diff && <div class="text-xs text-mute -mt-1 mb-2">diff {s.diff.length}줄 — <button class="underline" onClick={() => { sheetAsk.value = key }}>전문 보기</button></div>}
      <div class="flex gap-2 items-center">
        <button class="btn-pri flex-1 md:flex-none md:w-44 h-11 rounded-xl text-[16px] font-semibold" disabled={sent} onClick={() => answer(it, 'y')}
          title={first ? KEY_OK : ''}>{sent ? '보냄…' : '허용'}</button>
        <button class="h-11 px-5 rounded-xl text-[16px] font-semibold text-fg hover:bg-soft" disabled={sent} onClick={() => answer(it, 'n')}>거부</button>
        <button class="ibtn" onClick={() => { sheetAsk.value = key }} aria-label="더 보기(누가·언제·전문·늘 허용·말 전하기)"><IconMore /></button>
      </div>
    </div>
  )
  return (
    <div class="border-[1.5px] border-warn bg-panel rounded-[14px] px-3 py-2.5 shadow-card">
      <div class="flex gap-2 items-center text-[13px] font-semibold text-warn">
        <span>⚠ 승인 기다림 · {it.session} #{it.seq}{s.tool ? ' · ' + s.tool : ''}</span>
        {it.created && <span class="ml-auto text-xs font-normal text-mute">{String(it.created).slice(11, 19)}</span>}
      </div>
      {it.prompt && it.prompt !== text && (full || !s.command) && <div class="text-[13px] mt-1 whitespace-pre-wrap">{it.prompt}</div>}
      {/* 긴 명령은 띠 안에서 스크롤(대화가 가려지지 않게 높이 상한). 폰은 두 줄만, 누르면 전문 */}
      <div class="relative">
        <pre class={'font-mono text-[12.5px] leading-normal my-2 bg-code rounded-lg pl-2.5 pr-14 py-2 whitespace-pre-wrap break-all overflow-auto ' +
          (full ? 'max-h-[50vh]' : 'max-h-36 max-md:max-h-[3.4em] max-md:overflow-hidden max-md:cursor-pointer')}
          onClick={() => { if (!full && window.matchMedia('(max-width: 767px)').matches) sheetAsk.value = key }}>{text}</pre>
        <button type="button" class="cp-btn absolute top-1.5 right-1.5" title="명령 전문 복사"
          onClick={e => { e.stopPropagation(); const b = e.currentTarget; copyText(text).then(ok => flash(b, ok)) }}>복사</button>
      </div>
      {full && s.diff && <pre class="font-mono text-xs bg-code rounded-lg px-2.5 py-2 max-h-[40vh] overflow-auto">{s.diff.map((l, i) => <span key={i} class={'block ' + (l.startsWith('+') ? 'dl-add' : l.startsWith('-') ? 'dl-del' : '')}>{l}</span>)}</pre>}
      {!full && s.diff && <div class="text-xs text-mute -mt-1 mb-2">diff {s.diff.length}줄 — <button class="underline" onClick={() => { sheetAsk.value = key }}>전문 보기</button></div>}
      <div class="flex gap-1.5 flex-wrap items-center">
        <button class="btn btn-ok max-md:flex-1 max-md:py-2.5" disabled={sent} onClick={() => answer(it, 'y')}
          title={first ? KEY_OK : ''}>{sent ? '보냄…' : '허용'}{first && !sent && <kbd class="kbd max-md:hidden">{KEY_OK}</kbd>}</button>
        <button class="btn max-md:flex-1 max-md:py-2.5" disabled={sent} onClick={() => answer(it, 'n')}
          title={first ? KEY_NO : ''}>거부{first && !sent && <kbd class="kbd max-md:hidden">{KEY_NO}</kbd>}</button>
        {canAlways && <button class={'btn btn-ghost ' + (full ? '' : 'max-md:hidden')} disabled={!it.always.ok} title={it.always.ok ? '' : it.always.reason}
          onClick={() => setMode(mode === 'pick' ? null : 'pick')}>늘 허용 {mode === 'pick' ? '▴' : '▾'}</button>}
        <button class={'btn btn-ghost ' + (full ? '' : 'max-md:hidden')} onClick={() => setMode(mode === 'say' ? null : 'say')}>말 전하기</button>
        {!full && <button class="btn md:hidden max-md:py-2.5" onClick={() => { sheetAsk.value = key }} aria-label="더 보기">⋯</button>}
      </div>
      {canAlways && !it.always.ok && (full || mode) && <div class="text-xs text-mute mt-1.5">늘 허용 안 됨: {it.always.reason}</div>}
      {mode === 'pick' && canAlways && it.always.ok && <Picker it={it} onDone={() => setMode(null)} />}
      {mode === 'say' && <SayBox it={it} />}
    </div>
  )
}

// 한 건을 그리다 깨져도 그 건은 최소 칸(명령 글 + 허용·거부)으로 남는다(U2-8: 승인 칸이 안 보이던 일)
class CardGuard extends Component {
  state = { err: null }
  componentDidCatch(err) { this.setState({ err }) }
  render() {
    const it = this.props.it
    if (!this.state.err) return this.props.children
    let t = ''
    try { t = what(it) } catch (e) { t = String(it.prompt || '') }
    return (
      <div class="border-[1.5px] border-warn bg-panel rounded-[14px] px-3 py-2.5">
        <div class="text-[13px] font-semibold text-warn">⚠ 승인 기다림 · {String(it.session)} #{String(it.seq)} (칸을 다 그리지 못했다: {String(this.state.err.message || this.state.err)})</div>
        <pre class="font-mono text-[12.5px] my-2 bg-code rounded-lg px-2.5 py-2 whitespace-pre-wrap break-all max-h-36 overflow-auto">{String(t)}</pre>
        <div class="flex gap-1.5"><button class="btn btn-ok" onClick={() => answer(it, 'y')}>허용</button><button class="btn" onClick={() => answer(it, 'n')}>거부</button></div>
      </div>
    )
  }
}

export function Approvals() {
  const q = queue.value
  if (!q.length) return null
  return (
    <div class="max-h-[42vh] max-md:max-h-[46vh] overflow-auto scroll-thin space-y-2 mb-2.5 rounded-[14px]">
      {q.map(it => <CardGuard key={it.session + '#' + it.seq} it={it}><AskCard it={it} /></CardGuard>)}
    </div>
  )
}

export function AskSheet() {
  const k = sheetAsk.value
  const it = k && queue.value.find(x => x.session + '#' + x.seq === k)
  if (!it) return null
  return (
    <>
      <div class="sheet-back" onClick={() => { sheetAsk.value = null }} />
      <div class="sheet sheet-wide" role="dialog" aria-label="승인 전문">
        <div class="flex items-center mb-2"><span class="font-semibold">승인 전문</span><button class="btn btn-ghost ml-auto !border-0" onClick={() => { sheetAsk.value = null }}>닫기</button></div>
        <AskCard it={it} full />
      </div>
    </>
  )
}

// ── 입력 칸 ──
const HIST_KEY = 'stewart-hist', HIST_MAX = 100
const IMG_MAX = 4

export function Composer({ target }) {
  // target: 'main' 또는 작업자 이름(작업자에게는 지시·긴급)
  const ta = useRef(null)
  const [text, setText] = useState(() => store.get('stewart-draft-' + target, ''))
  const [imgs, setImgs] = useState([])
  const [sug, setSug] = useState({ items: [], on: -1, range: null })
  const [sending, setSending] = useState(false)
  const [failed, setFailed] = useState(null)
  const hist = useRef({ pos: null, draft: '' })
  const cmdCache = useRef(null)
  const timer = useRef(null)
  const st = hub.value
  const isMain = target === 'main'
  const vision = isMain && !!st?.vision
  const main = sessions.value.find(s => s.role === 'main')

  useEffect(() => { setText(store.get('stewart-draft-' + target, '')); setFailed(null) }, [target])
  const fill = composerFill.value
  useEffect(() => {   // 판 105: 되감은 차례의 내 말을 입력 칸으로
    if (fill && fill.target === target) { setText(fill.text); composerFill.value = null; ta.current?.focus() }
  }, [fill, target])
  useEffect(() => { store.set('stewart-draft-' + target, text) }, [text, target])
  useEffect(() => {   // 높이를 글에 맞춘다(최대 40% 화면)
    const t = ta.current; if (!t) return
    t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + 'px'
  }, [text])

  const hideSug = () => setSug({ items: [], on: -1, range: null })
  async function suggest() {
    const t = ta.current; if (!t) return
    const v = t.value, caret = t.selectionStart, before = v.slice(0, caret)
    const m = before.match(/(^|\s)@([^\s@]*)$/)
    try {
      if (m && isMain) {
        const q = m[2]
        const r = await get('/api/files?q=' + encodeURIComponent(q) + '&chat=' + encodeURIComponent(chat.value))
        if (t.selectionStart !== caret) return
        return setSug({ items: (r.files || []).map(f => ({ label: '@' + f, insert: '@' + f + ' ' })), on: 0, range: [caret - q.length - 1, caret] })
      }
      if (/^\/[^\s]*$/.test(before) && isMain) {
        if (!cmdCache.current) cmdCache.current = (await get('/api/commands?chat=' + encodeURIComponent(chat.value))).commands || []
        const q = before.toLowerCase()
        return setSug({ items: cmdCache.current.filter(c => c.name.toLowerCase().startsWith(q)).map(c => ({ label: c.name, hint: c.description || '', insert: c.name + ' ' })), on: 0, range: [0, caret] })
      }
    } catch (e) { /* 고르기는 없어도 된다 */ }
    hideSug()
  }
  // 판 106: 그림 밖 파일 → 작업 폴더 inbox/ (말 끝에 경로 한 줄 — 모델은 read_file 등으로 읽는다)
  async function uploadFiles(fs) {
    for (const f of fs) {
      if (f.size > 20 * 1024 * 1024) { toast(f.name + ': 20MB 넘음'); continue }
      try {
        const b64 = await new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1] || ''); r.onerror = bad; r.readAsDataURL(f) })
        const r = await cpost('/api/main/upload', { name: f.name, data: b64 })
        setText(t => (t ? t.replace(/\s*$/, '') + '\n' : '') + `[올린 파일: ${r.path}]`)
      } catch (e) { toast(f.name + ': ' + e.message) }
    }
  }

  function pick(i) {
    const it = sug.items[i]; if (!it) return
    const [a, b] = sug.range
    const v = text.slice(0, a) + it.insert + text.slice(b)
    setText(v); hideSug()
    requestAnimationFrame(() => { const t = ta.current; const p = a + it.insert.length; t.focus(); t.setSelectionRange(p, p) })
  }
  async function addFiles(files) {   // 2.0MP 로 줄임(글 그림 PNG·사진 JPEG, 원본도 함께 — imgprep.js 판 82). 건너뛸 때는 늘 까닭을 토스트로(판 74)
    const list = [...(files || [])]
    if (!list.length) return
    if (!vision) { toast(isMain ? '이 백엔드는 이미지를 받지 않는다(backends 표 vision = false)' : '이 칸에서는 아직 사진을 붙이지 못한다(메인 칸에서)'); return }
    for (const f of list) {
      try {
        const x = await prepImage(f, {}, { tiles: !!st?.image_tiles })
        if (!x.ok) { toast(x.why); continue }
        if (x.note) toast(`${x.name}: ${x.note}`)
        const add = x.tiles ? x.tiles.map(t => ({ name: t.name, size: t.size, data: t.data })) : [{ name: x.name, size: x.size, data: x.data, orig: x.orig }]
        setImgs(xs => { if (xs.length + add.length > IMG_MAX) { toast(`사진은 한 번에 ${IMG_MAX}장까지`); return xs } return [...xs, ...add] })
      } catch (e) { toast('사진을 못 읽었다: ' + (e?.message || e)) }
    }
  }
  async function send(kind) {
    const v = text.trim()
    if ((!v && !imgs.length) || sending) return
    if (v) { const h = store.get(HIST_KEY, []).filter(x => x !== v); h.push(v); store.set(HIST_KEY, h.slice(-HIST_MAX)) }
    hist.current.pos = null; hideSug(); setSending(true); setFailed(null)
    try {
      if (isMain) {
        const body = { text: v }
        if (imgs.length) body.images = imgs.map(x => ({ name: x.name, data: x.data, ...(x.orig ? { orig: x.orig } : {}) }))
        await cpost('/api/main', body)
      } else {
        await post('/api/session/send', { name: target, text: v, kind: kind || '지시' })
      }
      setText(''); setImgs([]); refresh()
    } catch (e) {
      if (imgs.length) toast('사진과 함께 보내지 못했다: ' + (e?.detail || e?.message || e))   // 서버 거절 문구 그대로(판 74)
      setFailed(e)   // 글은 칸에 남긴다 → [다시 보내기]
    } finally { setSending(false) }
  }
  async function stop() {
    try {
      if (isMain) await cpost('/api/main/stop', {})
      else await post('/api/session/control', { name: target, action: 'pause' })
      refresh()
    } catch (e) { toast('멈춤을 못 보냈다: ' + e.message) }
  }
  function onKey(e) {
    if (sug.items.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); const n = sug.items.length; setSug(s => ({ ...s, on: (s.on + (e.key === 'ArrowDown' ? 1 : -1) + n) % n })); return }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.ctrlKey && !e.metaKey)) { e.preventDefault(); pick(sug.on); return }
      if (e.key === 'Escape') { e.stopPropagation(); hideSug(); return }
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { if (!ta.current.value.trim() && queue.value.length) return; e.preventDefault(); send(); return }   // 빈 칸이면 승인 단축키로
    const t = ta.current, oneLine = !t.value.includes('\n'), h = store.get(HIST_KEY, [])
    if (e.key === 'ArrowUp' && (oneLine || t.selectionStart === 0) && h.length) {
      if (hist.current.pos === null) { hist.current.draft = t.value; hist.current.pos = h.length }
      if (hist.current.pos > 0) { e.preventDefault(); hist.current.pos -= 1; setText(h[hist.current.pos]) }
    } else if (e.key === 'ArrowDown' && hist.current.pos !== null && (oneLine || t.selectionStart === t.value.length)) {
      e.preventDefault(); hist.current.pos += 1
      if (hist.current.pos >= h.length) { hist.current.pos = null; setText(hist.current.draft) } else setText(h[hist.current.pos])
    }
  }
  const working = isMain ? busy.value : ['working', 'waiting_approval'].includes(sessions.value.find(s => s.name === target)?.status)
  const stopping = isMain && st?.stopping
  const pending = isMain ? (st?.pending || []) : []
  return (
    <div class="relative" onDragOver={e => { if (vision) e.preventDefault() }} onDrop={e => { if (e.dataTransfer?.files?.length) { e.preventDefault(); addFiles([...e.dataTransfer.files]) } }}>
      {pending.length > 0 && <div class="text-xs text-mute mx-1 mb-1.5 truncate">대기 줄 {pending.length}: “{pending[pending.length - 1].slice(0, 80)}” — 이번 단계가 끝나면 메인이 받는다</div>}
      {failed && (
        <div class="flex items-center gap-2 text-[13px] text-bad mx-1 mb-1.5">
          <span class="dot dot-bad" /><span class="min-w-0 truncate">못 보냈다: {failed.message}</span>
          {failed.kind === 'token' ? <button class="btn ml-auto" onClick={() => location.reload()}>새로고침</button>
            : failed.kind === 'login' ? <a class="btn ml-auto" href="/login">다시 들어가기</a>
              : <button class="btn ml-auto" onClick={() => send()}>다시 보내기</button>}
        </div>
      )}
      {sug.items.length > 0 && (
        <div class="absolute bottom-full left-0 right-0 mb-2 bg-panel border border-line rounded-xl shadow-card max-h-64 overflow-auto z-20 text-[13.5px]">
          {sug.items.slice(0, 50).map((it, i) => (
            <div key={it.label} class={'px-3 py-1.5 cursor-pointer flex gap-2 ' + (i === sug.on ? 'bg-soft' : '')} onMouseDown={e => { e.preventDefault(); pick(i) }}>
              <span class="font-mono truncate">{it.label}</span>{it.hint && <span class="text-mute truncate">{it.hint}</span>}
            </div>
          ))}
        </div>
      )}
      {true ? (   // 판 124: 한 줄에서 자라는 칸 — 왼쪽 "+", 오른쪽 둥근 보내기(도는 중에는 멈춤). 폰·데스크톱 같은 꼴
        <div class="border border-line bg-panel rounded-[22px] px-1.5 py-1.5 mb-1.5 focus-within:border-mute/60">
          {imgs.length > 0 && <div class="flex gap-1.5 flex-wrap mb-1.5 px-2 pt-1">{imgs.map((im, i) => (
            <button key={i} class="chip" onClick={() => setImgs(xs => xs.filter((_, j) => j !== i))}>{im.name} ✕</button>
          ))}</div>}
          <div class="flex items-end gap-1">
            {isMain && <button class="ibtn !w-10 !h-10 text-mute" onClick={() => { plusSheet.value = true }} aria-label="붙이기·방식"><IconPlus /></button>}
            {!isMain && <label class="ibtn !w-10 !h-10 text-mute cursor-pointer" aria-label="그림 붙이기" title="그림 붙이기(작업자에게)"><IconImage />
              <input type="file" accept="image/*,.heic,.heif" multiple hidden onChange={e => { const fs = [...(e.currentTarget.files || [])]; e.currentTarget.value = ''; addFiles(fs) }} /></label>}
            <span class="relative flex-1 min-w-0 flex">
            {!text && <span class="absolute left-1.5 top-[9px] pointer-events-none text-[16px] leading-[1.45] text-mute whitespace-nowrap overflow-hidden" aria-hidden="true">
              {isMain ? '메시지' : `${target} 에게`}{isMain && !narrow.value && !touch && <span class="opacity-55"> &nbsp;/ 명령{talkHere() ? '' : ' · @ 파일'}</span>}</span>}
            <textarea ref={ta} rows={1} value={text} class="flex-1 min-w-0 resize-none bg-transparent outline-none focus-visible:outline-none text-[16px] leading-[1.45] py-[9px] px-1.5"
              aria-label={isMain ? '메시지' : `${target} 에게`}
              onInput={e => { setText(e.currentTarget.value); hist.current.pos = null; clearTimeout(timer.current); timer.current = setTimeout(suggest, 150) }}
              onKeyDown={onKey} onBlur={() => setTimeout(hideSug, 150)}
              onPaste={e => { const fs = pastedFiles(e.clipboardData); if (fs.length) { e.preventDefault(); addFiles(fs) } }} />
            </span>
            {!isMain && working && text.trim() && <button class="h-10 px-2 text-[14px] text-mute" disabled={sending} onClick={() => send('긴급')}>긴급</button>}
            {working && !text.trim() && !imgs.length
              ? <button class="w-10 h-10 rounded-full grid place-items-center bg-ink text-on-ink flex-none" disabled={stopping} onClick={stop}
                  aria-label={stopping ? '멈추는 중' : '멈춤'}><span class="w-5 h-5"><IconStop /></span></button>
              : <button class={'w-10 h-10 rounded-full grid place-items-center flex-none disabled:!opacity-100 ' + (text.trim() || imgs.length ? 'bg-ink text-on-ink' : 'bg-soft text-mute/60')} disabled={sending || (!text.trim() && !imgs.length)}
                  onClick={() => send()} aria-label={working && isMain ? '대기 줄에 보내기' : '보내기'} title={(working && isMain ? '대기 줄에 보내기' : '보내기') + (touch ? '' : ' (⌘/Ctrl+Enter)')}><span class="w-5 h-5"><IconSend /></span></button>}
          </div>
          {plusSheet.value && isMain && <PlusSheet vision={vision} addFiles={addFiles} uploadFiles={uploadFiles} mode={main?.mode} />}
        </div>
      ) : (
      <div class="border border-line bg-panel rounded-2xl pt-2.5 pb-2 pl-3.5 pr-2.5 shadow-card">
        {imgs.length > 0 && <div class="flex gap-1.5 flex-wrap mb-1.5">{imgs.map((im, i) => (
          <button key={i} class="chip" onClick={() => setImgs(xs => xs.filter((_, j) => j !== i))}>🖼 {im.name} ({Math.round(im.size / 1024)}KB) ✕</button>
        ))}</div>}
        <textarea ref={ta} rows={2} value={text} class="w-full resize-none bg-transparent outline-none text-[15px] leading-relaxed placeholder:text-mute"
          placeholder={isMain ? (chat.value === 'main' ? '메인에게' : '이 대화에') + ' 할 말 — /명령 · @파일' + (vision ? ' · 이미지 붙여넣기' : '') + (touch ? '' : ' · ⌘/Ctrl+Enter 보내기') : `${target} 에게 직접 할 말(메인에게도 알림이 간다)`}
          onInput={e => { setText(e.currentTarget.value); hist.current.pos = null; clearTimeout(timer.current); timer.current = setTimeout(suggest, 150) }}
          onKeyDown={onKey} onBlur={() => setTimeout(hideSug, 150)}
          onPaste={e => { const fs = pastedFiles(e.clipboardData); if (fs.length) { e.preventDefault(); addFiles(fs) } }} />
        <div class="flex items-center gap-1.5 mt-1">
          {vision && <label class="btn btn-ghost px-2.5" title="이미지 붙이기(붙여넣기·끌어다 놓기도 됨)">＋<input type="file" accept="image/*,.heic,.heif" multiple hidden onChange={e => { const fs = [...(e.currentTarget.files || [])]; e.currentTarget.value = ''; addFiles(fs) }} /></label>}
          {isMain && <label class="btn btn-ghost px-2.5" title="파일 올리기(문서·표·PDF 등 — 작업 폴더 inbox/ 에 두고 말 끝에 경로를 붙인다, 20MB 까지)">📎<input type="file" multiple hidden onChange={e => { const fs = [...(e.currentTarget.files || [])]; e.currentTarget.value = ''; uploadFiles(fs) }} /></label>}
          {isMain && main?.mode && <span class="chip" title="메인 권한 모드">{main.mode}</span>}
          <span class="flex-1" />
          {working && isMain && <span class="chip max-md:hidden">Esc 멈춤</span>}
          {!isMain && working && <button class="btn btn-ghost" disabled={!text.trim() || sending} onClick={() => send('긴급')} title="지금 일에 끼워 넣기">긴급</button>}
          {(!working || !!text.trim() || imgs.length > 0) && <button class="btn btn-pri" disabled={sending || (!text.trim() && !imgs.length)} onClick={() => send()}>{sending ? '보내는 중…' : working && isMain ? '대기 줄에' : '보내기'}</button>}
          {working && <button class="btn btn-stop" disabled={stopping} onClick={stop} title={isMain ? '메인의 지금 차례를 끊는다(Esc)' : '이 작업자를 잠시 멈춘다'}>{stopping ? '멈추는 중…' : '■ 멈춤'}</button>}
        </div>
      </div>
      )}
    </div>
  )
}

const talkHere = () => ((hub.value?.chats || []).find(x => x.name === chat.value) || (hub.value?.chats || []).find(x => x.name === 'main') || {}).kind === 'talk'

// 판 124 폰 "+" 시트: 파일 올리기 · 사진 · 방식(이 대화 시트로)
function PlusSheet({ vision, addFiles, uploadFiles, mode }) {
  const c = (hub.value?.chats || []).find(x => x.name === chat.value) || (hub.value?.chats || []).find(x => x.name === 'main')
  const talk = c?.kind === 'talk'   // parlando: 작업 폴더·편집 방식이 뜻이 없다
  const close = () => { plusSheet.value = false }
  return (
    <>
      <div class="sheet-back" onClick={close} />
      <div class="sheet" role="dialog" aria-label="붙이기">
        <label class="row-btn cursor-pointer"><span class="w-5 h-5 text-mute"><IconFile /></span><span>{talk ? '파일 보내기' : '파일 올리기'}</span><span class="sub">{talk ? '이 대화에서 읽는다' : '작업 폴더 inbox/'}</span>
          <input type="file" multiple hidden onChange={e => { const fs = [...(e.currentTarget.files || [])]; e.currentTarget.value = ''; close(); uploadFiles(fs) }} /></label>
        {vision && <label class="row-btn cursor-pointer"><span class="w-5 h-5 text-mute"><IconImage /></span><span>사진</span>
          <input type="file" accept="image/*,.heic,.heif" multiple hidden onChange={e => { const fs = [...(e.currentTarget.files || [])]; e.currentTarget.value = ''; close(); addFiles(fs) }} /></label>}
        {!talk && <button class="row-btn" onClick={() => { close(); workerOpen.value = true }}><span class="w-5 h-5 text-mute"><IconNew /></span><span>작업자 맡기기</span><span class="sub">따로 도는 작업자에게 일 하나</span></button>}
        <button class="row-btn" onClick={() => { close(); convSheet.value = true }}><span class="w-5 h-5 text-mute"><IconSliders /></span><span>{talk ? '생각' : '방식·생각'}</span><span class="sub">{talk ? '' : (MODE_NAME[mode] || mode || '')}</span></button>
        <div class="text-[13px] text-mute pt-2">{talk ? '/ 를 치면 명령을 고른다.' : '/ 를 치면 명령, @ 를 치면 파일을 고른다.'}</div>
      </div>
    </>
  )
}
const MODE_NAME = { plan: '계획', ask: '묻기', edits: '편집', auto: '자동' }

// Esc: 일하는 중이면 멈춤(고르기 판·시트가 열려 있으면 그것만 닫는다)
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || e.defaultPrevented) return
  if (sheetAsk.value) { sheetAsk.value = null; return }
  const st = hub.value
  if (view.value === chat.value && st?.busy && !st.stopping) cpost('/api/main/stop', {}).then(refresh).catch(err => toast('멈춤을 못 보냈다: ' + err.message))
})
