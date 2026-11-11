// 생각(reasoning) 블록(판 56). 꼴은 빌려 왔다(ui/THIRD_PARTY.md):
// - 받는 동안 아래로 따라가기, 위로 올리면 멈추고 바닥 근처로 오면 다시 따라가기: llama.cpp tools/ui ChatMessageReasoningBlock.svelte (MIT, The ggml authors)
// - 받는 중 소제목(마지막 제목·굵은 줄)·끝나면 "생각 · 12초": opencode packages/session-ui timeline/projection.ts reasoningHeading (MIT, opencode)
import { useRef, useEffect, useState } from 'preact/hooks'
import { fmtSecs } from './lib.js'

const AT_BOTTOM_PX = 24

function lastMatch(text, re, i = 1) {
  let best = null
  for (const m of text.matchAll(re)) best = { index: m.index, value: (m[i] || m[i + 1] || '').replace(/<[^>]+>/g, ' ').replace(/[*_`]/g, '').trim() }
  return best && best.value ? best : null
}

// 마지막 제목(#…, 굵은 글만 있는 줄). 없으면 빈 글
export function reasoningHeading(text) {
  const t = String(text || '').replace(/\r\n?/g, '\n')
  const cands = [lastMatch(t, /^\s{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$/gm), lastMatch(t, /^\s*(?:\*\*((?:(?!\*\*).)+)\*\*|__((?:(?!__).)+)__)\s*$/gm)]
    .filter(Boolean)
  if (!cands.length) return ''
  return cands.reduce((a, b) => (b.index >= a.index ? b : a)).value.slice(0, 80)
}

export function ThinkBlock({ text, streaming, secs, chars, cut, since }) {
  const [open, setOpen] = useState(false)
  const el = useRef(null), up = useRef(false)
  useEffect(() => {   // 받는 동안 따라가기
    const s = el.current
    if (!s || !streaming || up.current) return
    const f = requestAnimationFrame(() => { if (el.current && !up.current) el.current.scrollTop = el.current.scrollHeight })
    return () => cancelAnimationFrame(f)
  }, [text, open, streaming])
  const onScroll = () => {
    const s = el.current
    if (!s) return
    up.current = s.scrollHeight - s.clientHeight - s.scrollTop > AT_BOTTOM_PX
  }
  const head = streaming ? reasoningHeading(text) : ''
  const took = secs != null ? fmtSecs(secs) : since ? fmtSecs(Math.max(0, Date.now() / 1000 - since)) : ''
  return (
    <div class="my-2 text-[13px]">
      <button class="text-mute hover:underline text-left" onClick={() => setOpen(!open)}>
        {streaming ? <span class="spin mr-1.5 align-middle" /> : '💭 '}
        {streaming ? '생각하는 중' : '생각'}{took ? ' · ' + took : ''}{chars ? ` · ${chars}자` : ''}
        {head ? <span class="text-fg"> — {head}</span> : null} {open ? '▴' : '▾'}
      </button>
      {open && <div ref={el} onScroll={onScroll}
        class="mt-1 border-l-2 border-line pl-3 text-mute whitespace-pre-wrap break-words max-h-[28rem] overflow-auto">
        {cut ? <div class="text-xs italic mb-1">(앞은 잘림 — 끝 부분만)</div> : null}{text}</div>}
    </div>
  )
}
