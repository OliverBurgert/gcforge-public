/**
 * Smoke tests for static/js/trackable-list.js (extracted from
 * templates/geocaches/trackable_list.html — see docs/architecture-review-2026-09-workplan.md
 * WP-12). Indirect eval promotes the file's top-level function declarations
 * to globalThis, same technique as smoke.test.js.
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  globalThis.gettext = (s) => s
  ;(0, eval)(readFileSync(resolve(jsDir, 'gcf-common.js'), 'utf8'))
  const code = readFileSync(resolve(jsDir, 'trackable-list.js'), 'utf8')
  ;(0, eval)(code)
})

describe('trackable-list.js top-level functions', () => {
  const names = [
    'gcfTbBulkSync',
    'gcfTbSyncTrackingCodes',
    'gcfTbSyncByCode',
    'gcfTbLogByCodeOpen',
    'gcfTbLogByCodeBack',
    'gcfTbLogByCodeRun',
    'gcfTbLogByCodeToggleSameText',
    'gcfTbResolveLocations',
    'gcfTbRefreshFiltered',
  ]
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_gcfTbLogCodeEsc', () => {
  it('escapes HTML-significant characters', () => {
    expect(globalThis._gcfTbLogCodeEsc('<b>"quoted" & tags</b>'))
      .toBe('&lt;b&gt;&quot;quoted&quot; &amp; tags&lt;/b&gt;')
  })

  it('handles null/undefined as empty string', () => {
    expect(globalThis._gcfTbLogCodeEsc(null)).toBe('')
    expect(globalThis._gcfTbLogCodeEsc(undefined)).toBe('')
  })
})

describe('_tbUrl', () => {
  it('returns an empty string when #tb-list-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._tbUrl('urlSyncPlan')).toBe('')
  })
})

describe('_gcfTbLogByCodeParse (via gcfTbLogByCodeRun DOM wiring)', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <textarea id="tb-logcode-codes"></textarea>
      <div id="tb-logcode-error" class="d-none"></div>
      <div id="tb-logcode-step1"></div>
      <div id="tb-logcode-step2" class="d-none"></div>
    `
  })

  it('parses comma/space/newline separated codes into a deduped list', () => {
    document.getElementById('tb-logcode-codes').value = 'abc123, DEF456\nabc123 ghi-789'
    expect(globalThis._gcfTbLogByCodeParse()).toEqual(['ABC123', 'DEF456', 'GHI', '789'])
  })
})
