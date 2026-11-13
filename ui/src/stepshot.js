// 판 124.3: 작업 카드가 접혀 있어도 줄 아래에 보일 그림 — 도구가 본 마지막 그림(view_image 등) 하나. 없으면 null
export function lastShot(steps) {
  for (let i = (steps || []).length - 1; i >= 0; i--) if (steps[i]?.imgs?.length) return steps[i].imgs.slice(-1)
  return null
}
