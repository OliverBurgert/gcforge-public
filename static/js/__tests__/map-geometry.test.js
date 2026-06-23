/**
 * Tests for the pure geodesic + geometry helpers in map-draw.js.
 *
 * These mirror the Python implementations in geocaches/geo/__init__.py
 * (haversine, point_in_polygon, dist_to_segment_km). The JS and Python
 * are independent re-implementations of the same algorithms, so these
 * tests double as a cross-language parity check: where a Python value is
 * quoted in a comment it was derived from geocaches/geo/__init__.py and
 * the JS result is asserted to agree within a small tolerance.
 *
 * Loads map-draw.js with the indirect-eval pattern from smoke.test.js so
 * its top-level function declarations land on globalThis. map-draw.js
 * contains only `function`/`var` declarations at top level (no executable
 * statements that touch MapLibre), so it loads cleanly under jsdom.
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

beforeAll(() => {
  loadScript('map-draw.js')
})

// ── _gcfHaversineM — parity with geo.haversine_km (× 1000) ───────────────────
// Python uses R=6371 km + asin; JS uses R=6371000 m + atan2(√a,√(1-a)).
// These are the same formula, so JS metres / 1000 ≈ Python km.

describe('_gcfHaversineM', () => {
  it('returns 0 for identical points', () => {
    expect(globalThis._gcfHaversineM(48.0, 9.0, 48.0, 9.0)).toBe(0)
  })

  it('one degree of latitude ≈ 111.19 km', () => {
    // haversine_km(0,0, 1,0) = 111.194926... km in geo/__init__.py
    const m = globalThis._gcfHaversineM(0, 0, 1, 0)
    expect(m / 1000).toBeCloseTo(111.194926, 3)
  })

  it('Tübingen → Stuttgart ≈ 29.842 km (Python parity)', () => {
    // geo.haversine_km(48.52,9.06, 48.7758,9.1829) = 29.842265906870356 km
    // (verified against geocaches/geo/__init__.py — agrees to 11 sig figs).
    const m = globalThis._gcfHaversineM(48.52, 9.06, 48.7758, 9.1829)
    expect(m / 1000).toBeCloseTo(29.842266, 4)
  })

  it('is symmetric', () => {
    const a = globalThis._gcfHaversineM(40.0, -74.0, 51.5, -0.12)
    const b = globalThis._gcfHaversineM(51.5, -0.12, 40.0, -74.0)
    expect(a).toBeCloseTo(b, 6)
  })
})

// ── _gcfPointInPolygon — parity with geo.point_in_polygon ────────────────────
// ring is a closed [[lng,lat],...] ring (GeoJSON order).

describe('_gcfPointInPolygon', () => {
  // 0,0 → 0,10 → 10,10 → 10,0 square, given as [lng,lat] closed ring.
  const square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]

  it('point clearly inside the square', () => {
    expect(globalThis._gcfPointInPolygon(5, 5, square)).toBe(true)
  })

  it('point clearly outside the square', () => {
    expect(globalThis._gcfPointInPolygon(5, 20, square)).toBe(false)
    expect(globalThis._gcfPointInPolygon(20, 5, square)).toBe(false)
    expect(globalThis._gcfPointInPolygon(-5, 5, square)).toBe(false)
  })

  it('handles a concave (L-shaped) polygon', () => {
    // L-shape: occupies the lower band and the left column.
    const lshape = [[0, 0], [10, 0], [10, 3], [3, 3], [3, 10], [0, 10], [0, 0]]
    expect(globalThis._gcfPointInPolygon(1, 1, lshape)).toBe(true)   // in the foot
    expect(globalThis._gcfPointInPolygon(1, 8, lshape)).toBe(true)   // in the upright
    expect(globalThis._gcfPointInPolygon(8, 8, lshape)).toBe(false)  // in the notch
  })

  it('argument order is (lat, lon, ring) — lat/lon must not be swapped', () => {
    // Narrow box around lat 48, lon 9 (Germany). [lng,lat] ring.
    const box = [[8.9, 47.9], [9.1, 47.9], [9.1, 48.1], [8.9, 48.1], [8.9, 47.9]]
    expect(globalThis._gcfPointInPolygon(48.0, 9.0, box)).toBe(true)
    // Swapped (9.0, 48.0) lands far outside — guards against arg-order regressions.
    expect(globalThis._gcfPointInPolygon(9.0, 48.0, box)).toBe(false)
  })
})

// ── _gcfDistToSegmentM — parity with geo.dist_to_segment_km (× 1000) ──────────

describe('_gcfDistToSegmentM', () => {
  it('point on the segment has ~0 distance', () => {
    // Segment from (0,0) to (0,10) in lat; point at the midpoint (5,0).
    const m = globalThis._gcfDistToSegmentM(5, 0, 0, 0, 10, 0)
    expect(m).toBeCloseTo(0, 3)
  })

  it('degenerate segment falls back to point distance', () => {
    // len2 === 0 branch: both endpoints identical → haversine to that point.
    const seg = globalThis._gcfDistToSegmentM(1, 1, 0, 0, 0, 0)
    const direct = globalThis._gcfHaversineM(1, 1, 0, 0)
    expect(seg).toBeCloseTo(direct, 6)
  })

  it('clamps t to the near endpoint when the foot is past the segment', () => {
    // Point well beyond the (0,0)-(0,10) lat segment in the +lat direction.
    const m = globalThis._gcfDistToSegmentM(20, 0, 0, 0, 10, 0)
    const toEnd = globalThis._gcfHaversineM(20, 0, 10, 0)
    expect(m).toBeCloseTo(toEnd, 3)
  })

  it('perpendicular offset distance ≈ Python dist_to_segment_km', () => {
    // Segment along lon at lat=48 from lon 9.0→9.1; point 0.01° north of lon 9.05.
    // dist_to_segment_km(48.01,9.05, 48,9.0, 48,9.1) ≈ 1.1119 km
    const m = globalThis._gcfDistToSegmentM(48.01, 9.05, 48, 9.0, 48, 9.1)
    expect(m / 1000).toBeCloseTo(1.1119, 2)
  })
})

// ── _gcfIsInAnyRegion — region-membership dispatcher ─────────────────────────
// Reads the module-global _gcfDrawRegions; we set it directly here.

describe('_gcfIsInAnyRegion', () => {
  it('returns false when no regions are drawn', () => {
    globalThis._gcfDrawRegions = []
    expect(globalThis._gcfIsInAnyRegion(48, 9)).toBe(false)
  })

  it('rect membership uses [s,w,n,e] bbox bounds inclusively', () => {
    globalThis._gcfDrawRegions = [{ type: 'rect', bbox: [47, 8, 49, 10] }]
    expect(globalThis._gcfIsInAnyRegion(48, 9)).toBe(true)   // centre
    expect(globalThis._gcfIsInAnyRegion(47, 8)).toBe(true)   // corner (inclusive)
    expect(globalThis._gcfIsInAnyRegion(50, 9)).toBe(false)  // north of bbox
  })

  it('circle membership compares haversine metres against radius_m', () => {
    // center stored as [lat, lon]; ~1.5 km radius.
    globalThis._gcfDrawRegions = [{ type: 'circle', center: [48, 9], radius_m: 1500 }]
    expect(globalThis._gcfIsInAnyRegion(48, 9)).toBe(true)
    // ~1.11 km north → inside; ~2.2 km north → outside.
    expect(globalThis._gcfIsInAnyRegion(48.01, 9)).toBe(true)
    expect(globalThis._gcfIsInAnyRegion(48.02, 9)).toBe(false)
  })

  it('al_circle regions are ignored for cache membership', () => {
    globalThis._gcfDrawRegions = [{ type: 'al_circle', center: [48, 9], radius_m: 100000 }]
    expect(globalThis._gcfIsInAnyRegion(48, 9)).toBe(false)
  })

  it('polygon membership delegates to point-in-polygon', () => {
    const ring = [[8.9, 47.9], [9.1, 47.9], [9.1, 48.1], [8.9, 48.1], [8.9, 47.9]]
    globalThis._gcfDrawRegions = [{ type: 'polygon', coordinates: ring }]
    expect(globalThis._gcfIsInAnyRegion(48, 9)).toBe(true)
    expect(globalThis._gcfIsInAnyRegion(49, 9)).toBe(false)
  })

  it('corridor membership uses width_m against per-segment distance', () => {
    // path of [lng,lat] points along lat 48; width 1500 m.
    globalThis._gcfDrawRegions = [{ type: 'corridor', path: [[9.0, 48], [9.1, 48]], width_m: 1500 }]
    expect(globalThis._gcfIsInAnyRegion(48, 9.05)).toBe(true)        // on the path
    expect(globalThis._gcfIsInAnyRegion(48.01, 9.05)).toBe(true)     // ~1.1 km off → inside
    expect(globalThis._gcfIsInAnyRegion(48.05, 9.05)).toBe(false)    // ~5.5 km off → outside
  })

  it('matches if the point falls in ANY of several regions', () => {
    globalThis._gcfDrawRegions = [
      { type: 'rect', bbox: [0, 0, 1, 1] },
      { type: 'circle', center: [48, 9], radius_m: 1000 },
    ]
    expect(globalThis._gcfIsInAnyRegion(48, 9)).toBe(true)
    expect(globalThis._gcfIsInAnyRegion(0.5, 0.5)).toBe(true)
    expect(globalThis._gcfIsInAnyRegion(30, 30)).toBe(false)
  })
})

// ── _gcfSimplifyPath — distance-based decimation ─────────────────────────────

describe('_gcfSimplifyPath', () => {
  it('returns the path unchanged when it has fewer than 2 points', () => {
    expect(globalThis._gcfSimplifyPath([[9, 48]], 1000)).toEqual([[9, 48]])
  })

  it('always keeps the first and last point', () => {
    const path = [[9.0, 48], [9.00001, 48], [9.00002, 48], [9.1, 48]]
    const out = globalThis._gcfSimplifyPath(path, 1000)
    expect(out[0]).toEqual([9.0, 48])
    expect(out[out.length - 1]).toEqual([9.1, 48])
  })

  it('drops intermediate points closer than minSpacing to the last kept point', () => {
    // Three near-identical points then one far point → only first + last survive.
    const path = [[9.0, 48], [9.00001, 48], [9.00002, 48], [9.1, 48]]
    const out = globalThis._gcfSimplifyPath(path, 1000)
    expect(out).toEqual([[9.0, 48], [9.1, 48]])
  })

  it('keeps points that are far enough apart', () => {
    // Each step ~1.1 km apart; with 500 m spacing all intermediate points stay.
    const path = [[9.0, 48], [9.0, 48.01], [9.0, 48.02], [9.0, 48.03]]
    const out = globalThis._gcfSimplifyPath(path, 500)
    expect(out).toHaveLength(4)
  })
})

// ── _gcfBestSearchForPolygon — bbox vs circumscribed circle choice ───────────

describe('_gcfBestSearchForPolygon', () => {
  it('returns a rect for an elongated polygon (bbox area < circle area)', () => {
    // Long thin horizontal strip: bbox is much tighter than the bounding circle.
    const ring = [[0, 0], [10, 0], [10, 0.1], [0, 0.1], [0, 0]]
    const res = globalThis._gcfBestSearchForPolygon(ring)
    expect(res.type).toBe('rect')
    expect(res).toHaveProperty('s')
    expect(res).toHaveProperty('e')
  })

  it('returns a circle for a diamond polygon (circle area < bbox area)', () => {
    // A diamond (rotated square) at the equator: vertices on the axes. The
    // axis-aligned bbox wastes its four corners, so the circumscribed circle
    // (π·a²) beats the bbox (4·a²). (Used at the equator because away from it
    // longitude compression already shrinks the metric bbox below the circle.)
    const ring = [[0, -0.1], [0.1, 0], [0, 0.1], [-0.1, 0], [0, -0.1]]
    const res = globalThis._gcfBestSearchForPolygon(ring)
    expect(res.type).toBe('circle')
    expect(res).toHaveProperty('lat')
    expect(res).toHaveProperty('lon')
    expect(res.radius_m).toBeGreaterThan(0)
  })

  it('circle centre is the vertex centroid', () => {
    const ring = [[0, -0.1], [0.1, 0], [0, 0.1], [-0.1, 0], [0, -0.1]]
    const res = globalThis._gcfBestSearchForPolygon(ring)
    expect(res.type).toBe('circle')
    expect(res.lat).toBeCloseTo(0.0, 6)
    expect(res.lon).toBeCloseTo(0.0, 6)
  })
})

// ── _gcfCorridorBoxes — per-segment search shape generation ──────────────────

describe('_gcfCorridorBoxes', () => {
  it('produces at least one search shape for a simple two-point path', () => {
    const boxes = globalThis._gcfCorridorBoxes([[9.0, 48], [9.05, 48]], 1000)
    expect(boxes.length).toBeGreaterThanOrEqual(1)
    boxes.forEach(b => expect(['rect', 'circle']).toContain(b.type))
  })

  it('rect shapes carry s/w/n/e and circle shapes carry lat/lon/radius_m', () => {
    const boxes = globalThis._gcfCorridorBoxes([[9.0, 48], [9.2, 48.1]], 1500)
    boxes.forEach(b => {
      if (b.type === 'rect') {
        expect(b).toMatchObject({ s: expect.any(Number), w: expect.any(Number), n: expect.any(Number), e: expect.any(Number) })
      } else {
        expect(b).toMatchObject({ lat: expect.any(Number), lon: expect.any(Number), radius_m: expect.any(Number) })
      }
    })
  })
})

// ── _gcfParseGpx — pure GPX track parser (uses jsdom DOMParser) ───────────────

describe('_gcfParseGpx', () => {
  function gpx(inner) {
    return '<?xml version="1.0"?><gpx version="1.1">' + inner + '</gpx>'
  }

  it('parses a single track segment into [lng,lat] points', () => {
    const res = globalThis._gcfParseGpx(gpx(
      '<trk><trkseg>' +
      '<trkpt lat="48.0" lon="9.0"/>' +
      '<trkpt lat="48.1" lon="9.1"/>' +
      '</trkseg></trk>'))
    expect(res.points).toEqual([[9.0, 48.0], [9.1, 48.1]])
    expect(res.segmentCount).toBe(1)
    expect(res.originalCount).toBe(2)
    expect(res.lengthKm).toBeGreaterThan(0)
  })

  it('falls back to <rte><rtept> when there are no track segments', () => {
    const res = globalThis._gcfParseGpx(gpx(
      '<rte>' +
      '<rtept lat="48.0" lon="9.0"/>' +
      '<rtept lat="48.2" lon="9.0"/>' +
      '</rte>'))
    expect(res.points).toEqual([[9.0, 48.0], [9.0, 48.2]])
    expect(res.segmentCount).toBe(1)
  })

  it('throws on GPX with fewer than 2 usable points', () => {
    expect(() => globalThis._gcfParseGpx(gpx(
      '<trk><trkseg><trkpt lat="48" lon="9"/></trkseg></trk>')))
      .toThrow(/No track data/)
  })

  it('throws on invalid XML', () => {
    expect(() => globalThis._gcfParseGpx('<gpx><unclosed>')).toThrow()
  })
})
