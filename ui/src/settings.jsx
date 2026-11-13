// 판 125: 설정 시트 — 등급 넷이 어느 서버·모델을 가리키는지·닿는지, 바꾸기(백엔드·주소·모델·동시 수·상한), 키 넣기(키체인, 값은 다시 보이지 않음).
// 파일(backends.toml)이 정본이고 시트는 그 파일의 고칠 줄만 바꾼다(서버가 백업·검사). 새 대화·새 작업자부터 적용.
import { useEffect, useState } from 'preact/hooks'
import { signal } from '@preact/signals'
import { get, post } from './lib.js'
import { toast } from './state.js'
import { narrow } from './mobile.js'
import { tiersSig } from './newchat.jsx'

export const settingsOpen = signal(false)
const ST = { ok: '준비됨', down: '닿지 않음', auth: '인증 거부', nokey: '키 없음', none: '연결 없음', unset: '설정 없음', unchecked: '' }

function rowSub(r) {
  if (!r.connected) return ST[r.status] || r.source
  const where = r.external ? '바깥 API · ' + r.provider : r.model + (r.source === '기본 모델' ? ' (기본)' : '')
  return [where, r.key === '없음' ? '키 없음' : ST[r.status]].filter(Boolean).join(' · ')
}

function Field({ label, children, note }) {
  return <label class="block py-2 border-b border-line">
    <span class="text-[13px] text-mute">{label}</span>
    <div class="mt-1">{children}</div>
    {note && <div class="text-[12px] text-mute mt-1">{note}</div>}
  </label>
}
const inp = 'w-full bg-transparent text-[15px] outline-none py-1 border-b border-transparent focus:border-line disabled:text-mute'

