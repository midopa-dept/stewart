// node --test ui/test/imgprep.test.mjs — 가짜 File(type 빈 값·JPEG·PNG·큰 PNG·그림 아님)·붙여넣기
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { prepImage, kindOf, sniffBytes, pastedFiles, BIG } from '../src/imgprep.js'

globalThis.FileReader ??= class {   // node 에는 FileReader 가 없다: readAsDataURL 만
  readAsDataURL(f) { f.arrayBuffer().then(b => { this.result = `data:${f.type || 'application/octet-stream'};base64,` + Buffer.from(b).toString('base64'); this.onload() }, e => { this.error = e; this.onerror() }) }
}
const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]
const JPG = [0xff, 0xd8, 0xff, 0xe0, 0, 16, 0x4a, 0x46, 0x49, 0x46, 0, 1]
const file = (head, name, type, size = 2000) => { const b = new Uint8Array(size); b.set(head); return new File([b], name, { type }) }
const img = (w, h) => async src => ({ naturalWidth: w, naturalHeight: h, src })
const noImg = async () => { throw new Error('디코드 실패') }

test('앞 바이트 판정', () => {
  assert.equal(sniffBytes(Uint8Array.from(PNG)), 'image/png')
  assert.equal(sniffBytes(Uint8Array.from(JPG)), 'image/jpeg')
  assert.equal(sniffBytes(Uint8Array.from([0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 0x68, 0x65, 0x69, 0x63])), 'image/heic')
})

test('type 빈 PNG(안드로이드 고르기)도 받는다', async () => {
  const f = file(PNG, 'Screenshot.png', '')
  assert.equal(await kindOf(f), 'image/png')
  const r = await prepImage(f, { loadImg: img(800, 600) })
  assert.ok(r.ok, r.why); assert.equal(r.type, 'image/png')
  assert.equal(Buffer.from(r.data, 'base64')[0], 0x89)
})

test('이름도 type 도 없는 붙여넣기 JPEG', async () => {
  const r = await prepImage(file(JPG, '', ''), { loadImg: img(100, 100) })
  assert.ok(r.ok); assert.equal(r.type, 'image/jpeg')
})

test('디코드가 막혀도(CSP 등) 원본으로 보낸다', async () => {
  const r = await prepImage(file(JPG, 'a.jpg', 'image/jpeg'), { loadImg: noImg })
  assert.ok(r.ok); assert.match(r.note, /원본/)
})

test('큰 PNG 는 JPEG 로 줄이고, 줄이기 실패면 8MB 안에서 원본', async () => {
  const big = file(PNG, 'big.png', 'image/png', BIG + 10)
  const r = await prepImage(big, { loadImg: img(1440, 3200), toJpeg: () => 'QUJD' })
  assert.ok(r.ok); assert.equal(r.type, 'image/jpeg'); assert.equal(r.name, 'big.jpg')
  const r2 = await prepImage(big, { loadImg: img(1440, 3200), toJpeg: () => { throw new Error('캔버스를 못 만듦') } })
  assert.ok(r2.ok); assert.equal(r2.type, 'image/png'); assert.match(r2.note, /캔버스/)
})

test('그림 아님·HEIC 못 엶은 까닭을 낸다', async () => {
  const t = await prepImage(file([0x25, 0x50, 0x44, 0x46], 'doc.pdf', 'application/pdf'))
  assert.equal(t.ok, false); assert.match(t.why, /그림 파일이 아니라/)
  const h = await prepImage(file([0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 0x68, 0x65, 0x69, 0x63], 'p.heic', ''), { loadImg: noImg })
  assert.equal(h.ok, false); assert.match(h.why, /HEIC/)
})

test('붙여넣기: files 가 비면 items 에서', () => {
  const f = file(PNG, 'x.png', 'image/png')
  assert.deepEqual(pastedFiles({ files: [], items: [{ kind: 'string' }, { kind: 'file', getAsFile: () => f }] }), [f])
  assert.deepEqual(pastedFiles({ files: [f], items: [] }), [f])
})
