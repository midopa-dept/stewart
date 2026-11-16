// 판 162: 대화 안 작업자(모델이 workers 도구로 띄운 것)의 걸음 — 그 작업자를 띄운 대화 안 옆 판.
// 허브 세션 작업자 보기와 같은 항목(받은 일·생각(접힘)·답·도구·결과·diff), 2초마다 다시 읽는다(걸음이 끝날 때마다 늘어난다)
import { useEffect, useRef, useState } from 'preact/hooks'
import { get } from './lib.js'
import { chat } from './state.js'
import { innerWorker } from './thread.jsx'
import { Item } from './worker.jsx'

const ST = { queued: '줄 서는 중', running: '도는 중', done: '끝', cancelled: '취소' }

export function InnerWorkerPane() {
  const id = innerWorker.value, c = chat.value
  const [d, setD] = useState(null)
  const total = useRef(-1), box = useRef(null)
  useEffect(() => { innerWorker.value = null }, [c])   // 다른 대화로 가면 닫는다(작업자는 띄운 대화의 것)
  useEffect(() => {
    total.current = -1; setD(null)
    if (!id) return undefined
    let alive = true
    const load = async () => {
      try {
        const r = await get(`/api/chat/worker?chat=${encodeURIComponent(c)}&id=${encodeURIComponent(id)}`)
        if (!alive) return
        if (r.error) { setD({ error: r.error }); return }
        setD(prev => (prev && !prev.error && r.total === total.current) ? { ...prev, ...r, items: prev.items, start: prev.start } : r)
        total.current = r.total
      } catch { /* 다음 번에 */ }
    }
    load()
    const t = setInterval(load, 2000)
    return () => { alive = false; clearInterval(t) }
  }, [id, c])
  useEffect(() => { const b = box.current; if (b) b.scrollTop = b.scrollHeight }, [d?.total])
  if (!id) return null
  async function more() {
    const r = await get(`/api/chat/worker?chat=${encodeURIComponent(c)}&id=${encodeURIComponent(id)}&before=${d.start}&limit=80`)
    if (Array.isArray(r.items)) setD({ ...d, start: r.start, items: [...r.items, ...d.items] })
  }
  return (
    <div class="absolute inset-y-0 right-0 z-30 w-[min(560px,100%)] bg-bg border-l border-line shadow-card flex flex-col" role="dialog" aria-label={'작업자 ' + id}>
      <div class="flex items-center gap-2 px-3 py-2 border-b border-line text-[13.5px]">
        <b>작업자 {id}</b>
        {d && !d.error && <span class="text-mute truncate">{[d.tier, ST[d.status] || d.status, d.secs + '초', d.collected ? '결과 거둠' : ''].filter(Boolean).join(' · ')}</span>}
        <button class="ml-auto btn btn-ghost !px-2 !py-0.5" onClick={() => { innerWorker.value = null }} aria-label="닫기">✕</button>
      </div>
      <div ref={box} class="flex-1 overflow-auto px-3 py-2 text-[14px]">
        {!d ? <div class="text-mute py-10 text-center">읽는 중…</div>
          : d.error ? <div class="text-mute py-10 text-center">이 대화에 그 작업자가 없다(허브를 다시 켰거나 대화가 바뀜)</div>
            : <>
              {d.task && <div class="text-[12.5px] text-mute mb-2 whitespace-pre-wrap">맡은 일: {d.task}</div>}
              {d.start > 0 && <button class="btn btn-ghost w-full mb-3" onClick={more}>앞 {d.start}줄 더 보기</button>}
              {!d.items.length && <div class="text-mute py-10 text-center">아직 걸음이 없다</div>}
              {d.items.map((it, i) => <Item key={i} it={it} />)}
              {d.status === 'running' && <div class="text-mute text-[13px] my-2"><span class="spin mr-1.5 align-middle" />다음 걸음을 기다리는 중</div>}
            </>}
      </div>
    </div>
  )
}
