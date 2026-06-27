/**
 * Smoke tests for GCForge frontend JS.
 *
 * CONSTRAINT: Source files use plain function declarations called from inline
 * onclick handlers. Adding `export` statements would work syntactically but
 * violates the project convention (see memory: feedback_external_js_functions.md).
 *
 * WORKAROUND: indirect eval — `(0, eval)(code)` runs non-strict code in the
 * global scope, promoting function declarations to globalThis exactly as a
 * browser <script> tag does. Direct eval inside an ES module is strict-scoped
 * and would keep the declarations local. Indirect eval avoids that.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

function loadScript(filename) {
  const code = readFileSync(resolve(jsDir, filename), 'utf8')
  // Indirect eval: non-strict code, function declarations land on globalThis.
  // In jsdom + Vitest, globalThis === window, matching browser <script> behaviour.
  ;(0, eval)(code)
}

beforeAll(() => {
  // file-browser.js: only var declarations and function declarations at top
  // level — safe to load unconditionally.
  loadScript('file-browser.js')

  // cache-detail.js: all DOM code is inside `if (cfg)` where
  // cfg = document.getElementById('cache-detail-config') → null in jsdom,
  // so those blocks are skipped. Pure helper functions are declared at
  // top level and become available as globals.
  loadScript('cache-detail.js')

  // cache-list.js: top-level htmx.on guards on window.htmx; document.body
  // addEventListener calls only register handlers. Both safe in jsdom.
  loadScript('cache-list.js')
})

// ── Test 1: _fbFormatSize ──────────────────────────────────────────────────────
// From file-browser.js — no DOM dependencies.

describe('_fbFormatSize', () => {
  it('formats bytes under 1 KB as "N B"', () => {
    expect(globalThis._fbFormatSize(512)).toBe('512 B')
  })

  it('formats bytes in KB range', () => {
    expect(globalThis._fbFormatSize(1536)).toBe('1.5 KB')
  })

  it('formats bytes in MB range', () => {
    expect(globalThis._fbFormatSize(1048576)).toBe('1.0 MB')
  })

  it('returns empty string for null/undefined', () => {
    expect(globalThis._fbFormatSize(null)).toBe('')
    expect(globalThis._fbFormatSize(undefined)).toBe('')
  })
})

// ── Test 2: _fbEscapeJs ───────────────────────────────────────────────────────
// From file-browser.js — pure string replacement, no DOM.

describe('_fbEscapeJs', () => {
  it('escapes backslashes', () => {
    expect(globalThis._fbEscapeJs('C:\\Users\\test')).toBe("C:\\\\Users\\\\test")
  })

  it('escapes single quotes', () => {
    expect(globalThis._fbEscapeJs("it's a test")).toBe("it\\'s a test")
  })

  it('leaves plain strings unchanged', () => {
    expect(globalThis._fbEscapeJs('hello/world')).toBe('hello/world')
  })
})

// ── Test 3: _gcfFormatDD ──────────────────────────────────────────────────────
// From cache-detail.js — pure arithmetic, formats lat/lon as decimal degrees.

describe('_gcfFormatDD', () => {
  it('formats a coordinate pair as decimal degrees', () => {
    expect(globalThis._gcfFormatDD(48.5, 9.25)).toBe('48.500000  9.250000')
  })

  it('formats negative coordinates correctly', () => {
    expect(globalThis._gcfFormatDD(-33.8688, 151.2093)).toBe('-33.868800  151.209300')
  })

  it('formats the origin', () => {
    expect(globalThis._gcfFormatDD(0, 0)).toBe('0.000000  0.000000')
  })
})

// ── Test 4: _gcfFormatDMM ─────────────────────────────────────────────────────
// From cache-detail.js — converts decimal degrees to degrees + decimal minutes.

describe('_gcfFormatDMM', () => {
  it('formats whole-degree coordinates', () => {
    // lat=48.5 → N 48° 30.000', lon=9.25 → E 09° 15.000'
    expect(globalThis._gcfFormatDMM(48.5, 9.25)).toBe("N 48\u00b0 30.000'  E 09\u00b0 15.000'")
  })

  it('uses S/W for negative coordinates', () => {
    const result = globalThis._gcfFormatDMM(-10.0, -20.0)
    expect(result).toContain('S')
    expect(result).toContain('W')
  })
})

// ── Test 5: _gcfTrySplitCoordPair ─────────────────────────────────────────────
// From cache-detail.js — parses a "lat lon" string into [latStr, lonStr].
// Used in the corrected-coords form to auto-split a pasted coordinate pair.

// ── gcfBuildCurrentListUrl — preserves ?fx= when toggling Now-Forging ──────
// Regression guard: gcfSubmitScope() used to serialize #filter-form with
// FormData only, which drops every data-fx-* toolbar widget (Type, Country,
// Status, Tag, Flag, Found) since those carry no name=. The helper now mirrors
// gcfToolbarConfigureRequest so the redirect URL keeps the user's filters.

describe('gcfBuildCurrentListUrl', () => {
  function setupListPage({ fx = '', q = '' } = {}) {
    Object.defineProperty(window, 'location', {
      value: new URL('http://localhost/caches/' + (fx ? '?fx=' + fx : '')),
      writable: true, configurable: true,
    })
    document.body.innerHTML = `
      <form id="filter-form">
        <input name="q" value="${q}">
        <select data-fx-enum="cache_type"><option value="" selected></option><option value="T">T</option></select>
        <select data-fx-country><option value="" selected></option><option value="DE">DE</option></select>
        <select data-fx-tag><option value="" selected></option></select>
        <select data-fx-flag><option value="" selected></option></select>
        <select data-fx-found><option value="" selected></option><option value="1">1</option></select>
      </form>`
  }

  it('returns null when no filter-form exists', () => {
    document.body.innerHTML = ''
    expect(globalThis.gcfBuildCurrentListUrl()).toBeNull()
  })

  it('preserves a country filter encoded in ?fx= across the rebuild', () => {
    // fx = b64url-json({g:'and',c:[{f:'country',op:'in',v:['DE']}]}) — the
    // shape encoded by the toolbar — and the Country select reflects DE.
    setupListPage({ fx: globalThis.gcfEncodeFx({ g: 'and', c: [{ f: 'country', op: 'in', v: ['DE'] }] }) })
    document.querySelector('[data-fx-country]').value = 'DE'
    const next = globalThis.gcfBuildCurrentListUrl()
    const fx = new URL('http://localhost' + next.replace(/^[^?]+/, '')).searchParams.get('fx')
    expect(fx).toBeTruthy()
    const tree = globalThis.gcfDecodeFx(fx)
    expect(tree.c.some(c => c.f === 'country' && c.v[0] === 'DE')).toBe(true)
  })

  it('keeps non-toolbar fx conditions when only the form changes', () => {
    // distance condition is outside the toolbar's owned-fields set.
    const fx = globalThis.gcfEncodeFx({ g: 'and', c: [{ f: 'distance_km', op: 'lt', v: 10 }] })
    setupListPage({ fx, q: 'puzzle' })
    const next = globalThis.gcfBuildCurrentListUrl()
    const params = new URL('http://localhost' + next.replace(/^[^?]+/, '')).searchParams
    const tree = globalThis.gcfDecodeFx(params.get('fx'))
    expect(tree.c.some(c => c.f === 'distance_km')).toBe(true)
    expect(params.get('q')).toBe('puzzle')
  })

  it('strips empty params and omits ?fx= when nothing is set', () => {
    setupListPage()
    const next = globalThis.gcfBuildCurrentListUrl()
    expect(next).toBe('/caches/')
  })
})

describe('_gcfTrySplitCoordPair', () => {
  it('splits a decimal pair separated by comma+space', () => {
    expect(globalThis._gcfTrySplitCoordPair('48.5, 9.25')).toEqual(['48.5', '9.25'])
  })

  it('splits a hemisphere-prefixed DMM pair', () => {
    const result = globalThis._gcfTrySplitCoordPair("N 48° 18.189' E 008° 58.876'")
    expect(result).toEqual(["N 48° 18.189'", "E 008° 58.876'"])
  })

  it('returns null for a single coordinate (no split possible)', () => {
    expect(globalThis._gcfTrySplitCoordPair('48.5')).toBeNull()
  })

  it('returns null for an empty string', () => {
    expect(globalThis._gcfTrySplitCoordPair('')).toBeNull()
  })
})
