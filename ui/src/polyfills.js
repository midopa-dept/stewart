// 조금 옛 브라우저용 작은 메우기(Object.hasOwn·at·findLast). 새 브라우저에서는 아무것도 하지 않는다.
if (!Object.hasOwn) Object.hasOwn = (o, k) => Object.prototype.hasOwnProperty.call(o, k)
for (const C of [Array, String]) {
  if (!C.prototype.at) {
    Object.defineProperty(C.prototype, 'at', {
      value(i) { i = Math.trunc(i) || 0; if (i < 0) i += this.length; return i < 0 || i >= this.length ? undefined : this[i] },
      writable: true, configurable: true,
    })
  }
}
if (!Array.prototype.findLast) {
  Object.defineProperty(Array.prototype, 'findLast', {
    value(f, t) { for (let i = this.length - 1; i >= 0; i--) if (f.call(t, this[i], i, this)) return this[i] },
    writable: true, configurable: true,
  })
}
