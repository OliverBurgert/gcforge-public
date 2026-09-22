/**
 * Tests for the map-marker payload consumers.
 *
 * The marker endpoint (geocaches/views/map.py::map_markers) emits column
 * arrays — { fields: ['c','n','la',…], rows: [[…], …], count: N } — which
 * _gcfDecodeMarkers expands back into the compact marker objects every map
 * module reads:
 *   { c, n, la, lo, t, sz, d, tr, f, s, m,
 *     optional: dnf, gc, oc, cla, clo, aid, sn, gr, alp }
 * where:
 *   t  = TYPE_SHORT cache-type code ('T','M','U',...; '?' fallback)
 *   sz = SIZE_SHORT size code ('U' fallback)
 *   f  = found OR completed (truthy)
 *   s  = STATUS_SHORT status code ('A' fallback; 'X' archived, 'D' disabled)
 *   m  = is_mine
 *   dnf present+true only when DNFed and NOT found
 *   cla/clo = corrected coords (present only when corrected)
 *   alp = present+true only for the Adventure Lab parent; a stage carries
 *     aid but no alp (m.aid != null && !m.alp)
 *
 * cache-map.js promotes cla/clo → la/lo and stashes originals in ola/olo,
 * so downstream "is corrected" checks read m.ola != null. These tests pin:
 *   - _gcfDecodeMarkers (cache-map.js) rows + fields → marker objects
 *   - _gcfStatusKey   (map-icons.js)  status-priority resolution from f/s/m
 *   - gcfMapIconId    (map-icons.js)  image-id string format
 *   - _gcfPlatform    (map-icons.js)  gc/oc selection
 *   - gcfPrepareMapIcons (map-icons.js) per-marker dedup keying
 *   - _gcfMarkerColor / _gcfMarkerBorderColor (cache-map.js) circle colours
 *
 * map-icons.js references GCF_WP_ICON_MAP at load (it's a top-level var in
 * the same file) so it loads standalone. cache-map.js references gettext/
 * maplibregl only inside functions, so a defensive global stub is enough.
 */

import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
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
  // Defensive i18n stubs — referenced only inside functions we don't call,
  // but harmless to define.
  globalThis.gettext = (s) => s
  globalThis.interpolate = (fmt, args) => fmt
  globalThis.ngettext = (a, b, n) => (n === 1 ? a : b)
  loadScript('gcf-common.js')
  loadScript('map-icons.js')
  loadScript('cache-map.js')
})

// ── _gcfDecodeMarkers — compact rows + fields → marker objects ───────────────
// Mirrors geocaches/views/map.py MARKER_FIELDS: the first 11 columns are
// always present, the rest are optional and trailing nulls are trimmed server
// side, so a short row leaves the remaining keys unset.

const MARKER_FIELDS = [
  'c', 'n', 'la', 'lo', 't', 'sz', 'd', 'tr', 'f', 's', 'm',
  'aid', 'sn', 'gr', 'cla', 'clo', 'oc', 'gc', 'dnf', 'alp',
]

function payload(...rows) {
  return { fields: MARKER_FIELDS, rows: rows, count: rows.length }
}

