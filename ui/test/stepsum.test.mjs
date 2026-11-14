// 판 139: 단계 묶음 제목 요약 — 작업자 도구 줄은 머리("[작업자 w1]")를 떼고 그 도구로 센다
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { stepSummary } from '../src/stepsum.js'

test('작업자 줄과 맡기기', () => {
  const s = stepSummary([{ text: 'workers spawn' }, { text: 'workers spawn' }, { text: 'workers wait' },
    { text: '[작업자 w1] read_file README.md' }, { text: '[작업자 w2] write_file notes.md' }])
  assert.equal(s, '작업자 일 3개 · 파일 1개 읽음 · 파일 1개 고침')
  assert.ok(!s.includes('['))
})

test('보통 줄은 그대로', () => {
  assert.equal(stepSummary([{ text: 'run_shell ls' }, { text: 'read_file a' }, { text: 'reflect', kind: 'reflect' }]), '명령 1개 실행 · 파일 1개 읽음')
})
