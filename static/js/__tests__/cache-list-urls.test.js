/**
 * Tests for the pure URL/param-building helpers in cache-list.js.
 *
 * Covers the helpers NOT already exercised by smoke.test.js
 * (gcfBuildCurrentListUrl is tested there):
 *
 *   - gcfEncodeFx / gcfDecodeFx  — ?fx= base64url(JSON) round-trip and
 *       byte-for-byte parity with geocaches/filter_expr.py (to_url_param).
 *   - gcfRemoveFilter            — chip removal param surgery (whole-param
 *       delete vs single-value removal from a CSV param), always drops ?page=.
 *   - filterByTag                — tag-select toggle (set vs clear-on-repeat).
 *   - _gcfReadToolbarConditions  — toolbar widgets → fx condition list.
 *
 * cache-list.js uses plain function declarations + a few IIFEs that only
 * register listeners (guarded on window.htmx / element presence), so it loads
 * cleanly under the indirect-eval harness from smoke.test.js. The redirect
 * helpers assign window.location.href / use sessionStorage — both stubbed.
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

// Stub window.location so reads of .search/.pathname work and writes to
// .href are captured (gcfRemoveFilter/filterByTag navigate by assignment).
function stubLocation(href) {
  const u = new URL(href)
  let assigned = null
  Object.defineProperty(window, 'location', {
    value: {
      get search() { return u.search },
      get pathname() { return u.pathname },
      get href() { return assigned !== null ? assigned : u.href },
      set href(v) { assigned = v },
    },
    writable: true,
    configurable: true,
  })
  return () => assigned
}

beforeAll(() => {
  loadScript('cache-list.js')
})

beforeEach(() => {
  document.body.innerHTML = ''
})

// ── gcfEncodeFx / gcfDecodeFx — round-trip + Python parity ───────────────────

describe('gcfEncodeFx / gcfDecodeFx', () => {
  it('returns empty string for an empty/childless tree', () => {
    expect(globalThis.gcfEncodeFx(null)).toBe('')
    expect(globalThis.gcfEncodeFx({ g: 'and', c: [] })).toBe('')
    expect(globalThis.gcfEncodeFx({ g: 'and' })).toBe('')
  })

  it('encodes to URL-safe base64 with no padding and no +/=', () => {
    const enc = globalThis.gcfEncodeFx({ g: 'and', c: [{ f: 'country', op: 'in', v: ['DE'] }] })
    expect(enc).not.toMatch(/[+/=]/)
  })

  it('is byte-for-byte identical to filter_expr.to_url_param (Python parity)', () => {
    // Python (geocaches/filter_expr.py::to_url_param):
    //   json.dumps(tree, separators=(",",":"), ensure_ascii=False) → utf-8
    //   → base64.urlsafe_b64encode → rstrip("=")
    // verified via: uv run python -c "..."  →  the literal below.
    const tree = { g: 'and', c: [{ f: 'country', op: 'in', v: ['DE'] }] }
    const PY = 'eyJnIjoiYW5kIiwiYyI6W3siZiI6ImNvdW50cnkiLCJvcCI6ImluIiwidiI6WyJERSJdfV19'
    expect(globalThis.gcfEncodeFx(tree)).toBe(PY)
  })

  it('round-trips a multi-condition tree', () => {
    const tree = {
      g: 'or',
      c: [
        { f: 'cache_type', op: 'in', v: ['T', 'M'] },
        { f: 'distance_km', op: 'lt', v: 10 },
      ],
    }
    expect(globalThis.gcfDecodeFx(globalThis.gcfEncodeFx(tree))).toEqual(tree)
  })

  it('preserves non-ASCII values through the round-trip (ensure_ascii=False parity)', () => {
    const tree = { g: 'and', c: [{ f: 'name', op: 'contains', v: ['Tübingen — café'] }] }
    expect(globalThis.gcfDecodeFx(globalThis.gcfEncodeFx(tree))).toEqual(tree)
  })

  it('gcfDecodeFx returns an empty group for empty input', () => {
    expect(globalThis.gcfDecodeFx('')).toEqual({ g: 'and', c: [] })
    expect(globalThis.gcfDecodeFx(null)).toEqual({ g: 'and', c: [] })
  })

  it('gcfDecodeFx returns an empty group (does not throw) on garbage', () => {
    expect(globalThis.gcfDecodeFx('!!!not-base64!!!')).toEqual({ g: 'and', c: [] })
  })
})

// ── gcfRemoveFilter — chip removal param surgery ─────────────────────────────

describe('gcfRemoveFilter', () => {
  function urlAfterRemove(initialSearch, csv) {
    const read = stubLocation('http://localhost/caches/' + initialSearch)
    globalThis.sessionStorage.clear()
    globalThis.gcfRemoveFilter(csv, null)
    return read()
  }

  it('deletes an entire param when the entry has no "=" value', () => {
    const next = urlAfterRemove('?q=puzzle&found=1', 'found')
    const params = new URL('http://localhost' + next.replace(/^[^?]+/, '')).searchParams
    expect(params.get('found')).toBeNull()
    expect(params.get('q')).toBe('puzzle')
  })

  it('removes a single value from a CSV param, keeping the rest', () => {
    const next = urlAfterRemove('?type=T,M,U', 'type=M')
    const params = new URL('http://localhost' + next.replace(/^[^?]+/, '')).searchParams
    expect(params.get('type')).toBe('T,U')
  })

  it('deletes the whole param when removing its only CSV value', () => {
    const next = urlAfterRemove('?type=M', 'type=M')
    const params = new URL('http://localhost' + next.replace(/^[^?]+/, '')).searchParams
    expect(params.get('type')).toBeNull()
  })

  it('handles multiple comma-separated removal directives in one call', () => {
    const next = urlAfterRemove('?type=T,M&found=1&q=x', 'type=M,found')
    const params = new URL('http://localhost' + next.replace(/^[^?]+/, '')).searchParams
    expect(params.get('type')).toBe('T')
    expect(params.get('found')).toBeNull()
    expect(params.get('q')).toBe('x')
  })

  it('always drops ?page= so removal resets to page 1', () => {
    const next = urlAfterRemove('?q=x&page=4', 'q')
    const params = new URL('http://localhost' + next.replace(/^[^?]+/, '')).searchParams
    expect(params.get('page')).toBeNull()
  })

  it('persists the rebuilt URL to sessionStorage before navigating', () => {
    const next = urlAfterRemove('?q=x&found=1', 'found')
    expect(globalThis.sessionStorage.getItem('gcforge_list_url')).toBe(next)
  })
})

// ── filterByTag — tag-select toggle ──────────────────────────────────────────

describe('filterByTag', () => {
  function setup(currentTag) {
    document.body.innerHTML = `
      <div id="cache-table-container">
        <div data-params="tag=${currentTag}&q=foo"></div>
      </div>
      <form id="filter-form">
        <select name="tag"><option value=""></option><option value="hike">hike</option></select>
      </form>`
    return document.querySelector('#filter-form [name="tag"]')
  }

  it('sets the tag select to the clicked tag when a different tag is active', () => {
    const sel = setup('walk')
    let changed = false
    sel.addEventListener('change', () => { changed = true })
    globalThis.filterByTag('hike')
    expect(sel.value).toBe('hike')
    expect(changed).toBe(true)
  })

  it('clears the tag select when the clicked tag is already active (toggle off)', () => {
    const sel = setup('hike')
    globalThis.filterByTag('hike')
    expect(sel.value).toBe('')
  })

  it('is a no-op when no #filter-form tag select exists', () => {
    document.body.innerHTML = '<div id="cache-table-container"><div data-params=""></div></div>'
    expect(() => globalThis.filterByTag('hike')).not.toThrow()
  })
})

// ── _gcfReadToolbarConditions — toolbar widgets → fx condition list ──────────

describe('_gcfReadToolbarConditions', () => {
  it('returns no conditions when every widget is at its empty value', () => {
    document.body.innerHTML = `
      <select data-fx-enum="cache_type"><option value="" selected></option></select>
      <select data-fx-found><option value="" selected></option></select>`
    expect(globalThis._gcfReadToolbarConditions()).toEqual([])
  })

  it('maps a single-value enum widget to an {f,op:in,v:[value]} condition', () => {
    document.body.innerHTML =
      '<select data-fx-enum="cache_type"><option value="T" selected>T</option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'cache_type', op: 'in', v: ['T'] },
    ])
  })

  it('maps the found tri-state to is_true / is_false', () => {
    document.body.innerHTML = '<select data-fx-found><option value="1" selected>1</option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'found', op: 'is_true', v: true },
    ])
    document.body.innerHTML = '<select data-fx-found><option value="0" selected>0</option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'found', op: 'is_false', v: true },
    ])
  })

  it('maps country __none__ to is_none and a code to in', () => {
    document.body.innerHTML =
      '<select data-fx-country><option value="__none__" selected></option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'country', op: 'is_none', v: true },
    ])
    document.body.innerHTML =
      '<select data-fx-country><option value="DE" selected>DE</option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'country', op: 'in', v: ['DE'] },
    ])
  })

  it('maps tags __none__ to is_none on the tags field', () => {
    document.body.innerHTML =
      '<select data-fx-tag><option value="__none__" selected></option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'tags', op: 'is_none', v: true },
    ])
  })

  it('translates a simple flag to a boolean is_true condition on the mapped field', () => {
    document.body.innerHTML =
      '<select data-fx-flag><option value="corrected_coords" selected></option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'has_corrected_coordinates', op: 'is_true', v: true },
    ])
  })

  it('maps the alc_in_progress flag to an alc/in_progress condition', () => {
    document.body.innerHTML =
      '<select data-fx-flag><option value="alc_in_progress" selected></option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([
      { f: 'alc', op: 'in_progress', v: true },
    ])
  })

  it('ignores exotic flags (they flow through ?flag= instead of fx)', () => {
    document.body.innerHTML =
      '<select data-fx-flag><option value="ftf_possible" selected></option></select>'
    expect(globalThis._gcfReadToolbarConditions()).toEqual([])
  })

  it('collects conditions from several widgets at once', () => {
    document.body.innerHTML = `
      <select data-fx-enum="cache_type"><option value="T" selected>T</option></select>
      <select data-fx-found><option value="1" selected>1</option></select>
      <select data-fx-country><option value="DE" selected>DE</option></select>`
    const conds = globalThis._gcfReadToolbarConditions()
    expect(conds).toContainEqual({ f: 'cache_type', op: 'in', v: ['T'] })
    expect(conds).toContainEqual({ f: 'found', op: 'is_true', v: true })
    expect(conds).toContainEqual({ f: 'country', op: 'in', v: ['DE'] })
    expect(conds).toHaveLength(3)
  })
})
