/**
 * Tests for the route planner's pure helpers in map-route.js:
 *   - waypoint list mutation (add / remove / move)
 *   - _gcfRouteLonLats() ordering ([lon,lat] pairs)
 *   - gcfRouteOpenBrouterWeb() deep-link construction (lonlats/pois/profile,
 *     literal ';' and ',' separators, POI cap)
 *
 * map-route.js declares only top-level functions/vars, so it loads cleanly
 * under the indirect-eval harness. The DOM-touching paths short-circuit when
 * the elements / gcfMap / maplibregl are absent, so these run headless.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

function loadScript(filename) {
  const code = readFileSync(resolve(jsDir, filename), 'utf8')
  ;(0, eval)(code)
}

beforeEach(() => {
  // Globals map-route.js reads. gcfMap is referenced directly (not typeof-
  // guarded), so it must at least be declared.
  globalThis.gcfMap = null
  globalThis._gcfMarkersData = null
  globalThis.maplibregl = undefined
  loadScript('map-route.js')
  globalThis._gcfRouteWaypoints = []
})

describe('waypoint list mutation', () => {
  it('adds waypoints in order', () => {
    globalThis.gcfRouteAddWaypoint(48.0, 9.0, 'A')
    globalThis.gcfRouteAddWaypoint(48.5, 9.5, 'B', 'cache', 'GC123')
    expect(globalThis._gcfRouteWaypoints).toHaveLength(2)
    expect(globalThis._gcfRouteWaypoints[1]).toMatchObject({
      lat: 48.5, lon: 9.5, label: 'B', kind: 'cache', code: 'GC123',
    })
  })

  it('defaults the label to the coordinates when none is given', () => {
    globalThis.gcfRouteAddWaypoint(48.123456, 9.654321)
    expect(globalThis._gcfRouteWaypoints[0].label).toBe('48.12346, 9.65432')
  })

  it('removes by index', () => {
    globalThis.gcfRouteAddWaypoint(1, 1, 'A')
    globalThis.gcfRouteAddWaypoint(2, 2, 'B')
    globalThis.gcfRouteRemoveWaypoint(0)
    expect(globalThis._gcfRouteWaypoints).toHaveLength(1)
    expect(globalThis._gcfRouteWaypoints[0].label).toBe('B')
  })

  it('moves a waypoint up/down and clamps at the ends', () => {
    globalThis.gcfRouteAddWaypoint(1, 1, 'A')
    globalThis.gcfRouteAddWaypoint(2, 2, 'B')
    globalThis.gcfRouteAddWaypoint(3, 3, 'C')
    globalThis.gcfRouteMoveWaypoint(2, -1)   // C up → A, C, B
    expect(globalThis._gcfRouteWaypoints.map(w => w.label)).toEqual(['A', 'C', 'B'])
    globalThis.gcfRouteMoveWaypoint(0, -1)   // no-op at top
    expect(globalThis._gcfRouteWaypoints.map(w => w.label)).toEqual(['A', 'C', 'B'])
  })
})

describe('_gcfRouteDefaultWidthKm', () => {
  it('maps each travel mode to its default corridor width', () => {
    expect(globalThis._gcfRouteDefaultWidthKm('hiking-beta')).toBe(0.2)
    expect(globalThis._gcfRouteDefaultWidthKm('trekking')).toBe(0.5)
    expect(globalThis._gcfRouteDefaultWidthKm('fastbike')).toBe(0.5)
    expect(globalThis._gcfRouteDefaultWidthKm('shortest')).toBe(0.5)
    expect(globalThis._gcfRouteDefaultWidthKm('car-fast')).toBe(1.0)
  })
})

describe('_gcfRouteLonLats', () => {
  it('returns [lon,lat] pairs in waypoint order', () => {
    globalThis.gcfRouteAddWaypoint(48.0, 9.0, 'A')
    globalThis.gcfRouteAddWaypoint(48.5, 9.5, 'B')
    expect(globalThis._gcfRouteLonLats()).toEqual([[9.0, 48.0], [9.5, 48.5]])
  })
})

describe('gcfRouteLoadSaved', () => {
  it('restores waypoints and the current name from a saved route', () => {
    globalThis._gcfSavedRoutes = [{
      id: 5, name: 'Day trip', profile: 'trekking', width_m: 500,
      path: [[9, 48], [9.5, 48.5]],
      waypoints: [
        { lat: 48.0, lon: 9.0, label: 'Home', kind: 'location', code: null },
        { lat: 48.5, lon: 9.5, label: 'GC123', kind: 'cache', code: 'GC123' },
      ],
    }]
    globalThis.gcfRouteLoadSaved({ value: '5' })
    expect(globalThis._gcfRouteWaypoints).toHaveLength(2)
    expect(globalThis._gcfRouteWaypoints[1].code).toBe('GC123')
    expect(globalThis._gcfRouteCurrentName).toBe('Day trip')
  })

  it('ignores an unknown selection', () => {
    globalThis._gcfSavedRoutes = []
    globalThis._gcfRouteWaypoints = [{ lat: 1, lon: 1, label: 'x' }]
    globalThis.gcfRouteLoadSaved({ value: '99' })
    expect(globalThis._gcfRouteWaypoints).toHaveLength(1)  // unchanged
  })
})

describe('itinerary (_gcfRouteProject / _gcfRouteBuildItinerary)', () => {
  // Eastbound segment along lat 48: 0.1° lon ≈ 7448 m.
  const PATH = [[9.0, 48.0], [9.1, 48.0]]

  function setupRoute(widthM) {
    globalThis._gcfRouteRegion = { path: PATH, width_m: widthM }
    globalThis._gcfRouteWaypoints = [
      { lat: 48.0, lon: 9.0, label: 'Start', code: null },
      { lat: 48.0, lon: 9.1, label: 'End', code: null },
    ]
  }

  it('projects a point onto the route (along + perpendicular offset)', () => {
    const cum = globalThis._gcfRouteCumulative(PATH)
    const total = cum[cum.length - 1]
    const mid = globalThis._gcfRouteProject(48.0, 9.05, PATH, cum)
    expect(mid.along_m).toBeCloseTo(total / 2, 0)
    expect(mid.offset_m).toBeCloseTo(0, 5)
    // 0.001° lat north ≈ 110.54 m off the line, still mid-way along.
    const off = globalThis._gcfRouteProject(48.001, 9.05, PATH, cum)
    expect(off.offset_m).toBeCloseTo(110.54, 0)
    expect(off.along_m).toBeCloseTo(total / 2, 0)
  })

  it('lists waypoints in order with distance from start', () => {
    setupRoute(1000)
    const it = globalThis._gcfRouteBuildItinerary(false)
    expect(it.rows).toHaveLength(2)
    expect(it.rows[0]).toMatchObject({ label: 'Start', waypoint: true })
    expect(it.rows[0].along_m).toBeCloseTo(0, 0)
    expect(it.rows[1].label).toBe('End')
    expect(it.rows[1].along_m).toBeCloseTo(it.total_m, 0)
  })

  it('includes only caches within the corridor width', () => {
    setupRoute(1000)
    globalThis._gcfMarkersData = [
      { c: 'GC1', n: 'Mid', la: 48.0, lo: 9.05 },     // on the line → included
      { c: 'GC2', n: 'Far', la: 48.05, lo: 9.05 },    // ~5.5 km off → excluded
    ]
    const it = globalThis._gcfRouteBuildItinerary(true)
    const codes = it.rows.map(r => r.code)
    expect(codes).toContain('GC1')
    expect(codes).not.toContain('GC2')
    // Waypoints flagged; the cache is not.
    expect(it.rows.find(r => r.code === 'GC1').waypoint).toBe(false)
    expect(it.rows.find(r => r.label === 'Start').waypoint).toBe(true)
  })

  it('does not duplicate a cache that is already a waypoint', () => {
    globalThis._gcfRouteRegion = { path: PATH, width_m: 1000 }
    globalThis._gcfRouteWaypoints = [
      { lat: 48.0, lon: 9.0, label: 'Start', code: null },
      { lat: 48.0, lon: 9.05, label: 'My cache', code: 'GC1' },
    ]
    globalThis._gcfMarkersData = [{ c: 'GC1', n: 'Mid', la: 48.0, lo: 9.05 }]
    const it = globalThis._gcfRouteBuildItinerary(true)
    expect(it.rows.filter(r => r.code === 'GC1')).toHaveLength(1)
  })

  it('reverses distances for the way home', () => {
    setupRoute(1000)
    globalThis._gcfRouteTableReversed = true
    const it = globalThis._gcfRouteBuildItinerary(false)
    // End is now first (distance 0), Start last (distance = total).
    expect(it.rows[0].label).toBe('End')
    expect(it.rows[0].along_m).toBeCloseTo(0, 0)
    expect(it.rows[1].label).toBe('Start')
    expect(it.rows[1].along_m).toBeCloseTo(it.total_m, 0)
  })
})

describe('itinerary export (_gcfRouteItineraryMatrix / CSV / TSV)', () => {
  const PATH = [[9.0, 48.0], [9.1, 48.0]]
  beforeEach(() => {
    globalThis.gettext = (s) => s   // export headers/labels use gettext
    globalThis._gcfRouteRegion = { path: PATH, width_m: 1000 }
    globalThis._gcfRouteWaypoints = [
      { lat: 48.0, lon: 9.0, label: 'Start, home', code: null },
      { lat: 48.0, lon: 9.1, label: 'End', code: null },
    ]
    globalThis._gcfRouteTableIncludeCaches = false
  })

  it('builds a header + one row per stop', () => {
    const m = globalThis._gcfRouteItineraryMatrix()
    expect(m[0]).toEqual(['#', 'Type', 'Name', 'Code', 'From start (km)', 'Off-route (m)'])
    expect(m).toHaveLength(3)
    expect(m[1][1]).toBe('Waypoint')
    expect(m[1][5]).toBe('')          // waypoints have no off-route value
  })

  it('respects the include-caches choice', () => {
    globalThis._gcfMarkersData = [{ c: 'GC1', n: 'Mid', la: 48.0, lo: 9.05 }]
    globalThis._gcfRouteTableIncludeCaches = false
    expect(globalThis._gcfRouteItineraryMatrix()).toHaveLength(3)  // 2 waypoints only
    globalThis._gcfRouteTableIncludeCaches = true
    const withCaches = globalThis._gcfRouteItineraryMatrix()
    expect(withCaches).toHaveLength(4)                              // + GC1
    expect(withCaches.some(row => row[3] === 'GC1')).toBe(true)
  })

  it('CSV-quotes fields containing commas', () => {
    const csv = globalThis._gcfRouteToCsv(globalThis._gcfRouteItineraryMatrix())
    expect(csv.split('\r\n')[0]).toBe('#,Type,Name,Code,From start (km),Off-route (m)')
    expect(csv).toContain('"Start, home"')
  })

  it('TSV is tab-separated', () => {
    const tsv = globalThis._gcfRouteToTsv(globalThis._gcfRouteItineraryMatrix())
    expect(tsv.split('\n')[0]).toBe('#\tType\tName\tCode\tFrom start (km)\tOff-route (m)')
  })
})

describe('gcfRouteOpenBrouterWeb', () => {
  let opened
  beforeEach(() => {
    opened = null
    globalThis.window.open = (url) => { opened = url }
  })

  it('builds a brouter-web deep link with ;-separated lonlats and the profile', () => {
    globalThis.gcfRouteAddWaypoint(48.0, 9.0, 'A')
    globalThis.gcfRouteAddWaypoint(48.5, 9.5, 'B')
    globalThis.gcfRouteOpenBrouterWeb()
    expect(opened).toContain('brouter.de/brouter-web/#')
    expect(opened).toContain('lonlats=9.000000,48.000000;9.500000,48.500000')
    expect(opened).toContain('profile=hiking-beta')
    expect(opened).not.toContain('pois=')   // no markers loaded
  })

  it('includes visible caches as POIs, capped at the limit', () => {
    globalThis.gcfRouteAddWaypoint(48.0, 9.0, 'A')
    const markers = []
    for (let i = 0; i < 60; i++) markers.push({ c: 'GC' + i, la: 48 + i / 1000, lo: 9 + i / 1000 })
    globalThis._gcfMarkersData = markers
    globalThis.gcfRouteOpenBrouterWeb()
    const pois = decodeURIComponent(opened.split('pois=')[1].split('&')[0])
    expect(pois.split(';')).toHaveLength(globalThis._gcfRouteMaxPois)
    expect(pois).toContain('GC0')
  })
})
