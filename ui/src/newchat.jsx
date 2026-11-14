// 판 125: 새 대화 — 누르면 바로 빈 대화가 열린다(판 없이). 자리(stewart · parlando)와 모델 등급은 빈 대화의 첫 화면에서 고르고,
// 첫 말을 보내면 굳는다(다르게 고르면 그 꼴의 빈 대화를 새로 열고 앞 빈 대화는 닫는다 — 지시문·도구는 만들 때 정해지므로).
import { useEffect, useState } from 'preact/hooks'
import { signal } from '@preact/signals'
import { get, post } from './lib.js'
import { hub, chat, openChat, refresh, toast } from './state.js'

const KIND_KEY = 'stewart.newKind', TIER_KEY = 'stewart.newTier'
const read = (k, d) => { try { return localStorage.getItem(k) ?? d } catch { return d } }
const save = (k, v) => { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k) } catch { /* 저장 못 해도 된다 */ } }
export const NEW_TITLE = '새 대화'
export const workerOpen = signal(false)   // "+" 시트의 작업자 맡기기 판
export const tiersSig = signal(null)
export const loadTiers = () => get('/api/tiers').then(r => { tiersSig.value = r; return r }, () => { tiersSig.value = { rows: [] }; return tiersSig.value })
const usable = r => r.connected && r.key !== '없음' && r.status !== 'auth'   // 판 136: 막는 것은 키 없음(바깥)·인증 거부(찔러 본 401·403)뿐
const ST = { ok: '준비됨', down: '닿지 않음', auth: '인증 거부', nokey: '키 없음', none: '연결 없음', unset: '설정 없음', unchecked: '' }

export const isDraft = c => !!c && c.name !== 'main' && c.title === NEW_TITLE && !c.first && !c.busy   // 아직 말하지 않은 새 대화

export async function startNew(kind, tier) {
  const t = tiersSig.value || await loadTiers()
  const drafts = (hub.value?.chats || []).filter(isDraft).map(c => c.name)
  const rows = t.rows || []
  kind = kind ?? read(KIND_KEY, 'talk')
  tier = tier ?? read(TIER_KEY, '')
  if (tier && !rows.some(r => r.tier === tier && usable(r))) tier = ''
  if (!tier && t.instance) tier = rows.find(usable)?.tier || ''   // 따로 도는 허브: 연결된 등급(rubato)
  const r = await post('/api/chat/new', { how: 'fresh', kind, ...(tier ? { tier } : {}), title: NEW_TITLE, from: chat.value })
  openChat(r.chat)
  for (const n of drafts) await post('/api/chat/close', { chat: n }).catch(() => {})   // 앞의 빈 새 대화는 닫는다(쌓이지 않게)
  refresh()
  return r
}
export function newChat() {
  const cur = (hub.value?.chats || []).find(c => c.name === chat.value)
  if (isDraft(cur)) return   // 이미 빈 새 대화를 보고 있다
  startNew().catch(e => toast('새 대화를 못 열었다: ' + e.message))
}

// 빈 대화 첫 화면의 고르기 두 줄 — 글자 단추 + 밑줄, 설명은 고른 것 아래 보조 글 한 줄
export function DraftPick() {
  const [switching, setSwitching] = useState(false)
  useEffect(() => { if (!tiersSig.value) loadTiers() }, [])
  const st = hub.value
  const c = (st?.chats || []).find(x => x.name === (st?.chat || chat.value))
  if (!c || c.name === 'main') return null
  const t = tiersSig.value || { rows: [] }
  const kind = c.kind || 'code', tier = c.tier || ''
  async function pick(k, tr) {
    if ((k === kind && tr === tier) || switching) return
    save(KIND_KEY, k)
    save(TIER_KEY, tr)
    setSwitching(true)
    try {
      await startNew(k, tr)   // 앞 빈 대화(말 없음 — 지난 목록에 남지 않는다)는 startNew 가 닫는다
    } catch (e) { toast('바꾸지 못했다: ' + e.message) } finally { setSwitching(false) }
  }
  const btn = (on, ok, label, onClick, key) => <button key={key} disabled={!ok || switching} aria-pressed={on}
    class={'py-1 ' + (on ? 'text-fg font-semibold underline underline-offset-4 decoration-1' : ok ? 'text-mute hover:text-fg' : 'text-mute/40 cursor-not-allowed')}
    onClick={onClick}>{label}</button>
  const rows = t.rows || []
  const opts = [...(t.instance ? [] : [{ tier: '', label: '기본', ok: true }]), ...rows.map(r => ({ tier: r.tier, label: r.tier, ok: usable(r), r }))]
  const sel = opts.find(o => o.tier === tier)
  const r = sel?.r
  const sub = !sel ? '' : !r ? '허브의 기본 모델' : [r.external ? '바깥 API · ' + r.provider : r.model, ST[r.status]].filter(Boolean).join(' · ')
  const off = opts.filter(o => o.tier && !o.ok && o.r?.connected).map(o => `${o.label} ${o.r.key === '없음' ? '키 없음' : ST[o.r.status]}`)
  return <div class="mt-6 text-[14px] space-y-1.5">
    <div class="flex justify-center gap-4" role="radiogroup" aria-label="자리">
      {btn(kind === 'code', true, 'stewart', () => pick('code', tier), 'code')}
      {btn(kind === 'talk', true, <i>parlando</i>, () => pick('talk', tier), 'talk')}
    </div>
    {rows.length > 0 && <div class="flex flex-wrap justify-center gap-x-4" role="radiogroup" aria-label="모델 등급">
      {opts.map(o => btn(tier === o.tier, o.ok, o.label, () => pick(kind, o.tier), o.tier || '-'))}
    </div>}
    <div class="text-[12.5px] text-mute">{sub}{off.length ? (sub ? ' · ' : '') + off.join(' · ') : ''}</div>
  </div>
}
