// 사진 붙이기 전처리(판 70, 사용자 "사진 입력도 Claude 처럼"): 폰 사진은 크고(수 MB·4천 px) HEIC 일 수 있다.
// 판 82(사진 글자 실측 B 채택): 줄이는 기준을 서버 화소 상한(vLLM 2,097,152)에 맞춰 화소 수 MAX_PIXELS(2.0MP) 이하로.
//   글 그림(PNG·WebP — 화면 캡처 꼴)은 PNG 그대로 줄이고, 사진(JPEG·HEIC 등)은 JPEG 0.9. 8MB 를 넘으면 JPEG 로 떨어진다.
//   줄였으면 원본(서버가 받는 꼴·8MB 안)도 함께 보낸다 — 모델이 view_image region 으로 원 해상도를 잘라 다시 본다.
//   긴 그림 조각(opts.tiles, 허브 설정 [hub] image_tiles = true, 기본 끔): 세로/가로가 TILE_ASPECT 를 넘으면 원래 가로폭을 지키며
//   ≤2.0MP 조각 2~3장(10% 겹침)으로. 실측에서 조각은 겹침 때문에 오히려 낮았다(판 78) — 그래서 기본 끔.
// 판 74: 파일 종류(f.type)가 빈 값이어도(안드로이드 파일 고르기) 앞 바이트·확장자로 판정한다. 디코드가 안 돼도
// 서버가 받는 꼴(PNG·JPEG·WebP·GIF)이고 SEND_MAX 안이면 원본을 그대로 보낸다(서버가 앞 바이트로 다시 본다).
export const MAX_PIXELS = 2000000
export const SEND_MAX = 8 * 1024 * 1024        // 서버 images.max_bytes 기본과 같다
export const TILE_ASPECT = 2.2
export const TILE_MAX = 3
export const JPEG_Q = 0.9
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

// img 를 k 배로(crop 이 있으면 원본 좌표 {y, h} 띠만) mime 으로 → base64
function encodeDefault(img, k, mime, crop) {
  const sy = crop ? crop.y : 0, sh = crop ? crop.h : img.naturalHeight
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(sh * k))
  const g = c.getContext('2d')
  if (!g) throw new Error('캔버스를 못 만듦')
  g.drawImage(img, 0, sy, img.naturalWidth, sh, 0, 0, c.width, c.height)
  const out = c.toDataURL(mime, mime === 'image/jpeg' ? JPEG_Q : undefined)
  if (!out.startsWith('data:' + mime)) throw new Error(mime.split('/')[1].toUpperCase() + ' 로 못 바꿈')
  return out.split(',')[1]
}

const b64 = url => url.slice(url.indexOf(',') + 1)
const b64size = d => Math.round(d.length * 0.75)
const ext = mime => (mime === 'image/png' ? '.png' : '.jpg')

// 돌려줌: {ok:true, name, size, data(base64), type, orig?(원본 base64), note?, tiles?:[{name,size,data,type}]} 또는 {ok:false, why}
// deps(시험용): {loadImg, encode(img, k, mime, crop?) → base64}. opts: {tiles: bool}
export async function prepImage(f, deps = {}, opts = {}) {
  const loadImg = deps.loadImg || loadImgDefault, encode = deps.encode || encodeDefault
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
  if (img) {
    const w = img.naturalWidth, h = img.naturalHeight, px = w * h
    const textLike = type === 'image/png' || type === 'image/webp'   // 화면 캡처 꼴: 글자 가장자리를 지키려 PNG
    const orig = plain && f.size <= SEND_MAX ? b64(url) : undefined
    const base = name0.replace(/\.[^.]+$/, '')
    try {
      if (opts.tiles && h / w > TILE_ASPECT) {   // 긴 그림 조각(손잡이, 기본 끔)
        const k = Math.min(1, Math.sqrt(MAX_PIXELS / (w * w * 1.6)))   // 조각 하나가 가로:세로 1:1.6 이하 꼴로
        const th = Math.min(h, Math.floor(MAX_PIXELS / (w * k * k)))
        const step = Math.floor(th * 0.9)
        const tiles = []
        for (let y = 0; tiles.length < TILE_MAX; y += step) {
          const ch = Math.min(th, h - y)
          let mime = textLike ? 'image/png' : 'image/jpeg'
          let data = encode(img, k, mime, { y, h: ch })
          if (b64size(data) > SEND_MAX) { mime = 'image/jpeg'; data = encode(img, k, mime, { y, h: ch }) }
          tiles.push({ name: `${base}-${tiles.length + 1}${ext(mime)}`, size: b64size(data), data, type: mime })
          if (y + ch >= h) break
        }
        if (tiles.length > 1) return { ok: true, name: name0, tiles, orig, note: `긴 그림이라 ${tiles.length}조각으로 나눴다(위에서부터, 10% 겹침)` }
      }
      if (plain && px <= MAX_PIXELS && f.size <= SEND_MAX) return keep
      const k = Math.min(1, Math.sqrt(MAX_PIXELS / px))
      let mime = textLike ? 'image/png' : 'image/jpeg'
      let data = encode(img, k, mime)
      if (b64size(data) > SEND_MAX && mime === 'image/png') { mime = 'image/jpeg'; data = encode(img, k, mime) }
      return { ok: true, name: base + ext(mime), size: b64size(data), data, type: mime, ...(orig && k < 1 ? { orig } : {}) }
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
