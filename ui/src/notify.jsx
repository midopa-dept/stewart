// 새 승인 대기 알림: 진동·짧은 소리·알림(화면이 열려 있을 때. 폰이 잠기면 허브 ntfy 길을 쓴다) + 탭 제목
import { useState } from 'preact/hooks'
import { effect } from '@preact/signals'
import { store } from './lib.js'
import { queue, hub } from './state.js'

const notifOn = () => store.get('stewart-notif', '0') === '1'
let audio = null, swReg = null
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').then(r => { swReg = r }).catch(() => {})

const seen = new Set()
let first = true
effect(() => {
  const q = queue.value, st = hub.value
  if (!st) return
  const n = q.length
  document.title = (n ? `(${n}) 승인 대기 — ` : st.busy ? '… ' : '') + 'stewart hub'
  const fresh = q.filter(it => !seen.has(it.session + '#' + it.seq))
  q.forEach(it => seen.add(it.session + '#' + it.seq))
  if (first) { first = false; return }            // 처음 연 화면의 기존 대기는 울리지 않는다
  if (!fresh.length || !notifOn()) return
  try { navigator.vibrate?.([200, 100, 200]) } catch (e) { /* */ }
  try {
    if (audio) { const o = audio.createOscillator(), g = audio.createGain(); o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(audio.destination); o.start(); o.stop(audio.currentTime + 0.15) }
  } catch (e) { /* */ }
  if (window.Notification && Notification.permission === 'granted' && (document.hidden || !document.hasFocus())) {
    const it = fresh[0], body = `${it.session}: ${it.kind}${fresh.length > 1 ? ` 외 ${fresh.length - 1}건` : ''}`
    const opt = { body, tag: 'stewart-approval', renotify: true }
    if (swReg) swReg.showNotification('stewart 승인 대기', opt).catch(() => {})
    else { try { new Notification('stewart 승인 대기', opt) } catch (e) { /* */ } }
  }
})

export function NotifToggle() {
  const [on, setOn] = useState(notifOn())
  async function flip() {
    const v = !on
    store.set('stewart-notif', v ? '1' : '0'); setOn(v)
    if (v) {
      try { audio = audio || new (window.AudioContext || window.webkitAudioContext)() } catch (e) { /* */ }
      if (window.Notification && Notification.permission === 'default') { try { await Notification.requestPermission() } catch (e) { /* */ } }
      if (window.Notification && Notification.permission === 'denied') alert('이 브라우저에서 알림이 막혀 있다. 진동·소리·탭 제목만 쓴다.')
    }
  }
  return <button class="underline-offset-2 hover:underline" onClick={flip} title="새 승인 대기 때 알림·진동·소리">{on ? '알림 끄기' : '알림 켜기'}</button>
}