function Edit({ v, r, back, reload }) {
  const bks = v.backends || []
  const cur = bks.find(b => b.name === r.backend)
  const [bname, setB] = useState(r.source === '표' || r.source === '이 허브의 모델' ? r.backend : '')
  const isNew = bname === '+'
  const b0 = bks.find(b => b.name === bname)
  const [f, setF] = useState({ name: '', base_url: b0?.base_url || '', model: b0?.model || '', concurrency: b0?.concurrency || 1 })
  const [cap, setCap] = useState({ day: r.caps?.usd_day ?? '', chat: r.caps?.usd_chat ?? '' })
  const [models, setModels] = useState(null), [key, setKey] = useState(''), [busy, setBusy] = useState(false)
  useEffect(() => { const b = bks.find(x => x.name === bname); if (b) setF({ name: '', base_url: b.base_url, model: b.model, concurrency: b.concurrency }) }, [bname])
  const dis = !v.editable || busy
  const tb = isNew ? f.name : bname
  const ext = r.external || (isNew && /^https:\/\//.test(f.base_url))
  const bf = (isNew || (b0 && (f.base_url !== b0.base_url || f.model !== b0.model || +f.concurrency !== b0.concurrency)))
    ? { base_url: f.base_url, model: f.model, concurrency: +f.concurrency || 1 } : null
  const tf = { backend: tb || null, ...(r.manual && r.external ? { max_usd_day: cap.day === '' ? null : +cap.day, max_usd_chat: cap.chat === '' ? null : +cap.chat } : {}) }
  const preview = [...(bf ? [`[backends.${tb || '<이름>'}]`, ...Object.entries(bf).map(([k, x]) => `${k} = ${JSON.stringify(x)}`), ''] : []),
    `[tiers.${r.tier}]`, ...Object.entries(tf).filter(([, x]) => x !== null).map(([k, x]) => `${k} = ${JSON.stringify(x)}`)].join('\n')
  async function save(confirmOutside) {
    if (isNew && !/^[A-Za-z0-9_-]+$/.test(f.name)) return toast('새 서버 이름은 영숫자·_- 로')
    setBusy(true)
    try {
      await post('/api/settings/tier', { tier: r.tier, backend: tb || undefined, backend_fields: bf || {}, tier_fields: tf, confirm_outside: !!confirmOutside })
      toast('저장했다 — 새 대화·새 작업자부터', 'ok', 4000); tiersSig.value = null; await reload(); back()
    } catch (e) {
      if (/바깥 주소/.test(e.message) && confirm(e.message.replace(' — 확인한 뒤 다시', '') + '\n그래도 저장할까요?')) { setBusy(false); return save(true) }
      toast('저장 못 함: ' + e.message)
    } finally { setBusy(false) }
  }
  async function putKey() {
    setBusy(true)
    try { const x = await post('/api/settings/key', { tier: r.tier, value: key }); setKey(''); toast(`키를 키체인(${x.keychain})에 넣었다`, 'ok', 4000); tiersSig.value = null; await reload() }
    catch (e) { toast('키를 못 넣음: ' + e.message) } finally { setBusy(false) }
  }
  async function load() {
    try { const x = await post('/api/settings/probe', { base_url: f.base_url }); setModels(x.models || []); if (x.models?.[0] && !f.model) setF({ ...f, model: x.models[0] }) }
    catch (e) { toast('불러오지 못함: ' + e.message) }
  }
  return <div>
    <div class="flex items-center gap-2 mb-1"><button class="text-mute hover:text-fg text-[14px]" onClick={back}>‹ 등급</button>
      <span class="font-semibold text-[17px] ml-1">{r.tier}</span><span class="text-[13px] text-mute">{rowSub(r)}</span></div>
    <Field label="서버(백엔드)">
      <select class={inp} disabled={dis} value={bname} onChange={e => setB(e.currentTarget.value)}>
        {!v.instance && <option value="">기본 모델{v.default ? ` (${v.default})` : ''}</option>}
        {bks.map(b => <option key={b.name} value={b.name}>{b.name} — {b.external ? '바깥 API' : b.model}</option>)}
        <option value="+">새 서버…</option>
      </select>
    </Field>
    {(isNew || b0) && <>
      {isNew && <Field label="새 서버 이름"><input class={inp} disabled={dis} placeholder="small" value={f.name} onInput={e => setF({ ...f, name: e.currentTarget.value })} /></Field>}
      <Field label="주소" note={b0 && !isNew ? `이 서버를 쓰는 다른 등급에도 함께 바뀐다` : ''}>
        <div class="flex gap-2"><input class={inp} disabled={dis} placeholder="http://<서버 주소>:<포트>/v1" value={f.base_url} onInput={e => setF({ ...f, base_url: e.currentTarget.value })} />
          <button class="text-[13px] text-mute hover:text-fg flex-none disabled:opacity-40" disabled={dis || !f.base_url} onClick={e => { e.preventDefault(); load() }}>불러오기</button></div>
      </Field>
      <Field label="모델 이름">
        {models?.length ? <select class={inp} disabled={dis} value={f.model} onChange={e => setF({ ...f, model: e.currentTarget.value })}>{models.map(m => <option key={m}>{m}</option>)}</select>
          : <input class={inp} disabled={dis} value={f.model} onInput={e => setF({ ...f, model: e.currentTarget.value })} />}
      </Field>
      <Field label="동시 요청 수"><input class={inp} type="number" min="1" disabled={dis} value={f.concurrency} onInput={e => setF({ ...f, concurrency: e.currentTarget.value })} /></Field>
    </>}
    {ext && r.connected && <Field label={`키 — ${r.key === '있음' ? '있음' : '없음'}`} note="값은 키체인에만 들어가고 다시 보이지 않는다">
      <div class="flex gap-2"><input class={inp} type="password" autocomplete="off" disabled={dis} placeholder={r.key === '있음' ? '바꾸려면 새 키' : '키 붙여 넣기'} value={key} onInput={e => setKey(e.currentTarget.value)} />
        <button class="text-[13px] text-mute hover:text-fg flex-none disabled:opacity-40" disabled={dis || !key} onClick={e => { e.preventDefault(); putKey() }}>넣기</button></div>
    </Field>}
    {r.manual && r.external && <Field label="돈 상한(달러, 0 = 끔)" note={r.priced ? '' : '이 서버에 가격표가 없어 돈을 셀 수 없다'}>
      <div class="flex gap-4 text-[14px]"><span class="flex items-center gap-1">하루 <input class={inp + ' !w-20'} type="number" min="0" step="0.5" disabled={dis} value={cap.day} onInput={e => setCap({ ...cap, day: e.currentTarget.value })} /></span>
        <span class="flex items-center gap-1">대화 <input class={inp + ' !w-20'} type="number" min="0" step="0.5" disabled={dis} value={cap.chat} onInput={e => setCap({ ...cap, chat: e.currentTarget.value })} /></span></div>
    </Field>}
    <div class="pt-3"><div class="text-[12.5px] text-mute mb-1">이렇게 적힌다 — {v.file}</div>
      <pre class="text-[12.5px] bg-soft rounded-lg px-3 py-2 overflow-x-auto">{preview}</pre></div>
    {v.editable ? <div class="flex justify-end pt-3"><button class="btn-pri h-11 px-6 rounded-xl" disabled={dis} onClick={() => save(false)}>저장</button></div>
      : <div class="text-[13px] text-mute pt-3">{v.why}</div>}
  </div>
}

export function SettingsSheet() {
  const [v, setV] = useState(null), [sel, setSel] = useState(null)
  const reload = () => get('/api/settings').then(setV, e => toast('설정을 못 읽음: ' + e.message))
  useEffect(() => { reload() }, [])
  const close = () => { settingsOpen.value = false }
  async function revert() {
    if (!confirm(`${v.file} 을 바로 앞 백업으로 되돌릴까요? (지금 것은 다시 백업으로 남는다)`)) return
    try { const x = await post('/api/settings/revert', {}); setV(x.view); toast('되돌렸다', 'ok') } catch (e) { toast('못 되돌림: ' + e.message) }
  }
  const r = sel && v?.rows.find(x => x.tier === sel)
  return <>
    <div class="sheet-back" onClick={close} />
    <div class={'sheet sheet-wide max-h-[88vh] overflow-auto ' + (narrow.value ? '' : '!w-[520px]')} role="dialog" aria-label="설정">
      {!v ? <div class="text-mute py-6 text-center">읽는 중…</div> : r ? <Edit v={v} r={r} back={() => setSel(null)} reload={reload} /> : <>
        <div class="flex items-baseline gap-2"><span class="text-[13px] text-mute">설정</span>
          <span class="text-[12.5px] text-mute ml-auto">{v.instance ? '이 허브는 rubato 만 연결된 stewart' : ''}</span></div>
        <div class="text-[17px] font-semibold mt-1 mb-1">모델 등급</div>
        {v.rows.map(x => <button key={x.tier} class="row-btn w-full" onClick={() => setSel(x.tier)}>
          <span>{x.tier}</span><span class={'sub truncate max-w-[70%] ' + (x.external && x.connected ? 'text-warn' : !x.connected || x.key === '없음' ? 'opacity-60' : '')}>{rowSub(x)}</span></button>)}
        <div class="text-[12.5px] text-mute pt-3 leading-relaxed">
          고친 것은 {v.file} 에 적힌다(새 대화·새 작업자부터). 열린 대화는 만들 때의 모델 그대로.
          {v.backups > 0 && v.editable && <> · <button class="underline underline-offset-2 hover:text-fg" onClick={revert}>되돌리기</button></>}
          {!v.editable && <div class="mt-1">{v.why}</div>}
        </div>
        <div class="flex justify-end pt-2"><button class="text-[14px] text-mute hover:text-fg" onClick={close}>닫기</button></div>
      </>}
    </div>
  </>
}
