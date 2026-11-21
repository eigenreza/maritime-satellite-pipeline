/** Small chart helpers shared by the result pages. */

type TickProps = { x?: number; y?: number; payload?: { value: string } }

/** X-axis label on two lines: the class name, then a muted second line (for example the number of cases). */
export function ClassTick({ x = 0, y = 0, payload, sub }: TickProps & { sub: (value: string) => string }) {
  const v = payload?.value ?? ''
  return (
    <g transform={`translate(${x},${y})`}>
      <text dy={12} textAnchor="middle" fontSize={11} fill="#17212b">{v}</text>
      <text dy={26} textAnchor="middle" fontSize={10} fill="#5a6875">{sub(v)}</text>
    </g>
  )
}
