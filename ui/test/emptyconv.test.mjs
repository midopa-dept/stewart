// 판 134: 시작 안내 줄만 있는 대화는 빈 대화(첫 화면·고르기 줄을 그린다)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { talked } from '../src/emptyconv.js'

test('빈 대화 판정', () => {
  assert.equal(talked([]), false)
  assert.equal(talked(null), false)
  assert.equal(talked([{ kind: 'info', text: '프로젝트 기억 파일: /x/STEWART.md (707자)' }]), false)   // 사용자 기억 파일이 있을 때의 시작 줄
  assert.equal(talked([{ kind: 'warn', text: '웹 읽기: 사설 주소 허용' }, { kind: 'info', text: '스킬 3개: a, b, c' }]), false)
  assert.equal(talked([{ type: 'meta', m: {} }, { type: 'memory' }]), false)
  assert.equal(talked([{ kind: 'info', text: '안내' }, { kind: 'user', text: '안녕' }]), true)
  assert.equal(talked([{ type: 'assistant', m: { text: '답' } }]), true)
})
