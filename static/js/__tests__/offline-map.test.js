/**
 * Tests for map-offline.js — offline PMTiles area management and style building.
 *
 * Uses the same indirect-eval pattern as smoke.test.js so function declarations
 * land on globalThis exactly as a browser <script> tag would.
 */

import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest'
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
  // jsdom sets window.location.origin to 'http://localhost' — no setup needed.
  loadScript('map-offline.js')
})

beforeEach(() => {
  globalThis._gcfOfflineAreas = []
  delete globalThis.gcfMapSetStyle
  delete window.setLayer
  localStorage.clear()
})

// ── _gcfOfflineLayersForSource ────────────────────────────────────────────────

const LAYER_COUNT = 12

describe('_gcfOfflineLayersForSource', () => {
  it('returns 12 layers', () => {
    expect(globalThis._gcfOfflineLayersForSource('s')).toHaveLength(LAYER_COUNT)
  })

  it('all layers reference the given source', () => {
    globalThis._gcfOfflineLayersForSource('my-src').forEach(l =>
      expect(l.source).toBe('my-src')
    )
  })

  it('all layer IDs are unique and prefixed with srcId', () => {
    const layers = globalThis._gcfOfflineLayersForSource('pfx')
    const ids = layers.map(l => l.id)
    expect(new Set(ids).size).toBe(ids.length)
    ids.forEach(id => expect(id.startsWith('pfx-')).toBe(true))
  })

  it('earth: fill layer on the earth source-layer', () => {
    const earth = globalThis._gcfOfflineLayersForSource('s').find(l => l.id === 's-earth')
    expect(earth.type).toBe('fill')
    expect(earth['source-layer']).toBe('earth')
  })

  it('water-line: has a geometry-type filter to select LineStrings', () => {
    const wl = globalThis._gcfOfflineLayersForSource('s').find(l => l.id === 's-water-line')
    expect(wl.type).toBe('line')
    expect(JSON.stringify(wl.filter)).toContain('geometry-type')
  })

  it('roads-tunnels: has a line-dasharray paint property', () => {
    const tunnels = globalThis._gcfOfflineLayersForSource('s').find(l => l.id === 's-roads-tunnels')
    expect(Array.isArray(tunnels.paint['line-dasharray'])).toBe(true)
    expect(tunnels.paint['line-dasharray'].length).toBeGreaterThan(0)
  })

  it('buildings: minzoom is 14', () => {
    const bldg = globalThis._gcfOfflineLayersForSource('s').find(l => l.id === 's-buildings')
    expect(bldg.minzoom).toBe(14)
  })

  it('road-labels: symbol type with text-font as a string array', () => {
    const labels = globalThis._gcfOfflineLayersForSource('s').find(l => l.id === 's-road-labels')
    expect(labels.type).toBe('symbol')
    expect(Array.isArray(labels.layout['text-font'])).toBe(true)
    labels.layout['text-font'].forEach(f => expect(typeof f).toBe('string'))
  })

  it('road-labels: minzoom is 13', () => {
    const labels = globalThis._gcfOfflineLayersForSource('s').find(l => l.id === 's-road-labels')
    expect(labels.minzoom).toBe(13)
  })

  it('places: symbol type with symbol-sort-key defined', () => {
    const places = globalThis._gcfOfflineLayersForSource('s').find(l => l.id === 's-places')
    expect(places.type).toBe('symbol')
    expect(places.layout['symbol-sort-key']).toBeDefined()
  })
})

// ── gcfBuildOfflineStyle ──────────────────────────────────────────────────────

