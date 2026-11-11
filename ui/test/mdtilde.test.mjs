// node --test ui/test/mdtilde.test.mjs — 물결표 한 개는 취소선이 아니다(판 84)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Marked } from 'marked'
import { tildeTokenizer } from '../src/mdtilde.js'

const m = new Marked({ gfm: true, tokenizer: tildeTokenizer })

test('한국어 범위는 그대로, ~~두 개~~ 만 취소선', () => {
  assert.equal(m.parse('3~5개 정도, 10~20분 걸린다.').trim(), '<p>3~5개 정도, 10~20분 걸린다.</p>')
  assert.equal(m.parse('진짜 ~~취소 **굵게**~~ 끝').trim(), '<p>진짜 <del>취소 <strong>굵게</strong></del> 끝</p>')
  assert.equal(m.parse('홈 ~/x 와 ~~~ 와 끝~').trim(), '<p>홈 ~/x 와 ~~~ 와 끝~</p>')
})
