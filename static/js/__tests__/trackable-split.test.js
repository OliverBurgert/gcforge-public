/**
 * Smoke tests for static/js/trackable-split.js (extracted from
 * templates/geocaches/trackable_split.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeEach(() => {
  document.body.innerHTML = ''
  globalThis.htmx = { ajax: vi.fn() }
  const code = readFileSync(resolve(jsDir, 'trackable-split.js'), 'utf8')
  ;(0, eval)(code)
})

describe('trackable-split.js top-level function', () => {
  it('exposes _gcfTbSplitBindRows', () => {
    expect(typeof globalThis._gcfTbSplitBindRows).toBe('function')
  })
})

describe('_gcfTbSplitBindRows', () => {
  it('binds a click handler that triggers htmx.ajax with the row link href', () => {
    document.body.innerHTML = `
      <div id="tb-table-container">
        <table><tbody><tr><td><a class="cache-name-link" href="/GC123/">GC123</a></td></tr></tbody></table>
      </div>
    `
    globalThis._gcfTbSplitBindRows()
    const tr = document.querySelector('tr')
    expect(tr.dataset.tbBound).toBe('1')
    tr.dispatchEvent(new Event('click', { bubbles: true }))
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1)
    const [method, url, opts] = globalThis.htmx.ajax.mock.calls[0]
    expect(method).toBe('GET')
    expect(url).toContain('/GC123/?embed=1')
    expect(opts).toEqual({
      target: '#tb-detail-pane',
      select: '#tb-detail-content',
      swap: 'innerHTML',
    })
  })

  it('does not double-bind an already-bound row', () => {
    document.body.innerHTML = `
      <div id="tb-table-container">
        <table><tbody><tr data-tb-bound="1"><td><a class="cache-name-link" href="/GC123/">GC123</a></td></tr></tbody></table>
      </div>
    `
    expect(() => globalThis._gcfTbSplitBindRows()).not.toThrow()
  })

  it('skips rows without a cache-name-link', () => {
    document.body.innerHTML = '<div id="tb-table-container"><table><tbody><tr><td>no link</td></tr></tbody></table></div>'
    expect(() => globalThis._gcfTbSplitBindRows()).not.toThrow()
  })
})
