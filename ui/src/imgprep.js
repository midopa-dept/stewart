// 사진 붙이기 전처리(판 70, 사용자 "사진 입력도 Claude 처럼"): 폰 사진은 크고(수 MB·4천 px) HEIC 일 수 있다.
// 긴 변이 MAX_SIDE 를 넘거나 BIG 바이트를 넘거나 HEIC 면 브라우저에서 줄여 JPEG 로 바꾼다(서버는 줄이지 않는다 — 새 의존성 없음).
// 브라우저가 그 꼴을 못 열면(예: HEIC 를 못 여는 브라우저) 원본 그대로 두지 않고 null(화면이 안내한다).
export const MAX_SIDE = 1600
export const BIG = 1.5 * 1024 * 1024
const KEEP = /^image\/(png|jpeg|webp|gif)$/

function readDataUrl(f) {
  return new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = bad; r.readAsDataURL(f) })
}

function loadImg(url) {
  return new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url })
}

// 돌려줌: {name, size, data(base64, 앞머리 없이), type} 또는 null(못 엶)
export async function prepImage(f) {
  const heic = /heic|heif/i.test(f.type) || /\.(heic|heif)$/i.test(f.name || '')
  const url = await readDataUrl(f)
  if (KEEP.test(f.type) && !heic && f.size <= BIG && f.type !== 'image/gif') {
    const img = await loadImg(url).catch(() => null)
    if (img && Math.max(img.naturalWidth, img.naturalHeight) <= MAX_SIDE) return { name: f.name || 'paste.png', size: f.size, data: url.split(',')[1], type: f.type }
  }
  if (f.type === 'image/gif' && f.size <= BIG) return { name: f.name, size: f.size, data: url.split(',')[1], type: f.type }   // 움직이는 그림은 그대로
  const img = await loadImg(url).catch(() => null)
  if (!img) return null
  const k = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k))
  c.getContext('2d').drawImage(img, 0, 0, c.width, c.height)
  const out = c.toDataURL('image/jpeg', 0.85)
  const data = out.split(',')[1]
  const name = (f.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg'
  return { name, size: Math.round(data.length * 0.75), data, type: 'image/jpeg' }
}
