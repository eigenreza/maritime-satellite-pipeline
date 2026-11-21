/** Words that change with a number, and the generated sentences that use them (tested with 1 and 2). */

export const plural = (n: number, one: string, many = `${one}s`) => (n === 1 ? one : many)
export const count = (n: number, one: string, many?: string) => `${n} ${plural(n, one, many)}`
export const was = (n: number) => (n === 1 ? 'was' : 'were')

export type PassStory = { ais: number; found: number; missed: number; missedSmall: number; missedBig: number; dets: number; noAis: number; noAisRejected: number }

/** One or two sentences on what happened in one radar pass, from its own counts. */
export function passStoryText(s: PassStory): string {
  const parts: string[] = []
  if (s.ais) {
    parts.push(`The radar found ${s.found} of the ${count(s.ais, 'ship')} that reported by AIS.`)
    if (s.missed) {
      if (s.missedSmall === s.missed) {
        parts.push(s.missed === 1 ? 'The one it missed was under 60 m.' : `All ${s.missed} it missed were under 60 m.`)
      } else if (s.missedSmall) {
        parts.push(`${s.missedSmall} of the ${s.missed} it missed ${was(s.missedSmall)} under 60 m`
          + (s.missedBig ? `, ${s.missedBig} ${was(s.missedBig)} over 100 m.` : '.'))
      } else {
        parts.push(`It missed ${s.missed}` + (s.missedBig ? `, ${s.missedBig === s.missed ? (s.missed === 1 ? 'it' : 'all') : s.missedBig} over 100 m.` : '.'))
      }
    }
  }
  if (s.noAis) {
    parts.push(`${count(s.noAis, 'bright spot')} had no AIS ship nearby; the AI calls ${s.noAisRejected} of ${s.noAis === 1 ? 'it' : 'them'} ${s.noAisRejected === 1 && s.noAis === 1 ? 'a likely false alarm' : 'likely false alarms'}.`)
  }
  return parts.join(' ')
}

/** Axis label under a sea-state class on the AI page. */
export const shipsAndFalseAlarms = (ships: number, fas: number) => `${count(ships, 'ship')}, ${count(fas, 'false alarm')}`

/** The start-up sentence on the operations page. */
export const startupAlertsText = (n: number) =>
  n === 1
    ? 'The only alert came in the first hours of the replay, before the source had delivered its first data. That is the expected start-up state. '
    : `All ${n} alerts came in the first hours of the replay, before each source had delivered its first data. That is the expected start-up state. `

/** Label under a chart class: how many cases, or that there are too few. */
export const casesLabel = (n: number, unit: string, enough: boolean, model?: string | null) =>
  enough ? count(n, unit.replace(/s$/, ''), unit) : `${n}: too few${model ? `; model ${model}` : ''}`

/** A 95% range in tenths, rounded outward so it always contains the interval: 35% to 48% is "between 3 and 5 in 10". */
export const tenRange = (lo: number, hi: number) =>
  `between ${Math.max(0, Math.floor(10 * lo + 1e-9))} and ${Math.min(10, Math.ceil(10 * hi - 1e-9))} in 10`