describe('gcfBuildOfflineStyle', () => {
  it('returns a MapLibre style object with version 8', () => {
    expect(globalThis.gcfBuildOfflineStyle().version).toBe(8)
  })

  it('glyphs URL includes window.location.origin and required template vars', () => {
    const { glyphs } = globalThis.gcfBuildOfflineStyle()
    expect(glyphs).toContain(window.location.origin)
    expect(glyphs).toContain('{fontstack}')
    expect(glyphs).toContain('{range}')
  })

  it('empty areas => no sources and no layers', () => {
    const style = globalThis.gcfBuildOfflineStyle()
    expect(Object.keys(style.sources)).toHaveLength(0)
    expect(style.layers).toHaveLength(0)
  })

  it('one area => source keyed as gcf-offline-{id}', () => {
    globalThis._gcfOfflineAreas = [{ id: 42 }]
    expect(globalThis.gcfBuildOfflineStyle().sources['gcf-offline-42']).toBeDefined()
  })

  it('one area => source is vector type with pmtiles:// URL containing area ID', () => {
    globalThis._gcfOfflineAreas = [{ id: 7 }]
    const src = globalThis.gcfBuildOfflineStyle().sources['gcf-offline-7']
    expect(src.type).toBe('vector')
    expect(src.url).toMatch(/^pmtiles:\/\//)
    expect(src.url).toContain('/7/tiles.pmtiles')
  })

  it('one area => 12 layers', () => {
    globalThis._gcfOfflineAreas = [{ id: 1 }]
    expect(globalThis.gcfBuildOfflineStyle().layers).toHaveLength(LAYER_COUNT)
  })

  it('two areas => 24 layers with no duplicate layer IDs', () => {
    globalThis._gcfOfflineAreas = [{ id: 1 }, { id: 2 }]
    const { layers } = globalThis.gcfBuildOfflineStyle()
    expect(layers).toHaveLength(LAYER_COUNT * 2)
    const ids = layers.map(l => l.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('source attribution is set', () => {
    globalThis._gcfOfflineAreas = [{ id: 1 }]
    const src = globalThis.gcfBuildOfflineStyle().sources['gcf-offline-1']
    expect(src.attribution).toBeTruthy()
  })
})

// ── _gcfOfflineUpdateButton ───────────────────────────────────────────────────

describe('_gcfOfflineUpdateButton', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('hides both UI elements when areas list is empty', () => {
    document.body.innerHTML = `
      <label id="map-offline-label"></label>
      <button id="detail-offline-btn"></button>
    `
    globalThis._gcfOfflineAreas = []
    globalThis._gcfOfflineUpdateButton()
    expect(document.getElementById('map-offline-label').style.display).toBe('none')
    expect(document.getElementById('detail-offline-btn').style.display).toBe('none')
  })

  it('shows both UI elements when areas list is non-empty', () => {
    document.body.innerHTML = `
      <label id="map-offline-label" style="display:none"></label>
      <button id="detail-offline-btn" style="display:none"></button>
    `
    globalThis._gcfOfflineAreas = [{ id: 1 }]
    globalThis._gcfOfflineUpdateButton()
    expect(document.getElementById('map-offline-label').style.display).toBe('')
    expect(document.getElementById('detail-offline-btn').style.display).toBe('')
  })

  it('does not throw when neither element exists in the DOM', () => {
    globalThis._gcfOfflineAreas = [{ id: 1 }]
    expect(() => globalThis._gcfOfflineUpdateButton()).not.toThrow()
  })

  it('handles partially present DOM (only list-view label)', () => {
    document.body.innerHTML = `<label id="map-offline-label" style="display:none"></label>`
    globalThis._gcfOfflineAreas = [{ id: 1 }]
    expect(() => globalThis._gcfOfflineUpdateButton()).not.toThrow()
    expect(document.getElementById('map-offline-label').style.display).toBe('')
  })
})

// ── gcfOfflineLoadAreas ───────────────────────────────────────────────────────

describe('gcfOfflineLoadAreas', () => {
  afterEach(() => {
    delete globalThis.fetch
  })

  function mockFetch(areas) {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve(areas)
    })
  }

  it('populates _gcfOfflineAreas from the API response', async () => {
    const areas = [{ id: 1 }, { id: 2 }]
    mockFetch(areas)
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(globalThis._gcfOfflineAreas).toEqual(areas)
  })

  it('calls onDone after a successful fetch', async () => {
    mockFetch([])
    const onDone = vi.fn()
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, () => { onDone(); res() }))
    expect(onDone).toHaveBeenCalledOnce()
  })

  it('calls onDone even when fetch rejects', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network error'))
    const onDone = vi.fn()
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, () => { onDone(); res() }))
    expect(onDone).toHaveBeenCalledOnce()
  })

  it('treats a null API response as an empty array', async () => {
    mockFetch(null)
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(globalThis._gcfOfflineAreas).toEqual([])
  })

  it('fetches /offline-maps/areas.json', async () => {
    mockFetch([])
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(globalThis.fetch).toHaveBeenCalledWith('/offline-maps/areas.json')
  })

  it('auto-restores via window.setLayer when offline was the last saved style', async () => {
    localStorage.setItem('gcforge_map_style', 'offline')
    mockFetch([{ id: 1 }])
    window.setLayer = vi.fn()
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(window.setLayer).toHaveBeenCalledWith('offline', null)
  })

  it('auto-restores via gcfMapSetStyle when setLayer is absent', async () => {
    localStorage.setItem('gcforge_map_style', 'offline')
    mockFetch([{ id: 1 }])
    globalThis.gcfMapSetStyle = vi.fn()
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(globalThis.gcfMapSetStyle).toHaveBeenCalledWith('offline')
  })

  it('prefers window.setLayer over gcfMapSetStyle for auto-restore', async () => {
    localStorage.setItem('gcforge_map_style', 'offline')
    mockFetch([{ id: 1 }])
    window.setLayer = vi.fn()
    globalThis.gcfMapSetStyle = vi.fn()
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(window.setLayer).toHaveBeenCalledOnce()
    expect(globalThis.gcfMapSetStyle).not.toHaveBeenCalled()
  })

  it('does not auto-restore when saved style is not offline', async () => {
    localStorage.setItem('gcforge_map_style', 'street')
    mockFetch([{ id: 1 }])
    window.setLayer = vi.fn()
    globalThis.gcfMapSetStyle = vi.fn()
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(window.setLayer).not.toHaveBeenCalled()
    expect(globalThis.gcfMapSetStyle).not.toHaveBeenCalled()
  })

  it('does not auto-restore when areas list is empty', async () => {
    localStorage.setItem('gcforge_map_style', 'offline')
    mockFetch([])
    window.setLayer = vi.fn()
    await new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))
    expect(window.setLayer).not.toHaveBeenCalled()
  })

  it('does not auto-restore when neither restore function is available', async () => {
    localStorage.setItem('gcforge_map_style', 'offline')
    mockFetch([{ id: 1 }])
    // No setLayer or gcfMapSetStyle defined — should not throw
    await expect(new Promise(res => globalThis.gcfOfflineLoadAreas(null, res))).resolves.toBeUndefined()
  })
})
