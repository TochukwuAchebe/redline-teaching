const base = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true }

export const Arrow = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" strokeWidth="2" {...base}><path d="M5 12h14M13 6l6 6-6 6" /></svg>
)
export const ChevronLeft = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" strokeWidth="2" {...base}><path d="M15 18l-6-6 6-6" /></svg>
)
export const ChevronRight = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" strokeWidth="2" {...base}><path d="M9 18l6-6-6-6" /></svg>
)
export const ChevronDown = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" strokeWidth="2.2" {...base}><path d="M6 9l6 6 6-6" /></svg>
)
export const UploadIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" strokeWidth="1.8" {...base}><path d="M12 16V4M6.5 9.5L12 4l5.5 5.5" /><path d="M4 15v3a2 2 0 002 2h12a2 2 0 002-2v-3" /></svg>
)
export const DownloadIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" strokeWidth="2" {...base}><path d="M12 4v12M6.5 10.5L12 16l5.5-5.5" /><path d="M4 20h16" /></svg>
)
export const Spark = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M12 2l2.2 6.3 6.3 2.2-6.3 2.2L12 19l-2.2-6.3-6.3-2.2 6.3-2.2z" />
  </svg>
)
export const Undo = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" strokeWidth="2" {...base}><path d="M9 14L4 9l5-5" /><path d="M4 9h11a5 5 0 010 10h-3" /></svg>
)
