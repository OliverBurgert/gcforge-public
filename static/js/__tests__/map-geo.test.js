/**
 * Tests for the ?geo= encode/decode contract in map-draw.js.
 *
 * Encoder: gcfPinAsFilter() serialises _gcfDrawRegions into the ?geo= URL
 *          param. Formats (pipe-separated):
 *            rect:s,w,n,e
 *            circle:lat,lon,radius_m
 *            polygon:lng1,lat1,lng2,lat2,...
 *            corridor:width_m:lng1,lat1,...
 * Decoder: _gcfRestoreGeoShapes() reads ?geo= back into _gcfDrawRegions
 *          (and adds shapes to the draw control).
 *
 * The Python side parses the SAME strings in geocaches/filters.py
 * (_parse_geo_param) and re-encodes them in geocaches/query.py
 * (_match_saved_area._regions_to_geo, rect+circle only). These tests lock
 * down the exact string format Python expects:
 *   - rect:   ",".join(f"{v:.6f}" for v in bbox)      → JS .toFixed(6)
 *   - circle: f"{lat:.6f},{lon:.6f},{round(radius_m)}" → JS .toFixed(6) + Math.round
 *
 * gcfPinAsFilter writes to window.location.search; _gcfRestoreGeoShapes
 * reads it and calls _gcfDrawCtrl.add(). We stub both so no MapLibre is
 * needed — the encode/decode logic itself is pure.
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

function loadScript(filename) {
  const code = readFileSync(resolve(jsDir, filename), 'utf8')
  ;(0, eval)(code)
}

// Replace window.location with a plain object whose `search` is a writable
// string (gcfPinAsFilter assigns window.location.search = ...). Returns a
// helper to read back the geo param the encoder produced.
function stubLocation(initialSearch = '') {
  let search = initialSearch
  Object.defineProperty(window, 'location', {
    value: {
      get search() { return search },
      set search(v) { search = v.startsWith('?') ? v : '?' + v },
    },
    writable: true,
    configurable: true,
  })
}

function geoParamFromLocation() {
  return new URLSearchParams(window.location.search).get('geo')
}

// Minimal stand-in for the MapboxDraw control. _gcfRestoreGeoShapes only
// calls .add(feature) and expects an array of ids back.
function stubDrawCtrl() {
  let nextId = 1
  const added = []
  globalThis._gcfDrawCtrl = {
    add(feature) {
      const id = 'feat-' + nextId++
      added.push({ id, feature })
      return [id]
    },
  }
  return added
}

beforeAll(() => {
  // _gcfRestoreGeoShapes calls _gcfUpdateCorridorBuffer/_gcfUpdateDrawStatus,
  // which read the bare global `gcfMap` (normally defined in cache-map.js).
  // null is fine — those functions early-return when gcfMap is falsy.
  globalThis.gcfMap = null
  loadScript('map-draw.js')
})

beforeEach(() => {
  document.body.innerHTML = ''
  globalThis._gcfDrawRegions = []
  globalThis._gcfCorridorWidthM = 1000
  globalThis._gcfNextCircleIsAlc = false
  globalThis._gcfDrawCtrl = null
  stubLocation('')
})

// ── Encode (gcfPinAsFilter) — exact Python-compatible format ─────────────────

describe('gcfPinAsFilter (encode)', () => {
  it('encodes a rect as rect:s,w,n,e with 6 decimal places', () => {
    globalThis._gcfDrawRegions = [{ type: 'rect', bbox: [47.5, 8.5, 48.5, 9.5], id: 1 }]
    globalThis.gcfPinAsFilter()
    expect(geoParamFromLocation()).toBe('rect:47.500000,8.500000,48.500000,9.500000')
  })

  it('encodes a circle as circle:lat,lon,radius_m (6dp coords, rounded radius)', () => {
    // center stored as [lat, lon]; radius rounded to an integer.
    globalThis._gcfDrawRegions = [{ type: 'circle', center: [48.123456, 9.654321], radius_m: 1234.7, id: 2 }]
    globalThis.gcfPinAsFilter()
    expect(geoParamFromLocation()).toBe('circle:48.123456,9.654321,1235')
  })

  it('rounds coordinates to exactly 6 decimals (matches Python f"{v:.6f}")', () => {
    globalThis._gcfDrawRegions = [{ type: 'circle', center: [48.1234567, 9.7654321], radius_m: 500, id: 3 }]
    globalThis.gcfPinAsFilter()
    // .toFixed(6) rounds half-to-even-ish like Python's format → 48.123457 / 9.765432
    expect(geoParamFromLocation()).toBe('circle:48.123457,9.765432,500')
  })

  it('encodes a polygon as polygon:lng1,lat1,... dropping the closing vertex', () => {
    const ring = [[9.0, 48.0], [9.1, 48.0], [9.1, 48.1], [9.0, 48.0]] // closed
    globalThis._gcfDrawRegions = [{ type: 'polygon', coordinates: ring, id: 4 }]
    globalThis.gcfPinAsFilter()
    // Closing vertex (== first) is dropped; 3 distinct vertices → 6 numbers.
    expect(geoParamFromLocation()).toBe(
      'polygon:9.000000,48.000000,9.100000,48.000000,9.100000,48.100000')
  })

  it('encodes a corridor as corridor:width_m:lng1,lat1,... (rounded width)', () => {
    globalThis._gcfDrawRegions = [{ type: 'corridor', path: [[9.0, 48.0], [9.2, 48.0]], width_m: 1500.6, id: 5 }]
    globalThis.gcfPinAsFilter()
    expect(geoParamFromLocation()).toBe('corridor:1501:9.000000,48.000000,9.200000,48.000000')
  })

  it('joins multiple regions with a pipe', () => {
    globalThis._gcfDrawRegions = [
      { type: 'rect', bbox: [1, 2, 3, 4], id: 1 },
      { type: 'circle', center: [48, 9], radius_m: 1000, id: 2 },
    ]
    globalThis.gcfPinAsFilter()
    expect(geoParamFromLocation()).toBe(
      'rect:1.000000,2.000000,3.000000,4.000000|circle:48.000000,9.000000,1000')
  })

  it('preserves other query params and drops ?page=', () => {
    stubLocation('?q=puzzle&page=3')
    globalThis._gcfDrawRegions = [{ type: 'rect', bbox: [1, 2, 3, 4], id: 1 }]
    globalThis.gcfPinAsFilter()
    const params = new URLSearchParams(window.location.search)
    expect(params.get('q')).toBe('puzzle')
    expect(params.get('page')).toBeNull()
    expect(params.get('geo')).toBe('rect:1.000000,2.000000,3.000000,4.000000')
  })

  it('does nothing when there are no drawn regions', () => {
    globalThis._gcfDrawRegions = []
    globalThis.gcfPinAsFilter()
    expect(geoParamFromLocation()).toBeNull()
  })
})

// ── Decode (_gcfRestoreGeoShapes) — reads ?geo= back into regions ────────────

describe('_gcfRestoreGeoShapes (decode)', () => {
  it('decodes a rect into a rect region with [s,w,n,e] bbox', () => {
    stubLocation('?geo=rect:47.500000,8.500000,48.500000,9.500000')
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    expect(globalThis._gcfDrawRegions).toHaveLength(1)
    expect(globalThis._gcfDrawRegions[0]).toMatchObject({
      type: 'rect', bbox: [47.5, 8.5, 48.5, 9.5],
    })
  })

  it('decodes a circle into center [lat,lon] + radius_m', () => {
    stubLocation('?geo=circle:48.123456,9.654321,1235')
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    expect(globalThis._gcfDrawRegions[0]).toMatchObject({
      type: 'circle', center: [48.123456, 9.654321], radius_m: 1235,
    })
  })

  it('decodes a polygon and auto-closes the ring', () => {
    stubLocation('?geo=polygon:9.000000,48.000000,9.100000,48.000000,9.100000,48.100000')
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    const r = globalThis._gcfDrawRegions[0]
    expect(r.type).toBe('polygon')
    // 3 vertices + closing copy of the first.
    expect(r.coordinates).toHaveLength(4)
    expect(r.coordinates[0]).toEqual(r.coordinates[r.coordinates.length - 1])
  })

  it('decodes a corridor into path + width_m', () => {
    stubLocation('?geo=corridor:1501:9.000000,48.000000,9.200000,48.000000')
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    expect(globalThis._gcfDrawRegions[0]).toMatchObject({
      type: 'corridor', path: [[9.0, 48.0], [9.2, 48.0]], width_m: 1501,
    })
  })

  it('decodes multiple pipe-separated regions', () => {
    stubLocation('?geo=rect:1.000000,2.000000,3.000000,4.000000|circle:48.000000,9.000000,1000')
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    expect(globalThis._gcfDrawRegions.map(r => r.type)).toEqual(['rect', 'circle'])
  })

  it('ignores malformed parts (wrong arity)', () => {
    stubLocation('?geo=rect:1,2,3') // only 3 values → not a valid rect
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    expect(globalThis._gcfDrawRegions).toHaveLength(0)
  })

  it('is a no-op when there is no geo param', () => {
    stubLocation('?q=foo')
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    expect(globalThis._gcfDrawRegions).toHaveLength(0)
  })
})

// ── Round-trip: encode → decode → identical regions ──────────────────────────

describe('geo round-trip (encode → decode)', () => {
  function roundTrip(regions) {
    globalThis._gcfDrawRegions = regions.map((r, i) => Object.assign({ id: i + 1 }, r))
    globalThis.gcfPinAsFilter()
    const encoded = window.location.search
    // Reset and decode from the encoded URL.
    globalThis._gcfDrawRegions = []
    Object.defineProperty(window, 'location', {
      value: { search: encoded }, writable: true, configurable: true,
    })
    stubDrawCtrl()
    globalThis._gcfRestoreGeoShapes()
    return globalThis._gcfDrawRegions
  }

  it('round-trips a rect unchanged (to 6dp)', () => {
    const out = roundTrip([{ type: 'rect', bbox: [47.5, 8.25, 48.5, 9.75] }])
    expect(out[0].type).toBe('rect')
    expect(out[0].bbox).toEqual([47.5, 8.25, 48.5, 9.75])
  })

  it('round-trips a circle with rounded radius', () => {
    const out = roundTrip([{ type: 'circle', center: [48.123456, 9.654321], radius_m: 1234.7 }])
    expect(out[0].type).toBe('circle')
    expect(out[0].center).toEqual([48.123456, 9.654321])
    expect(out[0].radius_m).toBe(1235) // encode rounded; decode reads the integer
  })

  it('round-trips a corridor preserving path and width', () => {
    const out = roundTrip([{ type: 'corridor', path: [[9.0, 48.0], [9.2, 48.1]], width_m: 1500 }])
    expect(out[0].type).toBe('corridor')
    expect(out[0].path).toEqual([[9.0, 48.0], [9.2, 48.1]])
    expect(out[0].width_m).toBe(1500)
  })

  it('round-trips a polygon (closing vertex dropped on encode, re-added on decode)', () => {
    const ring = [[9.0, 48.0], [9.1, 48.0], [9.1, 48.1], [9.0, 48.0]]
    const out = roundTrip([{ type: 'polygon', coordinates: ring }])
    expect(out[0].type).toBe('polygon')
    // First three vertices preserved; ring closed again.
    expect(out[0].coordinates.slice(0, 3)).toEqual([[9.0, 48.0], [9.1, 48.0], [9.1, 48.1]])
    expect(out[0].coordinates[0]).toEqual(out[0].coordinates[out[0].coordinates.length - 1])
  })
})
