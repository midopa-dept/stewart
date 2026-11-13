// 판 124.3: 접힌 작업 카드 아래 그림 — 마지막 걸음의 마지막 그림, 걸음이 이어지면 최신으로
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { lastShot } from '../src/stepshot.js'

test('마지막 그림', () => {
  assert.equal(lastShot([]), null)
  assert.equal(lastShot([{ text: 'a' }]), null)
  assert.deepEqual(lastShot([{ imgs: ['a.png'] }, { text: 'x' }]), ['a.png'])
  assert.deepEqual(lastShot([{ imgs: ['a.png'] }, { imgs: ['b.png', 'c.png'] }]), ['c.png'])
})
