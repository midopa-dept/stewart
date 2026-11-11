// diff 줄 번호(판 55): 서버가 준 unified diff(@@ -a,b +c,d @@ 머리)에서 줄마다 옛·새 번호를 센다.
// 꼴(옛 번호·표지·새 번호·본문 4칸 격자, 묶음 여럿이면 "묶음 i/n")은 llama.cpp tools/ui 의
// ChatMessageToolCallBlockEditFile.svelte 에서 빌려 왔다(MIT, The ggml authors — ui/THIRD_PARTY.md).
// 그쪽은 앞뒤 글로 LCS 를 돌리지만 우리는 서버가 이미 diff 를 주므로 머리에서 번호만 센다.

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/

// 돌려줌: [{kind: 'hunk'|'add'|'del'|'ctx'|'note', old, new, text, n, of}]
export function diffRows(text) {
  const lines = String(text || '').split('\n').filter(l => !l.startsWith('+++') && !l.startsWith('---'))
  const of = lines.filter(l => HUNK.test(l)).length
  const rows = []
  let o = null, n = null, k = 0
  for (const l of lines) {
    const m = HUNK.exec(l)
    if (m) {
      o = +m[1]; n = +m[2]; k += 1
      rows.push({ kind: 'hunk', text: m[3].trim(), n: k, of })
    } else if (o == null) {
      if (l !== '') rows.push({ kind: 'note', text: l })
    } else if (l.startsWith('+')) {
      rows.push({ kind: 'add', new: n++, text: l.slice(1) })
    } else if (l.startsWith('-')) {
      rows.push({ kind: 'del', old: o++, text: l.slice(1) })
    } else if (l.startsWith(' ')) {
      rows.push({ kind: 'ctx', old: o++, new: n++, text: l.slice(1) })
    } else if (l.startsWith('\\')) {
      rows.push({ kind: 'note', text: l })          // "\ No newline at end of file"
    } else if (l !== '') {
      rows.push({ kind: 'note', text: l })          // 서버가 붙인 "…(N줄 더)" 같은 줄
    }
  }
  return rows
}
