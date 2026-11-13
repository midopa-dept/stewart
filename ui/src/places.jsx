// 판 124: 자리 이름 — 옆 기둥 묶음 머리·머리 한 줄·첫 화면·"이 대화"의 자리 줄·새 대화 고르기가 모두 여기서 읽는다(이름을 바꿀 때 이 한 곳만)
// 기본(코드) 자리는 제품 이름 그대로, 대화 자리는 제품 이름 + 기울인 parlando(CLI 의 인자 없는 실행 · parlando 낱말과 같은 뜻)
// lead: 앞에 붙는 제품 이름 · it: 기울여 쓰는 낱말 · folder: 머리 한 줄에서 자리 이름 대신 프로젝트 폴더 이름을 보이나
export const PLACE = {
  talk: { lead: 'stewart', it: 'parlando' },
  code: { lead: 'stewart', folder: true },
}
export const place = k => PLACE[k] || PLACE.code
export const placeText = k => { const p = place(k); return [p.lead, p.it].filter(Boolean).join(' ') }
// short: 좁은 머리 한 줄에서는 기울인 낱말만(있으면)
export const PlaceName = ({ k, short }) => {
  const p = place(k)
  if (short && p.it) return <i>{p.it}</i>
  return <>{p.lead}{p.lead && p.it ? ' ' : ''}{p.it && <i>{p.it}</i>}</>
}
