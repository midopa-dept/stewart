// 사진 붙이기 전처리(판 70, 사용자 "사진 입력도 Claude 처럼"): 폰 사진은 크고(수 MB·4천 px) HEIC 일 수 있다.
// 긴 변이 MAX_SIDE 를 넘거나 BIG 바이트를 넘거나 HEIC 면 브라우저에서 줄여 JPEG 로 바꾼다(서버는 줄이지 않는다 — 새 의존성 없음).
// 판 74: 파일 종류(f.type)가 빈 값이어도(안드로이드 파일 고르기) 앞 바이트·확장자로 판정한다. 디코드가 안 돼도
// 서버가 받는 꼴(PNG·JPEG·WebP·GIF)이고 SEND_MAX 안이면 원본을 그대로 보낸다(서버가 앞 바이트로 다시 본다).
export const MAX_SIDE = 1600
export const BIG = 1.5 * 1024 * 1024
export const SEND_MAX = 8 * 1024 * 1024        // 서버 images.max_bytes 기본과 같다
const SERVER_OK = /^image\/(png|jpeg|webp|gif)$/
const EXT = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif',
  heic: 'image/heic', heif: 'image/heif', bmp: 'image/bmp', avif: 'image/avif' }

// 앞 바이트 → mime(모르면 '')
export function sniffBytes(b) {
  if (!b || b.length < 4) return ''
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return 'image/png'
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg'
  if (b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46) return 'image/gif'
  if (b.length >= 12 && b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 && b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50) return 'image/webp'
  if (b[0] === 0x42 && b[1] === 0x4d) return 'image/bmp'
  if (b.length >= 12 && b[4] === 0x66 && b[5] === 0x74 && b[6] === 0x79 && b[7] === 0x70) {   // ftyp…: heic·heif·avif
    const brand = String.fromCharCode(b[8], b[9], b[10], b[11])
    if (/^avi[fs]$/.test(brand)) return 'image/avif'
    if (/^(hei[cmsx]|hev[cmsx]|mif1|msf1)$/.test(brand)) return 'image/heic'
  }
  return ''
}

// 그림인지·무슨 꼴인지: 앞 바이트 > 브라우저가 준 type > 확장자. 그림이 아니면 ''
export async function kindOf(f) {
  let head = null
  try { head = new Uint8Array(await f.slice(0, 16).arrayBuffer()) } catch (e) { /* 못 읽음 */ }
  const byBytes = sniffBytes(head)
  if (byBytes) return byBytes
  if (/^image\//.test(f.type || '')) return f.type
  const ext = ((f.name || '').match(/\.([a-z0-9]+)$/i) || [])[1]
  return (ext && EXT[ext.toLowerCase()]) || ''
}

function readDataUrl(f) {
  return new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = () => bad(r.error || new Error('파일을 못 읽음')); r.readAsDataURL(f) })
}

function loadImgDefault(src) {
  return new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => bad(new Error('디코드 실패')); i.src = src })
}

function toJpegDefault(img, k) {
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k))
  const g = c.getContext('2d')
  if (!g) throw new Error('캔버스를 못 만듦')
  g.drawImage(img, 0, 0, c.width, c.height)
  const out = c.toDataURL('image/jpeg', 0.85)
  if (!out.startsWith('data:image/jpeg')) throw new Error('JPEG 로 못 바꿈')
  return out.split(',')[1]
}

const b64 = url => url.slice(url.indexOf(',') + 1)

// 돌려줌: {ok:true, name, size, data(base64), type, note?} 또는 {ok:false, why(화면에 보일 까닭)}
// deps(시험용): {loadImg, toJpeg}
export async function prepImage(f, deps = {}) {
  const loadImg = deps.loadImg || loadImgDefault, toJpeg = deps.toJpeg || toJpegDefault
  const name0 = f.name || 'paste'
  const type = await kindOf(f)
  if (!type) return { ok: false, why: `${name0}: 그림 파일이 아니라 건너뛰었다(종류 ${f.type || '알 수 없음'})` }
  const typed = f.type === type ? f : new File([f], name0, { type })   // 빈 type 이면 맞는 type 을 붙여 디코드
  let url
  try { url = await readDataUrl(typed) } catch (e) { return { ok: false, why: `${name0}: 파일을 못 읽었다(${e.message || e})` } }
  if (url.startsWith('data:;') || url.startsWith('data:application/octet-stream')) url = 'data:' + type + url.slice(url.indexOf(';'))
  const plain = SERVER_OK.test(type) && type !== 'image/gif'
  const keep = { ok: true, name: name0, size: f.size, data: b64(url), type }
  if (type === 'image/gif') return f.size <= SEND_MAX ? keep : { ok: false, why: `${name0}: GIF 가 너무 크다(${Math.round(f.size / 1048576)}MB, 상한 8MB)` }
  const img = await loadImg(url).catch(() => null)
  if (img && plain && f.size <= BIG && Math.max(img.naturalWidth, img.naturalHeight) <= MAX_SIDE) return keep
  if (img) {
    try {
      const data = toJpeg(img, Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight)))
      return { ok: true, name: name0.replace(/\.[^.]+$/, '') + '.jpg', size: Math.round(data.length * 0.75), data, type: 'image/jpeg' }
    } catch (e) {
      if (plain && f.size <= SEND_MAX) return { ...keep, note: `줄이지 못해 원본으로 보낸다(${e.message || e})` }
      return { ok: false, why: `${name0}: 줄이지 못했다(${e.message || e})` }
    }
  }
  // 브라우저가 디코드하지 못함: 서버가 받는 꼴이면 원본 그대로
  if (plain && f.size <= SEND_MAX) return { ...keep, note: '브라우저가 미리 열지 못해 원본으로 보낸다' }
  if (/heic|heif|avif/.test(type)) return { ok: false, why: `${name0}: 이 브라우저가 ${type.split('/')[1].toUpperCase()} 를 열지 못한다 — 사진 앱에서 JPEG 로 보내기·스크린샷으로` }
  if (plain) return { ok: false, why: `${name0}: 너무 크다(${Math.round(f.size / 1048576)}MB, 상한 8MB) — 브라우저가 줄이지도 못했다` }
  return { ok: false, why: `${name0}: 서버가 받지 않는 꼴(${type}) — PNG·JPEG·WebP·GIF 로` }
}

// 붙여넣기: clipboardData.files 가 비어도(안드로이드 키보드 등) items 의 파일을 꺼낸다
export function pastedFiles(cd) {
  const fs = [...(cd?.files || [])]
  if (fs.length) return fs
  return [...(cd?.items || [])].filter(it => it.kind === 'file').map(it => it.getAsFile()).filter(Boolean)
}
