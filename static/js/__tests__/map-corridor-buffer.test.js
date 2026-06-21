/**
 * Tests for the corridor rounded-cap buffer geometry in map-draw.js.
 *
 * These are the standalone, MapLibre-free building blocks of the visible
 * corridor highlight polygon:
 *   - _gcfPerpendicularOffset(lng,lat, dlng,dlat, dist_m)
 *       → [leftPoint, rightPoint] offset perpendicular to the travel direction.
 *   - _gcfSemicircle(lng,lat, startAngle, dist_m, steps)
 *       → steps+1 arc points sweeping 180° clockwise (the rounded end cap).
 *   - _gcfCorridorBuffer(path, width_m, steps)
 *       → a single closed ring: startCap + leftSide + endCap + reversed
 *         rightSide + closing vertex.
 *
 * All three are pure number-crunching (the local equirectangular metre
 * approximation: 111320 m/° lng·cos(lat), 110540 m/° lat). map-draw.js loads
 * cleanly under the indirect-eval harness (top-level fn/var declarations only).
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

function loadScript(filename) {
  const code = readFileSync(resolve(jsDir, filename), 'utf8')
  ;(0, eval)(code)
}

// Metre conversion constants used throughout map-draw.js.
const M_PER_DEG_LAT = 110540
const M_PER_DEG_LNG = 111320

// Approximate metric distance between two [lng,lat] points using the same
// equirectangular model the source uses, so assertions stay self-consistent.
function metresBetween(a, b, lat) {
  const cosLat = Math.cos(lat * Math.PI / 180)
  const dx = (a[0] - b[0]) * M_PER_DEG_LNG * cosLat
  const dy = (a[1] - b[1]) * M_PER_DEG_LAT
  return Math.sqrt(dx * dx + dy * dy)
}

beforeAll(() => {
  loadScript('map-draw.js')
})

// ── _gcfPerpendicularOffset ──────────────────────────────────────────────────

describe('_gcfPerpendicularOffset', () => {
  it('offsets left (north) and right (south) of an eastbound segment', () => {
    // Travelling east (dlng>0, dlat=0): left perpendicular is +lat (north),
    // right is -lat (south). Same longitude as the centre.
    const [left, right] = globalThis._gcfPerpendicularOffset(9.0, 48.0, 1.0, 0.0, 1000)
    expect(left[0]).toBeCloseTo(9.0, 9)   // lng unchanged
    expect(right[0]).toBeCloseTo(9.0, 9)
    expect(left[1]).toBeGreaterThan(48.0)  // left is north
    expect(right[1]).toBeLessThan(48.0)    // right is south
    // 1000 m north == 1000/110540 degrees of latitude.
    expect(left[1] - 48.0).toBeCloseTo(1000 / M_PER_DEG_LAT, 9)
    expect(48.0 - right[1]).toBeCloseTo(1000 / M_PER_DEG_LAT, 9)
  })

  it('places both offset points exactly dist_m from the centre', () => {
    const [left, right] = globalThis._gcfPerpendicularOffset(9.0, 48.0, 0.7, 0.7, 1500)
    expect(metresBetween(left, [9.0, 48.0], 48.0)).toBeCloseTo(1500, 1)
    expect(metresBetween(right, [9.0, 48.0], 48.0)).toBeCloseTo(1500, 1)
  })

  it('left/right are symmetric about the centre point', () => {
    const c = [9.0, 48.0]
    const [left, right] = globalThis._gcfPerpendicularOffset(c[0], c[1], 1.0, 0.3, 800)
    expect((left[0] + right[0]) / 2).toBeCloseTo(c[0], 9)
    expect((left[1] + right[1]) / 2).toBeCloseTo(c[1], 9)
  })

  it('returns the point twice for a degenerate (zero-length) direction', () => {
    const res = globalThis._gcfPerpendicularOffset(9.0, 48.0, 0, 0, 1000)
    expect(res).toEqual([[9.0, 48.0], [9.0, 48.0]])
  })
})

// ── _gcfSemicircle ───────────────────────────────────────────────────────────

describe('_gcfSemicircle', () => {
  it('returns steps+1 points', () => {
    expect(globalThis._gcfSemicircle(9.0, 48.0, 0, 1000, 8)).toHaveLength(9)
    expect(globalThis._gcfSemicircle(9.0, 48.0, 0, 1000, 16)).toHaveLength(17)
  })

  it('every arc point sits at radius dist_m from the centre', () => {
    const pts = globalThis._gcfSemicircle(9.0, 48.0, 0, 1000, 8)
    pts.forEach(p => {
      expect(metresBetween(p, [9.0, 48.0], 48.0)).toBeCloseTo(1000, 1)
    })
  })

  it('sweeps exactly 180° clockwise: last point is opposite the first', () => {
    // startAngle 0 → first point due east; after a 180° sweep the last point
    // is due west. Their midpoint is the centre.
    const pts = globalThis._gcfSemicircle(9.0, 48.0, 0, 1000, 8)
    const first = pts[0], last = pts[pts.length - 1]
    expect((first[0] + last[0]) / 2).toBeCloseTo(9.0, 6)
    expect((first[1] + last[1]) / 2).toBeCloseTo(48.0, 6)
    // First point east of centre, last point west of centre.
    expect(first[0]).toBeGreaterThan(9.0)
    expect(last[0]).toBeLessThan(9.0)
  })

  it('sweep is clockwise: from startAngle 0 (east) the arc dips south first', () => {
    const pts = globalThis._gcfSemicircle(9.0, 48.0, 0, 1000, 8)
    // a = startAngle - π·i/steps → after one step the angle is negative,
    // so sin(a) < 0 → the second point is south of the centre.
    expect(pts[1][1]).toBeLessThan(48.0)
  })
})

// ── _gcfCorridorBuffer ───────────────────────────────────────────────────────

describe('_gcfCorridorBuffer', () => {
  it('returns null for a path with fewer than 2 points', () => {
    expect(globalThis._gcfCorridorBuffer([[9, 48]], 1000)).toBeNull()
    expect(globalThis._gcfCorridorBuffer([], 1000)).toBeNull()
  })

  it('returns a closed ring (first vertex repeated at the end)', () => {
    const ring = globalThis._gcfCorridorBuffer([[9.0, 48.0], [9.1, 48.0]], 1000)
    expect(ring[0]).toEqual(ring[ring.length - 1])
  })

  it('ring length = 2 caps (steps+1) + 2·n side points + 1 closing vertex', () => {
    // Default steps = 16 → each cap is 17 points. n = 2 path points → 2 left
    // + 2 right side points. Plus the closing vertex.
    const ring = globalThis._gcfCorridorBuffer([[9.0, 48.0], [9.1, 48.0]], 1000)
    const steps = 16
    const n = 2
    expect(ring).toHaveLength(2 * (steps + 1) + 2 * n + 1)
  })

  it('honours an explicit steps argument', () => {
    const ring = globalThis._gcfCorridorBuffer([[9.0, 48.0], [9.1, 48.0]], 1000, 4)
    const steps = 4
    const n = 2
    expect(ring).toHaveLength(2 * (steps + 1) + 2 * n + 1)
  })

  it('every ring vertex is within ~width_m of the path (with rounded-cap slack)', () => {
    // For a straight eastbound segment, the buffer is a stadium of half-width
    // 1000 m. No vertex should be much farther than width_m from the nearer
    // endpoint or the segment line.
    const path = [[9.0, 48.0], [9.1, 48.0]]
    const ring = globalThis._gcfCorridorBuffer(path, 1000)
    ring.forEach(p => {
      // Distance to the segment (lat 48 line between lng 9.0 and 9.1):
      // clamp lng to the segment span, then metric distance.
      const lng = Math.min(Math.max(p[0], 9.0), 9.1)
      const d = metresBetween(p, [lng, 48.0], 48.0)
      expect(d).toBeLessThanOrEqual(1000 + 1) // 1 m tolerance for float noise
    })
  })

  it('produces a wider ring for a larger half-width', () => {
    const narrow = globalThis._gcfCorridorBuffer([[9.0, 48.0], [9.1, 48.0]], 500)
    const wide = globalThis._gcfCorridorBuffer([[9.0, 48.0], [9.1, 48.0]], 2000)
    // Max north extent grows with width_m.
    const maxLat = r => Math.max(...r.map(p => p[1]))
    expect(maxLat(wide)).toBeGreaterThan(maxLat(narrow))
  })
})
