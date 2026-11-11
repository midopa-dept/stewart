// 복사(판 63, 사용자 지적 SW-16): 답 속 코드 블록·도구 결과·사람 손 카드를 단추 하나로.
// navigator.clipboard 는 보안 맥락(https·localhost)에서만 된다 — 폰에서 테일넷 http 로 열면 textarea + execCommand('copy') 로 떨어진다.
export function fallbackCopy(text) {
  const a = document.createElement('textarea')
  a.value = text
  a.setAttribute('readonly', '')
  a.style.position = 'fixed'; a.style.top = '0'; a.style.left = '0'; a.style.opacity = '0'
  document.body.appendChild(a)
  a.focus(); a.select()
  try { a.setSelectionRange(0, text.length) } catch (e) { /* 옛 브라우저 */ }
  let ok = false
  try { ok = document.execCommand('copy') } catch (e) { ok = false }
  a.remove()
  return ok
}

export function copyText(text) {
  const t = String(text ?? '')
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(t).then(() => true, () => fallbackCopy(t))
  }
  return Promise.resolve(fallbackCopy(t))
}

// 단추 글을 잠깐 바꿔 알린다("복사됨" / "복사 못 함")
export function flash(btn, ok) {
  if (!btn) return
  const was = btn.dataset.label || btn.textContent
  btn.dataset.label = was
  btn.textContent = ok ? '복사됨' : '복사 못 함'
  clearTimeout(btn._t)
  btn._t = setTimeout(() => { btn.textContent = was }, 1300)
}

// 마크다운 코드 블록의 [복사](lib.js 가 그리는 .cb 안 .cp-btn) — 그림이 innerHTML 이라 문서에서 한 번에 받는다
if (typeof document !== 'undefined') {
  document.addEventListener('click', e => {
    const b = e.target && e.target.closest && e.target.closest('.cp-btn')
    if (!b) return
    const code = b.closest('.cb')?.querySelector('pre code')
    if (!code) return
    e.preventDefault()
    copyText(code.textContent).then(ok => flash(b, ok))
  })
}