describe('_gcfDecodeMarkers', () => {
  it('maps a full-length row positionally onto the field names', () => {
    const row = ['GC1', 'Name', 48.5, 9.1, 'T', 'Mi', 2, 1.5, false, 'A', false,
                 7, 3, 30, 48.6, 9.2, 'OC1', 'GC1', true]
    const [m] = globalThis._gcfDecodeMarkers(payload(row))
    expect(m).toEqual({
      c: 'GC1', n: 'Name', la: 48.5, lo: 9.1, t: 'T', sz: 'Mi', d: 2, tr: 1.5,
      f: false, s: 'A', m: false, aid: 7, sn: 3, gr: 30, cla: 48.6, clo: 9.2,
      oc: 'OC1', gc: 'GC1', dnf: true,
    })
  })

  it('leaves the trimmed optional keys unset on a short row', () => {
    const row = ['GC2', 'Plain', 48.0, 9.0, 'M', 'S', 3, 2, true, 'A', false]
    const [m] = globalThis._gcfDecodeMarkers(payload(row))
    expect(Object.keys(m)).toHaveLength(11)
    expect(m.aid).toBeUndefined()
    expect(m.cla).toBeUndefined()
    expect(m.dnf).toBeUndefined()
    // The downstream guards all read "== null" / truthiness, so undefined
    // behaves exactly like the omitted key of the old per-marker objects.
    expect(m.sn == null).toBe(true)
    expect(!m.gr).toBe(true)
  })

  it('keeps an explicit null in the middle of a row', () => {
    // An AL parent carries aid but no stage number / geofencing radius.
    const row = ['LC1', 'Adv', 48.0, 9.0, 'L', 'V', null, null, false, 'A', false, 9]
    const [m] = globalThis._gcfDecodeMarkers(payload(row))
    expect(m.aid).toBe(9)
    expect(m.d).toBeNull()
    expect(m.sn).toBeUndefined()
  })

  it('decodes every row and preserves order', () => {
    const markers = globalThis._gcfDecodeMarkers(payload(
      ['GC1', 'A', 1, 2, 'T', 'S', 1, 1, false, 'A', false],
      ['GC2', 'B', 3, 4, 'M', 'S', 1, 1, false, 'A', false],
      ['GC3', 'C', 5, 6, 'U', 'S', 1, 1, false, 'A', false],
    ))
    expect(markers.map(m => m.c)).toEqual(['GC1', 'GC2', 'GC3'])
  })

  it('ignores extra columns the client does not know about', () => {
    // Forward compatibility: a newer server may append a column.
    const row = ['GC4', 'D', 1, 2, 'T', 'S', 1, 1, false, 'A', false,
                 null, null, null, null, null, null, null, null, null, 'future']
    const [m] = globalThis._gcfDecodeMarkers(payload(row))
    expect(Object.keys(m)).toHaveLength(MARKER_FIELDS.length)
    expect(Object.values(m)).not.toContain('future')
  })

  it('decodes the trailing "alp" (Adventure Lab parent) column', () => {
    const parentRow = ['LC1', 'Adv', 48.0, 9.0, 'L', 'V', null, null, false, 'A', false,
                        9, null, null, null, null, null, null, null, true]
    const [parent] = globalThis._gcfDecodeMarkers(payload(parentRow))
    expect(parent.aid).toBe(9)
    expect(parent.alp).toBe(true)

    // A stage carries the same aid but no alp column — trimmed away server-side.
    const stageRow = ['LC1-1', 'Adv S1', 48.0, 9.0, 'L', 'V', null, null, false, 'A', false, 9]
    const [stage] = globalThis._gcfDecodeMarkers(payload(stageRow))
    expect(stage.aid).toBe(9)
    expect(stage.alp).toBeUndefined()
  })

  it('returns an empty list for an empty payload', () => {
    expect(globalThis._gcfDecodeMarkers({ fields: MARKER_FIELDS, rows: [], count: 0 })).toEqual([])
    expect(globalThis._gcfDecodeMarkers({})).toEqual([])
    expect(globalThis._gcfDecodeMarkers(null)).toEqual([])
  })

  it('feeds _gcfPromoteCorrectedCoords the shape it expects', () => {
    const row = ['GC5', 'E', 48.0, 9.0, 'T', 'S', 1, 1, false, 'A', false,
                 null, null, null, 48.5, 9.5]
    const [m] = globalThis._gcfDecodeMarkers(payload(row))
    globalThis._gcfPromoteCorrectedCoords(m)
    expect(m.la).toBe(48.5)
    expect(m.ola).toBe(48.0)
  })
})

// ── _gcfStatusKey — status priority from the f/s/m payload fields ────────────

