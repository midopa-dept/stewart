// 묶음 제목 요약: "명령 3개 실행 · 파일 2개 읽음 · 파일 1개 고침"(꼴은 OpenHands event-group.tsx, MIT — ui/THIRD_PARTY.md)
const VERB = [[/^(workers|delegate)$/, '작업자 일', ''], [/^run_shell$/, '명령', '실행'], [/^(read_file|view_image)$/, '파일', '읽음'], [/^(write_file|edit_file|multi_edit)$/, '파일', '고침'],
  [/^(list_dir|glob|search|folder_scan|scan_read|scan_stats|peek)$/, '찾아보기', ''], [/^session_/, '작업자 일', ''], [/^memory_|^note_add$/, '기억', ''], [/^web_search$/, '검색', '번'], [/^web_fetch$/, '웹 글', '읽음'], [/^web_/, '웹', ''], [/^board$/, '현황판', ''], [/^forge_/, '저장소', '']]
export function stepSummary(steps) {
  const n = new Map()
  for (const s of steps) {
    if (s.kind === 'reflect') continue
    // 판 139: 작업자 도구 줄("[작업자 w1] read_file …")은 머리를 떼고 그 도구로 센다 — 전에는 "[작업자 2개" 로 괄호가 잘렸다
    const t = String(s.text || '').replace(/^\[작업자 [^\]]*\]\s*/, '')
    const name = t.split(' ')[0]
    const v = VERB.find(([re]) => re.test(name))
    const k = v ? v[1] + '\0' + v[2] : name + '\0'
    n.set(k, (n.get(k) || 0) + 1)
  }
  return [...n].map(([k, c]) => { const [w, verb] = k.split('\0'); return `${w} ${c}개${verb ? ' ' + verb : ''}` }).join(' · ')
}
