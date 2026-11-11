// 빌드 뒤: 화면 소스의 해시를 빌드 결과 옆(SOURCE_HASH)에 적는다. 시험(tests/test_u29.py)이 같은 셈으로 소스와 빌드가 맞는지 본다.
// 셈: ui/ 안의 index.html·vite.config.js·package.json·package-lock.json·src/** 를 상대 경로 순으로, "경로\0내용\0" 을 sha256.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const root = new URL('.', import.meta.url).pathname
const files = ['index.html', 'vite.config.js', 'package.json', 'package-lock.json']
const walk = d => readdirSync(d).sort().flatMap(n => { const p = join(d, n); return statSync(p).isDirectory() ? walk(p) : [p] })
for (const p of walk(join(root, 'src'))) files.push(relative(root, p).split(sep).join('/'))
files.sort()
const h = createHash('sha256')
for (const f of files) { h.update(f + '\0'); h.update(readFileSync(join(root, f))); h.update('\0') }
const hex = h.digest('hex') + '\n'
writeFileSync(join(root, '..', 'src', 'stewart', 'web_ui', 'SOURCE_HASH'), hex)
console.log('SOURCE_HASH', files.length, 'files')