describe('_gcfStatusKey', () => {
  it('found + archived → FX (special smiley + grey badge)', () => {
    expect(globalThis._gcfStatusKey({ f: true, s: 'X' })).toBe('FX')
  })

  it('archived (not found) → X', () => {
    expect(globalThis._gcfStatusKey({ f: false, s: 'X' })).toBe('X')
  })

  it('disabled → D', () => {
    expect(globalThis._gcfStatusKey({ s: 'D' })).toBe('D')
  })

  it('mine (active, not archived/disabled) → M', () => {
    expect(globalThis._gcfStatusKey({ s: 'A', m: true })).toBe('M')
  })

  it('found (active) → F', () => {
    expect(globalThis._gcfStatusKey({ s: 'A', f: true })).toBe('F')
  })

  it('plain active unfound → U', () => {
    expect(globalThis._gcfStatusKey({ s: 'A' })).toBe('U')
  })

  it('priority: archived beats disabled', () => {
    // Both flags can never be set by the server, but order must be deterministic.
    expect(globalThis._gcfStatusKey({ s: 'X', m: true, f: false })).toBe('X')
  })

  it('priority: found+archived (FX) beats plain archived (X)', () => {
    expect(globalThis._gcfStatusKey({ f: true, s: 'X', m: true })).toBe('FX')
  })

  it('priority: mine beats found when both set on an active cache', () => {
    expect(globalThis._gcfStatusKey({ s: 'A', m: true, f: true })).toBe('M')
  })

  it('missing status field defaults to non-X/D path (treated as active)', () => {
    // Server always sends s, but a defensive marker without s should not crash.
    expect(globalThis._gcfStatusKey({ f: true })).toBe('F')
    expect(globalThis._gcfStatusKey({})).toBe('U')
  })
})

// ── gcfMapIconId — image-id string format ────────────────────────────────────

describe('gcfMapIconId', () => {
  it('builds i-{type}-{platform}-{status} with no suffixes', () => {
    expect(globalThis.gcfMapIconId('T', 'gc', 'U', false, false)).toBe('i-T-gc-U')
  })

  it('appends -dnf when dnf is truthy', () => {
    expect(globalThis.gcfMapIconId('M', 'oc', 'U', true, false)).toBe('i-M-oc-U-dnf')
  })

  it('appends -c when corrected is truthy', () => {
    expect(globalThis.gcfMapIconId('U', 'gc', 'F', false, true)).toBe('i-U-gc-F-c')
  })

  it('appends both -dnf and -c in that order', () => {
    expect(globalThis.gcfMapIconId('T', 'gc', 'U', true, true)).toBe('i-T-gc-U-dnf-c')
  })

  it('appends -s when stage is truthy', () => {
    expect(globalThis.gcfMapIconId('L', 'gc', 'U', false, false, true)).toBe('i-L-gc-U-s')
  })

  it('appends -dnf, -c and -s together in that order', () => {
    expect(globalThis.gcfMapIconId('L', 'gc', 'U', true, true, true)).toBe('i-L-gc-U-dnf-c-s')
  })

  it('id matches the MapLibre concat expression order (i-type-plat-status[dnf][c][s])', () => {
    // map-layers/cache-map build the icon-image via
    // ['concat','i-',type,'-',plat,'-',status,dnf,corrected,stage].
    // gcfMapIconId must produce a string that layer expression can reconstruct.
    const id = globalThis.gcfMapIconId('L', 'gc', 'X', true, true, true)
    expect(id).toBe('i-L-gc-X-dnf-c-s')
  })

  it('a stage and its parent get distinct ids for the same type/platform/status', () => {
    // The whole point of the trailing param: a parent and a stage must never
    // collide on the same cached image.
    const parentId = globalThis.gcfMapIconId('L', 'gc', 'U', false, false, false)
    const stageId = globalThis.gcfMapIconId('L', 'gc', 'U', false, false, true)
    expect(parentId).not.toBe(stageId)
  })
})

// ── _gcfPlatform — gc/oc selection from popup props ──────────────────────────

describe('_gcfPlatform', () => {
  it('returns gc when a gcCode is present', () => {
    expect(globalThis._gcfPlatform({ gcCode: 'GC123' })).toBe('gc')
  })

  it('returns oc when gcCode is absent', () => {
    expect(globalThis._gcfPlatform({ ocCode: 'OC123' })).toBe('oc')
  })

  it("treats the literal string 'null' as no gc code (MapLibre prop coercion)", () => {
    // MapLibre serialises null props to the string "null"; the helper guards for it.
    expect(globalThis._gcfPlatform({ gcCode: 'null', ocCode: 'OC9' })).toBe('oc')
  })
})

