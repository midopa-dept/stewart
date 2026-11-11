// 작업자를 고르면 가운데에 그 작업자의 대화(받은 말 · 모델 답 · 도구 · 결과 접힘)와 단추(멈춤·이어 가기·끝내기)
import { useState, useEffect, useRef } from 'preact/hooks'
import { get, post } from './lib.js'
import { sessions, refresh, toast } from './state.js'
import { Md, DiffCard } from './thread.jsx'
import { STATUS } from './side.jsx'

export function useWorker(name) {
  const [conv, setConv] = useState(null), [detail, setDetail] = useState(null)
  const total = useRef(-1)
  useEffect(() => {
    total.current = -1; setConv(null); setDetail(null)
    if (!name) return undefined          // 메인을 볼 때는 부르지 않는다
    let alive = true
    const load = async () => {
      try {
        const d = await get('/api/session?name=' + encodeURIComponent(name))
        if (!alive || d.error) return
        setDetail(d)
        const c = await get('/api/session/conversation?name=' + encodeURIComponent(name))
        if (alive && Array.isArray(c.items) && c.total !== total.current) { total.current = c.total; setConv({ ...c, name }) }
      } catch (e) { /* 다음 번에 */ }
    }
    load()
    const t = setInterval(load, 2000)
    return () => { alive = false; clearInterval(t) }
  }, [name])
  return { conv, detail, setConv }
}

function Item({ it }) {
  const [open, setOpen] = useState(false)
  if (typeof it.text !== 'string') it = { ...it, text: String(it.text ?? '') }
  if (it.kind === 'diff') return <DiffCard text={it.text} stat={it.stat} />
  if (it.kind === 'assistant') return <div class="my-4"><Md text={it.text} /></div>
  if (it.kind === 'user') return (
    <div class="flex justify-end my-4"><div class="bg-soft px-3.5 py-2.5 rounded-[16px_16px_4px_16px] max-w-[78%] max-md:max-w-[88%] whitespace-pre-wrap break-words">
      <div class="text-xs text-mute mb-0.5">{it.interject ? '끼워 넣은 말' : '받은 말'}</div>{it.text}</div></div>)
  if (it.kind === 'result' || it.kind === 'env') return (
    <div class="ml-7 -mt-1 mb-1">
      <button class="text-xs text-mute hover:underline" onClick={() => setOpen(!open)}>{it.kind === 'env' ? '엔진이 붙인 환경 정보' : '결과 · ' + it.text.split('\n')[0].slice(0, 70)} {open ? '▴' : '▾'}</button>
      {open && <pre class="mt-1 bg-code rounded-lg px-2.5 py-1.5 font-mono text-xs text-mute whitespace-pre-wrap break-all max-h-72 overflow-auto">{it.text}</pre>}
    </div>)
  if (it.kind === 'tool') return <div class="flex gap-2.5 items-start text-[13.5px] my-1"><span class="w-5 h-5 rounded-md grid place-items-center flex-none bg-soft text-mute text-[11px]">⏺</span><span class="font-mono text-[13px] break-all">{it.text}</span></div>
  return <div class="text-[13px] text-mute my-2 whitespace-pre-wrap">{it.text}</div>
}

export function WorkerThread({ name, conv, detail, setConv }) {
  async function more() {
    const r = await get(`/api/session/conversation?name=${encodeURIComponent(name)}&before=${conv.start}&limit=80`)
    if (Array.isArray(r.items)) setConv({ ...conv, start: r.start, items: [...r.items, ...conv.items] })
  }
  if (!conv || conv.name !== name || !Array.isArray(conv.items)) return <div class="text-mute py-10 text-center">읽는 중…</div>
  return (
    <>
      {conv.start > 0 && <button class="btn btn-ghost w-full mb-3" onClick={more}>앞 {conv.start}줄 더 보기</button>}
      {!conv.items.length && <div class="text-mute py-10 text-center">아직 대화가 없다</div>}
      {conv.items.map((it, i) => <Item key={i} it={it} />)}
      {detail?.live && <div class="my-4"><Md text={detail.live + ' ▍'} /></div>}
    </>
  )
}

export function WorkerControls({ name }) {
  const s = sessions.value.find(x => x.name === name)
  if (!s) return null
  const act = async (a) => { try { await post('/api/session/control', { name, action: a }); refresh() } catch (e) { toast('안 됨: ' + e.message) } }
  const done = ['stopped', 'failed'].includes(s.status)
  return (
    <div class="flex gap-1.5 items-center">
      <span class="chip max-md:hidden">{(STATUS[s.status] || [s.status])[0]}</span>
      {s.status === 'paused' ? <button class="btn btn-ok !py-1" onClick={() => act('resume')}>이어 가기</button>
        : done ? <button class="btn btn-ok !py-1" onClick={() => act('resume')}>다시 열기</button>
          : <button class="btn !py-1" onClick={() => act('pause')}>멈춤</button>}
      {!done && <button class="btn btn-ghost btn-bad !py-1" onClick={() => { if (confirm(name + ' 을 끝낼까요? (기록은 남고 다시 열 수 있다)')) act('stop') }}>끝내기</button>}
    </div>
  )
}

export function WorkerInfo({ detail }) {
  if (!detail) return <div class="text-mute text-sm p-4">읽는 중…</div>
  return (
    <div class="px-4 py-3 text-[13px] space-y-3">
      <div><div class="font-semibold mb-1">보고</div>
        {!detail.reports?.length ? <div class="text-mute">아직 없음</div> : detail.reports.slice().reverse().map(p => (
          <div key={p.seq} class="mb-2"><div class="text-xs text-mute">#{p.seq} {p.kind || ''} · {p.reason || ''}</div>
            <pre class="bg-code rounded-lg px-2.5 py-1.5 font-mono text-xs whitespace-pre-wrap max-h-60 overflow-auto">{p.text}</pre></div>))}
      </div>
      <div><div class="font-semibold mb-1">바뀐 파일(git diff --stat)</div><pre class="bg-code rounded-lg px-2.5 py-1.5 font-mono text-xs whitespace-pre-wrap">{detail.diff_stat || '(없음)'}</pre></div>
      {detail.todos?.length > 0 && <div><div class="font-semibold mb-1">할 일</div>{detail.todos.map((t, i) => <div key={i}>[{t.status}] {t.content}</div>)}</div>}
      <div><div class="font-semibold mb-1">기록(최근)</div>
        <pre class="bg-code rounded-lg px-2.5 py-1.5 font-mono text-[11.5px] whitespace-pre-wrap max-h-80 overflow-auto">{(detail.events || []).map(e => `${(e.ts || '').slice(11, 19)} ${e.kind}${e.step != null ? ' s' + e.step : ''}${e.name ? ' ' + e.name : ''}${e.action ? ' ' + e.action : ''}`).join('\n')}</pre></div>
    </div>
  )
}
