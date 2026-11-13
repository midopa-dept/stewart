// 판 124: 선 아이콘 한 벌(1.5px, currentColor) — 그림 글자 대신
const P = { fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', viewBox: '0 0 24 24', 'aria-hidden': 'true' }

export const IconMenu = () => <svg {...P}><path d="M4 7h16M4 12h16M4 17h16" /></svg>
export const IconNew = () => <svg {...P}><path d="M12 20h8" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
export const IconPlus = () => <svg {...P}><path d="M12 5v14M5 12h14" /></svg>
export const IconSend = () => <svg {...P} stroke-width="2"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" /></svg>
export const IconStop = () => <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" /></svg>
export const IconUp = () => <svg {...P}><path d="M7 10v10M7 10l4-7a2 2 0 0 1 2 2v4h5a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 16.8 20H7" /><path d="M3 10h4v10H3z" /></svg>
export const IconDown = () => <svg {...P}><path d="M17 14V4M17 14l-4 7a2 2 0 0 1-2-2v-4H6a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 7.2 4H17" /><path d="M21 14h-4V4h4z" /></svg>
export const IconChev = () => <svg {...P}><path d="m9 6 6 6-6 6" /></svg>
export const IconDown2 = () => <svg {...P}><path d="m6 9 6 6 6-6" /></svg>
export const IconMore = () => <svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" /></svg>
export const IconFile = () => <svg {...P}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" /><path d="M14 3v5h5" /></svg>
export const IconImage = () => <svg {...P}><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="m21 16-5-5-9 9" /></svg>
export const IconWarn = () => <svg {...P}><path d="M12 4 2.8 19.5h18.4Z" /><path d="M12 10v4M12 17h.01" /></svg>
export const IconPanel = () => <svg {...P}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M15 4v16" /></svg>
export const IconUpS = () => <svg {...P}><path d="m6 15 6-6 6 6" /></svg>
export const IconSliders = () => <svg {...P}><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></svg>
export const IconX = () => <svg {...P}><path d="M6 6l12 12M18 6 6 18" /></svg>