// ── gcfPrepareMapIcons — per-marker dedup keying ─────────────────────────────
// We stub gcfEnsureMapIcon to capture (type, platform, statusKey, dnf,
// corrected, stage) tuples instead of touching canvas.

describe('gcfPrepareMapIcons', () => {
  let calls
  beforeEach(() => {
    calls = []
    globalThis.gcfEnsureMapIcon = vi.fn((map, type, plat, sk, dnf, corr, stage) => {
      calls.push({ type, plat, sk, dnf, corr, stage })
      return globalThis.gcfMapIconId(type, plat, sk, dnf, corr, stage)
    })
  })

  it('dedupes markers that share the same icon signature', () => {
    const markers = [
      { t: 'T', s: 'A', gc: 'GC1' },
      { t: 'T', s: 'A', gc: 'GC2' }, // same signature as the first
    ]
    globalThis.gcfPrepareMapIcons({}, markers)
    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({ type: 'T', plat: 'gc', sk: 'U', dnf: false, corr: false, stage: false })
  })

  it('generates a distinct icon per unique (type, platform, status, dnf, corrected)', () => {
    const markers = [
      { t: 'T', s: 'A', gc: 'GC1' },                    // gc, U
      { t: 'T', s: 'A', oc: 'OC1' },                    // oc, U
      { t: 'M', s: 'X', gc: 'GC2' },                    // gc, X
      { t: 'U', s: 'A', f: true, gc: 'GC3' },           // gc, F
      { t: 'U', s: 'A', dnf: true, gc: 'GC4' },         // gc, U, dnf
      { t: 'U', s: 'A', gc: 'GC5', ola: 48.0 },         // gc, U, corrected
    ]
    globalThis.gcfPrepareMapIcons({}, markers)
    expect(calls).toHaveLength(6)
  })

  it('treats an Adventure Lab stage (aid set, no alp) as a distinct icon signature', () => {
    const markers = [
      { t: 'L', s: 'A', gc: 'LC1', aid: 9, alp: true },   // parent
      { t: 'L', s: 'A', gc: 'LC1-1', aid: 9 },            // stage — same type/platform/status
    ]
    globalThis.gcfPrepareMapIcons({}, markers)
    expect(calls).toHaveLength(2)
    expect(calls.find(c => !c.stage)).toBeTruthy()  // parent
    expect(calls.find(c => c.stage)).toBeTruthy()   // stage
  })

  it('a plain (non-adventure) cache is never flagged as a stage', () => {
    const markers = [{ t: 'T', s: 'A', gc: 'GC1' }]
    globalThis.gcfPrepareMapIcons({}, markers)
    expect(calls[0].stage).toBe(false)
  })

  it('platform is oc only when oc set and gc absent', () => {
    const markers = [
      { t: 'T', s: 'A', oc: 'OC1' },          // oc-only → oc
      { t: 'T', s: 'A', gc: 'GC1', oc: 'OC1' }, // both → gc
    ]
    globalThis.gcfPrepareMapIcons({}, markers)
    const plats = calls.map(c => c.plat).sort()
    expect(plats).toEqual(['gc', 'oc'])
  })

  it('dnf is suppressed when the cache is found (found wins over DNF)', () => {
    const markers = [{ t: 'T', s: 'A', f: true, dnf: true, gc: 'GC1' }]
    globalThis.gcfPrepareMapIcons({}, markers)
    expect(calls[0].dnf).toBe(false)
    expect(calls[0].sk).toBe('F')
  })

  it('corrected flag is driven by ola (set after cla→la promotion)', () => {
    const markers = [{ t: 'T', s: 'A', gc: 'GC1', ola: 48.0, olo: 9.0 }]
    globalThis.gcfPrepareMapIcons({}, markers)
    expect(calls[0].corr).toBe(true)
  })
})

// ── _gcfMarkerColor / _gcfMarkerBorderColor — circle colour resolution ───────

