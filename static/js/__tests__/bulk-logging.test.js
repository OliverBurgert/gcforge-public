/**
 * Smoke tests for static/js/bulk-logging.js (extracted from
 * templates/geocaches/tools/bulk_logging.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  globalThis.gettext = (s) => s
  document.body.innerHTML = ''
  const code = readFileSync(resolve(jsDir, 'bulk-logging.js'), 'utf8')
  ;(0, eval)(code)
})

describe('bulk-logging.js top-level functions', () => {
  const names = ['gcfBulkDetailFrameLoad', 'gcfBulkSubmitNow', 'gcfBulkLogTypeChange', '_bulkNoteUrl']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_bulkCfgVal', () => {
  it('returns an empty string when #bulk-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._bulkCfgVal('tab')).toBe('')
  })
})

describe('_bulkNoteUrl', () => {
  it('builds a tab/note query string from the module-level tab', () => {
    expect(globalThis._bulkNoteUrl(42)).toBe('?tab=' + globalThis._bulkTab + '&note=42')
  })
})

describe('gcfBulkDetailFrameLoad', () => {
  it('ignores the first (initial) load and reloads on subsequent loads', () => {
    globalThis._bulkIframeInitialLoad = true
    let reloaded = false
    const originalLocation = window.location
    delete window.location
    window.location = { reload: () => { reloaded = true } }

    globalThis.gcfBulkDetailFrameLoad()
    expect(reloaded).toBe(false)
    expect(globalThis._bulkIframeInitialLoad).toBe(false)

    globalThis.gcfBulkDetailFrameLoad()
    expect(reloaded).toBe(true)

    window.location = originalLocation
  })
})

describe('gcfBulkLogTypeChange', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div class="note-row active">
        <span data-role="log-type-text">Found it · 2026-01-01 10:00</span>
      </div>
    `
  })

  it('replaces the log-type portion of the active row, keeping the date', () => {
    const select = { value: 'Write note' }
    globalThis.gcfBulkLogTypeChange.call(select)
    expect(document.querySelector('[data-role="log-type-text"]').textContent)
      .toBe('Write note · 2026-01-01 10:00')
  })

  it('does nothing when there is no active row', () => {
    document.body.innerHTML = ''
    expect(() => globalThis.gcfBulkLogTypeChange.call({ value: 'x' })).not.toThrow()
  })
})
