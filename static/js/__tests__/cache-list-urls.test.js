/**
 * Tests for the pure URL/param-building helpers in cache-list.js.
 *
 * Covers the helpers NOT already exercised by smoke.test.js
 * (gcfBuildCurrentListUrl is tested there):
 *
 *   - gcfEncodeFx / gcfDecodeFx  — ?fx= base64url(JSON) round-trip and
 *       byte-for-byte parity with geocaches/filtering/filter_expr.py (to_url_param).
 *   - gcfRemoveFilter            — chip removal param surgery (whole-param
 *       delete vs single-value removal from a CSV param), always drops ?page=.
 *   - filterByTag                — tag-select toggle (set vs clear-on-repeat).
 *   - _gcfReadToolbarConditions  — toolbar widgets → fx condition list.
 *   - _gcfMergeToolbarConditions — combines kept fx conditions with toolbar
 *       conditions; shared by gcfToolbarConfigureRequest and
 *       gcfBuildCurrentListUrl.
 *   - _gcfSyncToolbarFromUrl     — URL → toolbar widgets (search box,
 *       dropdowns, ref, saved-filter picker), so Reset / chip removal never
 *       leave stale values behind.
 *
 * cache-list.js uses plain function declarations + a few IIFEs that only
 * register listeners (guarded on window.htmx / element presence), so it loads
 * cleanly under the indirect-eval harness from smoke.test.js. The redirect
 * helpers assign window.location.href / use sessionStorage — both stubbed.
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

  it('is byte-for-byte identical to filtering.filter_expr.to_url_param (Python parity)', () => {
    // Python (geocaches/filtering/filter_expr.py::to_url_param):
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

// ── _gcfMergeToolbarConditions — AND-onto-tree merge (regression) ────────────
// Bug: toolbar conditions (Country/Type/Status/…) were spliced straight into
// the existing tree's c[] under the tree's own root op. For an "and" root
// that's harmless (flattening), but for an "or" root — e.g. the "show all
// with missing information" menu filter, `state is_none OR county is_none OR
// elevation is_null` — it turned a new toolbar condition into a 4th OR
// branch instead of ANDing it onto the whole group, matching everything.

describe('_gcfMergeToolbarConditions', () => {
  it('flattens into the same "and" group when the root is already "and"', () => {
    const tree = { g: 'and', c: [{ f: 'distance_km', op: 'lt', v: 10 }] }
    const kept = tree.c
    const merged = globalThis._gcfMergeToolbarConditions(tree, kept, [{ f: 'country', op: 'in', v: ['DE'] }])
    expect(merged).toEqual({
      g: 'and',
      c: [
        { f: 'distance_km', op: 'lt', v: 10 },
        { f: 'country', op: 'in', v: ['DE'] },
      ],
    })
  })

  it('wraps an "or" root as a subgroup instead of appending into it (the reported bug)', () => {
    const tree = {
      g: 'or',
      c: [
        { f: 'state', op: 'is_none', v: null },
        { f: 'county', op: 'is_none', v: null },
        { f: 'elevation', op: 'is_null', v: true },
      ],
    }
    const kept = tree.c
    const toolbarConds = [{ f: 'country', op: 'in', v: ['DE'] }]
    const merged = globalThis._gcfMergeToolbarConditions(tree, kept, toolbarConds)

    // Root must be "and" so Country ANDs onto the whole OR group, not into it.
    expect(merged.g).toBe('and')
    expect(merged.c).toHaveLength(2)
    const [orGroup, countryCond] = merged.c
    expect(orGroup).toEqual(tree)
    expect(countryCond).toEqual({ f: 'country', op: 'in', v: ['DE'] })
  })

  it('uses a plain "and" group when kept is empty, regardless of the original root op', () => {
    const tree = { g: 'or', c: [] }
    const merged = globalThis._gcfMergeToolbarConditions(tree, [], [
      { f: 'cache_type', op: 'in', v: ['T'] },
      { f: 'country', op: 'in', v: ['DE'] },
    ])
    // Multiple toolbar dropdowns must AND together, never OR.
    expect(merged).toEqual({
      g: 'and',
      c: [
        { f: 'cache_type', op: 'in', v: ['T'] },
        { f: 'country', op: 'in', v: ['DE'] },
      ],
    })
  })

  it('returns the tree unchanged (kept, original op) when there are no toolbar conditions', () => {
    const tree = { g: 'or', c: [{ f: 'state', op: 'is_none', v: null }] }
    const merged = globalThis._gcfMergeToolbarConditions(tree, tree.c, [])
    expect(merged).toEqual({ g: 'or', c: [{ f: 'state', op: 'is_none', v: null }] })
  })
})

// ── _gcfSyncToolbarFromUrl — widgets always mirror the applied URL ───────────
// Bug: Reset (and chip removal) rewrote the URL/table in place but left the
// search box, elevation, radius, center location and saved-filter picker at
// their old values, so the box still showed text that no longer filtered
// anything — and the next submit / map refetch silently re-applied it.

describe('_gcfSyncToolbarFromUrl', () => {
  function setupToolbar() {
    document.body.innerHTML = `
      <form id="filter-form">
        <input id="id_q" name="q" value="">
        <select id="saved-filter-select">
          <option value="">— select —</option>
          <optgroup label="Built-in"><option value="1" data-saved-name="FTF" data-builtin="1">FTF</option></optgroup>
          <optgroup label="My filters"><option value="2" data-saved-name="Mine">Mine</option></optgroup>
        </select>
        <button type="button" id="saved-filter-delete-btn" class="d-none"></button>
        <select data-fx-enum="cache_type"><option value="">All</option><option value="Traditional">Traditional</option></select>
        <select data-fx-found><option value="">All</option><option value="1">Found</option><option value="0">Not found</option></select>
        <select data-fx-country><option value="">All</option><option value="DE">DE</option><option value="__none__">None</option></select>
        <select data-fx-tag><option value="">All</option><option value="hike">hike</option></select>
        <select data-fx-flag><option value="">All</option><option value="ftf">FTF</option><option value="ftf_possible">FTF possible</option></select>
        <select name="elevation"><option value="">All</option><option value="none">Not set</option><option value="gt3000">&gt; 3000 m</option></select>
        <select name="ref"><option value="1">A</option><option value="2" data-default>B</option><option value="3">C</option></select>
        <input name="radius" type="number" value="">
      </form>`
  }
  const $ = (sel) => document.querySelector(sel)

  function dirtyEverything() {
    $('#id_q').value = 'puzzle'
    $('#id_q').classList.add('filter-active')
    $('[name="elevation"]').value = 'gt3000'
    $('[name="radius"]').value = '25'
    $('[name="ref"]').value = '3'
    $('[data-fx-enum]').value = 'Traditional'
    $('[data-fx-found]').value = '1'
    $('[data-fx-country]').value = 'DE'
    $('[data-fx-tag]').value = 'hike'
    $('[data-fx-flag]').value = 'ftf'
    $('#saved-filter-select').value = '2'
    $('#saved-filter-delete-btn').classList.remove('d-none')
  }

  it('resets every widget to its default when the URL carries no filters (Reset)', () => {
    setupToolbar()
    dirtyEverything()
    globalThis._gcfSyncToolbarFromUrl('/caches/')
    expect($('#id_q').value).toBe('')
    expect($('#id_q').classList.contains('filter-active')).toBe(false)
    expect($('[name="elevation"]').value).toBe('')
    expect($('[name="radius"]').value).toBe('')
    expect($('[data-fx-enum]').value).toBe('')
    expect($('[data-fx-found]').value).toBe('')
    expect($('[data-fx-country]').value).toBe('')
    expect($('[data-fx-tag]').value).toBe('')
    expect($('[data-fx-flag]').value).toBe('')
    expect($('#saved-filter-select').value).toBe('')
    expect($('#saved-filter-delete-btn').classList.contains('d-none')).toBe(true)
  })

  it('leaves the search box alone when the URL still carries the same q (chip removal)', () => {
    setupToolbar()
    $('#id_q').value = 'puzzle'
    globalThis._gcfSyncToolbarFromUrl('/caches/?q=puzzle&ref=2')
    expect($('#id_q').value).toBe('puzzle')
  })

  it('takes search / elevation / radius / dropdowns from the URL when present', () => {
    setupToolbar()
    const fx = globalThis.gcfEncodeFx({
      g: 'and',
      c: [{ f: 'cache_type', op: 'in', v: ['Traditional'] }, { f: 'found', op: 'is_false', v: true }],
    })
    globalThis._gcfSyncToolbarFromUrl(`/caches/?q=hello&elevation=none&radius=12.5&fx=${fx}`)
    expect($('#id_q').value).toBe('hello')
    expect($('[name="elevation"]').value).toBe('none')
    expect($('[name="radius"]').value).toBe('12.5')
    expect($('[data-fx-enum]').value).toBe('Traditional')
    expect($('[data-fx-found]').value).toBe('0')
    expect($('[data-fx-country]').value).toBe('')
  })

  it('keeps an exotic ?flag= value selected (it never rides the fx tree)', () => {
    setupToolbar()
    globalThis._gcfSyncToolbarFromUrl('/caches/?flag=ftf_possible')
    expect($('[data-fx-flag]').value).toBe('ftf_possible')
  })

  it('falls back to the default ref point when ?ref= is absent or unknown', () => {
    setupToolbar()
    $('[name="ref"]').value = '3'
    globalThis._gcfSyncToolbarFromUrl('/caches/')
    expect($('[name="ref"]').value).toBe('2')

    $('[name="ref"]').value = '3'
    globalThis._gcfSyncToolbarFromUrl('/caches/?ref=999')
    expect($('[name="ref"]').value).toBe('2')
  })

  it('falls back to the first ref point when none is flagged default', () => {
    setupToolbar()
    $('[data-default]').removeAttribute('data-default')
    $('[name="ref"]').value = '3'
    globalThis._gcfSyncToolbarFromUrl('/caches/')
    expect($('[name="ref"]').value).toBe('1')
  })

  it('selects the ref point named in ?ref=', () => {
    setupToolbar()
    globalThis._gcfSyncToolbarFromUrl('/caches/?ref=3')
    expect($('[name="ref"]').value).toBe('3')
  })

  it('selects the saved filter named in ?f= and shows delete only for non-builtin ones', () => {
    setupToolbar()
    globalThis._gcfSyncToolbarFromUrl('/caches/?f=Mine')
    expect($('#saved-filter-select').value).toBe('2')
    expect($('#saved-filter-delete-btn').classList.contains('d-none')).toBe(false)
    expect(globalThis._savedFilterSelectedPk).toBe('2')

    globalThis._gcfSyncToolbarFromUrl('/caches/?f=FTF')
    expect($('#saved-filter-select').value).toBe('1')
    expect($('#saved-filter-delete-btn').classList.contains('d-none')).toBe(true)

    globalThis._gcfSyncToolbarFromUrl('/caches/?f=Deleted')
    expect($('#saved-filter-select').value).toBe('')
    expect(globalThis._savedFilterSelectedPk).toBeNull()
  })

  it('does not throw on a page without the optional widgets', () => {
    document.body.innerHTML = '<form id="filter-form"></form>'
    expect(() => globalThis._gcfSyncToolbarFromUrl('/caches/')).not.toThrow()
  })
})

// End-to-end for the reported scenario: the Reset link calls
// gcfApplyListChange('/caches/'), which must leave no stale widget behind.
describe('gcfApplyListChange — Reset clears the toolbar widgets', () => {
  afterEach(() => {
    delete window.htmx
  })

  it('empties the search box and dropdowns and swaps the table for the bare list URL', () => {
    stubLocation('http://localhost/caches/?q=puzzle&elevation=gt3000&fx=' +
      globalThis.gcfEncodeFx({ g: 'and', c: [{ f: 'country', op: 'in', v: ['DE'] }] }))
    document.body.innerHTML = `
      <form id="filter-form">
        <input id="id_q" name="q" value="puzzle">
        <select data-fx-country><option value="">All</option><option value="DE" selected>DE</option></select>
        <select name="elevation"><option value="">All</option><option value="gt3000" selected>&gt; 3000 m</option></select>
        <input type="hidden" name="fx" value="stale">
      </form>
      <div id="cache-table-container"></div>`
    const ajax = vi.fn(() => Promise.resolve())
    window.htmx = { ajax }

    globalThis.gcfApplyListChange('/caches/')

    expect(document.querySelector('#id_q').value).toBe('')
    expect(document.querySelector('[data-fx-country]').value).toBe('')
    expect(document.querySelector('[name="elevation"]').value).toBe('')
    expect(document.querySelector('[name="fx"]').value).toBe('')
    expect(ajax).toHaveBeenCalledWith('GET', '/caches/', expect.objectContaining({ target: '#cache-table-container' }))
  })
})

// ── gcfConfirmFullUpdate — templates/geocaches/partials/_action_bar.html ─────
// (WP-12: moved out of that partial's own inline <script> since it's
// hx-swap-oob'd on every filter/scope change.)

describe('gcfConfirmFullUpdate', () => {
  beforeEach(() => {
    globalThis.gettext = (s) => s
  })

  it('builds a confirm() message from el.dataset.count and returns its result', () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true)
    const el = document.createElement('a')
    el.dataset.count = '100'
    const result = globalThis.gcfConfirmFullUpdate(el)
    expect(result).toBe(true)
    const msg = confirmSpy.mock.calls[0][0]
    expect(msg).toContain('100')
    expect(msg).toContain('This may consume significant GC API quota. Proceed?')
    confirmSpy.mockRestore()
  })

  it('treats a missing data-count as 0', () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false)
    const el = document.createElement('a')
    expect(globalThis.gcfConfirmFullUpdate(el)).toBe(false)
    expect(confirmSpy.mock.calls[0][0]).toContain(' 0 ')
    confirmSpy.mockRestore()
  })
})