describe('_gcfMarkerColor', () => {
  it('archived → grey', () => {
    expect(globalThis._gcfMarkerColor({ s: 'X' })).toBe('#6c757d')
  })
  it('disabled → light grey', () => {
    expect(globalThis._gcfMarkerColor({ s: 'D' })).toBe('#adb5bd')
  })
  it('mine (active) → yellow', () => {
    expect(globalThis._gcfMarkerColor({ s: 'A', m: true })).toBe('#ffc107')
  })
  it('found (active) → green', () => {
    expect(globalThis._gcfMarkerColor({ s: 'A', f: true })).toBe('#198754')
  })
  it('unfound active → blue', () => {
    expect(globalThis._gcfMarkerColor({ s: 'A' })).toBe('#0d6efd')
  })
  it('archived takes priority over mine and found', () => {
    expect(globalThis._gcfMarkerColor({ s: 'X', m: true, f: true })).toBe('#6c757d')
  })
  it('mine takes priority over found', () => {
    expect(globalThis._gcfMarkerColor({ s: 'A', m: true, f: true })).toBe('#ffc107')
  })
})

describe('_gcfMarkerBorderColor', () => {
  it('archived border', () => {
    expect(globalThis._gcfMarkerBorderColor({ s: 'X' })).toBe('#495057')
  })
  it('disabled border', () => {
    expect(globalThis._gcfMarkerBorderColor({ s: 'D' })).toBe('#6c757d')
  })
  it('default black border for active caches', () => {
    expect(globalThis._gcfMarkerBorderColor({ s: 'A', f: true })).toBe('#000')
  })
})

// ── _gcfPromoteCorrectedCoords — cla/clo → la/lo, originals → ola/olo ─────────
// Extracted from the marker-fetch .then() callback in cache-map.js so the
// promotion rule (which drives every downstream "is corrected" check via
// m.ola != null) is unit-testable without a network round-trip.

describe('_gcfPromoteCorrectedCoords', () => {
  it('promotes corrected coords to primary and stashes the originals', () => {
    const m = { la: 48.0, lo: 9.0, cla: 48.5, clo: 9.5 }
    globalThis._gcfPromoteCorrectedCoords(m)
    expect(m.la).toBe(48.5)   // corrected became primary
    expect(m.lo).toBe(9.5)
    expect(m.ola).toBe(48.0)  // original lat preserved
    expect(m.olo).toBe(9.0)   // original lon preserved
  })

  it('leaves markers without corrected coords untouched (no ola/olo)', () => {
    const m = { la: 48.0, lo: 9.0 }
    globalThis._gcfPromoteCorrectedCoords(m)
    expect(m.la).toBe(48.0)
    expect(m.lo).toBe(9.0)
    expect(m.ola).toBeUndefined()
    expect(m.olo).toBeUndefined()
  })

  it('treats a present-but-null cla/clo as "not corrected"', () => {
    const m = { la: 48.0, lo: 9.0, cla: null, clo: null }
    globalThis._gcfPromoteCorrectedCoords(m)
    expect(m.la).toBe(48.0)
    expect(m.ola).toBeUndefined()
  })

  it('requires BOTH cla and clo before promoting', () => {
    const m = { la: 48.0, lo: 9.0, cla: 48.5 } // clo missing
    globalThis._gcfPromoteCorrectedCoords(m)
    expect(m.la).toBe(48.0)
    expect(m.ola).toBeUndefined()
  })

  it('preserves a corrected coordinate of exactly 0 (uses != null, not falsy)', () => {
    // lat 0 / lon 0 is a valid corrected position; a truthiness check would
    // wrongly skip it. The guard is `!= null`, so it must still promote.
    const m = { la: 1.0, lo: 1.0, cla: 0, clo: 0 }
    globalThis._gcfPromoteCorrectedCoords(m)
    expect(m.la).toBe(0)
    expect(m.lo).toBe(0)
    expect(m.ola).toBe(1.0)
    expect(m.olo).toBe(1.0)
  })

  it('returns the same marker object (in-place mutation)', () => {
    const m = { la: 48.0, lo: 9.0, cla: 48.5, clo: 9.5 }
    expect(globalThis._gcfPromoteCorrectedCoords(m)).toBe(m)
  })

  it('tolerates a null/undefined marker without throwing', () => {
    expect(() => globalThis._gcfPromoteCorrectedCoords(null)).not.toThrow()
    expect(() => globalThis._gcfPromoteCorrectedCoords(undefined)).not.toThrow()
  })
})
