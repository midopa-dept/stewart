// 판 134: 빈 대화 = 사람·모델 말이 아직 없음(기록 줄 kind 또는 묶음 type 이 user·assistant 인 것이 없다).
// 시작 안내 줄(기억 파일·스킬·경고)만 있는 대화도 빈 대화다 — 첫 화면(자리·등급 고르기)을 그리고 안내 줄은 그 아래에 둔다.
export const talked = items => (items || []).some(x => ['user', 'assistant'].includes(x?.type || x?.kind))
