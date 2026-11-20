/** Illustrative slides for the welcome page (fictional companies, no client material):
 *  a compliant-but-dense slide, and the same content after a Redline redesign. */

const PEERS: [string, number][] = [
  ['Alder Medical', 15.2], ['Northwind', 11.4], ['Birchway', 9.1], ['Cedar Bio', 7.8], ['Dunmore', 6.9],
  ['Elmstead', 5.6], ['Fairhaven', 4.9], ['Glenrock', 4.6], ['Hollis', 4.2], ['Ivywood', 3.9],
  ['Juniper Labs', 3.4], ['Kestrel', 2.8],
]
const MEDIAN = 4.9
const pt = (p: number) => `${(p / 7.2).toFixed(3)}cqw` // a 720 pt-wide slide

function Footer() {
  return (
    <>
      <div className="ss-conf" />
      <div className="ss-num" style={{ fontSize: pt(8) }}>4</div>
    </>
  )
}

export function SampleBefore() {
  return (
    <div className="ss" aria-hidden="true">
      <div className="ss-title" style={{ fontSize: pt(22), top: '5%' }}>
        We refresh Northwind’s pay comparison group every year so the companies in it stay similar in size and business. This year we kept the existing list and checked it with AI
      </div>
      <div className="ss-caption" style={{ fontSize: pt(13), left: '4%', top: '30%', width: '56%' }}>Northwind Pay Comparison Group</div>
      <table className="ss-table" style={{ fontSize: pt(9.5), left: '4%', top: '35%', width: '56%' }}>
        <thead><tr><th>Company</th><th>Revenue</th><th>Market cap</th><th>Cap to revenue</th></tr></thead>
        <tbody>
          {PEERS.map(([n, v], i) => (
            <tr key={n} className={i % 2 ? 'odd' : ''}><td>{n}</td><td>${(4200 + i * 310).toLocaleString()}</td><td>${Math.round((4200 + i * 310) * v).toLocaleString()}</td><td>{v.toFixed(1)}x</td></tr>
          ))}
        </tbody>
      </table>
      <div className="ss-side" style={{ fontSize: pt(12), left: '63%', top: '30%', width: '33%' }}>
        <div className="ss-box-head ss-off-palette" style={{ fontSize: pt(12) }}>Questions for This Review</div>
        <p>Northwind has grown quickly, earns more of its revenue from subscriptions and is entering new markets. Do the rules we use to pick comparison companies still fit a business like that?</p>
        <p>Once the rules are updated, which of today’s comparison companies still qualify, and which new companies should we add to the list?</p>
      </div>
      <Footer />
    </div>
  )
}

function Chart() {
  const x0 = 92, x1 = 360, top = 6, row = 21
  const scale = (x1 - x0) / 16
  const bottom = top + row * PEERS.length
  return (
    <svg viewBox="0 0 400 290" className="ss-chart" role="presentation">
      <rect x={x0 + 3.4 * scale} y={top - 3} width={(6.9 - 3.4) * scale} height={bottom - top + 3} fill="#F2F2F2" />
      <line x1={x0 + MEDIAN * scale} x2={x0 + MEDIAN * scale} y1={top - 3} y2={bottom} stroke="#404040" strokeWidth="1" strokeDasharray="3 2" />
      {PEERS.map(([n, v], i) => {
        const y = top + i * row
        const hi = n === 'Northwind'
        return (
          <g key={n}>
            <text x={x0 - 6} y={y + 13} textAnchor="end" fontSize="10" fill={hi ? '#000' : '#404040'} fontWeight={hi ? 700 : 400}>{n}</text>
            <rect x={x0} y={y + 4} width={v * scale} height="13" fill={hi ? '#006EBE' : '#BFBFBF'} />
            <text x={x0 + v * scale + 4} y={y + 14} fontSize="9.5" fill={hi ? '#006EBE' : '#595959'} fontWeight={hi ? 700 : 400}>{v.toFixed(1)}x</text>
            {hi && <rect x={2} y={y + 1} width={x0 + v * scale + 34} height="19" fill="none" stroke="#E80000" strokeWidth="1.6" />}
          </g>
        )
      })}
      <text x={x0 + MEDIAN * scale} y={bottom + 14} textAnchor="middle" fontSize="9" fill="#404040">Median 4.9x</text>
      <text x={398} y={bottom + 14} textAnchor="end" fontSize="9" fill="#808080">Shaded: 25th–75th %ile</text>
    </svg>
  )
}

export function SampleAfter() {
  const bullet = (t: string) => (
    <div className="ss-bullet"><span className="ss-square" style={{ width: pt(5), height: pt(5), marginTop: pt(6) }} />{t}</div>
  )
  return (
    <div className="ss" aria-hidden="true">
      <div className="ss-title" style={{ fontSize: pt(24), top: '6%' }}>Northwind is valued at 11.4x revenue, more than twice the peer median of 4.9x</div>
      <div className="ss-caption" style={{ fontSize: pt(14), left: '4%', top: '25%', width: '56%', textAlign: 'center' }}>Market cap to revenue, current peer group</div>
      <div style={{ position: 'absolute', left: '4%', top: '31%', width: '56%', height: '55%' }}><Chart /></div>
      <div className="ss-source" style={{ fontSize: pt(9), left: '4%', top: '88%' }}>Source: current peer group table; full table in the appendix</div>
      <div className="ss-side" style={{ fontSize: pt(15), left: '63%', top: '25%', width: '33%' }}>
        <div className="ss-box-head" style={{ fontSize: pt(13) }}>Questions for this review</div>
        {bullet('Do our selection rules still fit Northwind?')}
        {bullet('Which current companies still qualify?')}
        {bullet('Which new companies should we add?')}
      </div>
      <Footer />
    </div>
  )
}
