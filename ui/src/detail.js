// 판 126: 과정은 다 닿게 — "조용한 종이"는 접어 두는 것이지 없애는 것이 아니다.
// detail: "자세히 보기" 손잡이(설정 시트·"이 대화" 판, 이 브라우저에 기억). 켜면 생각·작업 카드·차례 숫자·토큰 줄·스킬 줄·진행 원문이 흐름 속에 다 펼쳐진다.
// openTurns: 답 아래 "⋯" 로 펼친 차례 — 그 차례의 작업 카드·생각을 펼친다(두 번 안에 그 차례 과정 전체).
import { signal } from '@preact/signals'

const KEY = 'stewart-detail'
const read = () => { try { return localStorage.getItem(KEY) === '1' } catch { return false } }
export const detail = signal(read())
export function setDetail(v) {
  detail.value = !!v
  try { localStorage.setItem(KEY, v ? '1' : '0') } catch { /* 저장 못 해도 이 화면에서는 켜진다 */ }
}
export const openTurns = signal(new Set())
export function toggleTurn(t) {
  const s = new Set(openTurns.value)
  if (s.has(t)) s.delete(t); else s.add(t)
  openTurns.value = s
}
export const shown = tix => detail.value || openTurns.value.has(tix)
