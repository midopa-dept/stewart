// 판 124: 폰 화면 상태 — 좁은 화면 여부, "이 대화" 시트·"+" 시트, 키보드 높이(visualViewport)
import { signal } from '@preact/signals'

const MQ = '(max-width: 767px)'
const mq = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(MQ) : null
export const narrow = signal(!!mq?.matches)
mq?.addEventListener?.('change', e => { narrow.value = e.matches })

// 터치 기기: 키보드 단축키 안내(⌘/Ctrl·Esc)를 지운다
export const touch = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(hover: none) and (pointer: coarse)').matches : false

export const convSheet = signal(false)   // "이 대화" 시트(머리의 대화 이름을 누르면)
export const plusSheet = signal(false)   // 입력 칸 "+" 시트(붙이기·방식)

// 키보드가 올라오면 화면 높이를 보이는 칸(visualViewport)에 맞춘다 — 입력 칸이 키보드 위에 붙는다
const vv = typeof window !== 'undefined' ? window.visualViewport : null
function fit() {
  if (!vv) return
  document.documentElement.style.setProperty('--app-h', Math.round(vv.height) + 'px')
}
if (vv) { vv.addEventListener('resize', fit); vv.addEventListener('scroll', fit); fit() }
