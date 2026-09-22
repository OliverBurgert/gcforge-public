/**
 * Smoke tests for static/js/trackable-map-page.js (extracted from
 * templates/geocaches/trackable_map.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  globalThis.gettext = (s) => s
  ;(0, eval)(readFileSync(resolve(jsDir, 'gcf-common.js'), 'utf8'))
})

function loadScript() {
  const code = readFileSync(resolve(jsDir, 'trackable-map-page.js'), 'utf8')
  ;(0, eval)(code)
}

describe('trackable-map-page.js top-level functions', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    globalThis.gcfLoadMapLibre = vi.fn()
  })

  const names = ['gcfTbMapResolveLocations', '_gcfTbCsrf', '_tbMapUrl']
  it.each(names)('%s is a function', (name) => {
    loadScript()
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_tbMapUrl', () => {
  it('returns an empty string when #tb-map-config is absent', () => {
    document.body.innerHTML = ''
    globalThis.gcfLoadMapLibre = vi.fn()
    loadScript()
    expect(globalThis._tbMapUrl('resolveUrl')).toBe('')
  })
})

describe('_gcfTbCsrf', () => {
  it('reads the csrftoken cookie', () => {
    document.body.innerHTML = ''
    globalThis.gcfLoadMapLibre = vi.fn()
    Object.defineProperty(document, 'cookie', { value: 'csrftoken=abc123; other=x', configurable: true })
    loadScript()
    expect(globalThis._gcfTbCsrf()).toBe('abc123')
  })
})

describe('search input debounce', () => {
  it('submits the filter form after the debounce delay', () => {
    vi.useFakeTimers()
    document.body.innerHTML = `
      <form id="tb-map-filter-form"></form>
      <input id="tb-map-q">
    `
    globalThis.gcfLoadMapLibre = vi.fn()
    const form = document.getElementById('tb-map-filter-form')
    form.submit = vi.fn()
    loadScript()
    document.getElementById('tb-map-q').dispatchEvent(new Event('input'))
    vi.advanceTimersByTime(400)
    expect(form.submit).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})

describe('gcfTbMapResolveLocations', () => {
  it('disables the button, calls fetch, and shows a resolved status', async () => {
    document.body.innerHTML = `
      <button id="resolve-loc-btn"></button>
      <span id="resolve-loc-status"></span>
    `
    globalThis.gcfLoadMapLibre = vi.fn()
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ ok: true, resolved: 2, total_missing: 5 }) }))
    loadScript()
    globalThis.gcfTbMapResolveLocations()
    expect(document.getElementById('resolve-loc-btn').disabled).toBe(true)
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))
    expect(document.getElementById('resolve-loc-status').textContent).toBe('Resolved 2 of 5.')
  })
})
