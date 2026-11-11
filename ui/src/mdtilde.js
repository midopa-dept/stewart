// 판 84: GFM 은 물결표 한 개 쌍도 취소선으로 그린다 — 한국어 범위("3~5개, 10~20분")에 줄이 그어진다.
// marked 의 del 토큰 고르개를 바꿔 두 개(~~…~~)만 취소선으로, 한 개짜리 물결표는 글자 그대로 둔다.
export const tildeTokenizer = {
  del(src) {
    const m = /^~~(?=[^\s~])([\s\S]*?[^\s~])~~(?!~)/.exec(src)
    if (m) return { type: 'del', raw: m[0], text: m[1], tokens: this.lexer.inlineTokens(m[1]) }
    if (src[0] === '~') { const t = /^~+/.exec(src)[0]; return { type: 'text', raw: t, text: t } }
    return false
  },
}
