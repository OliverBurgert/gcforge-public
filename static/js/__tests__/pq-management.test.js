/**
 * Smoke tests for static/js/pq-management.js (extracted from
 * templates/geocaches/pq_management.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12). Indirect eval
 * promotes the file's top-level function declarations to globalThis, same
 * technique as smoke.test.js.
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
  const code = readFileSync(resolve(jsDir, 'pq-management.js'), 'utf8')
  ;(0, eval)(code)
})

describe('pq-management.js top-level functions', () => {
  const names = [
    'gcfPqInsertTag',
    'gcfPqCheckedCount',
    'gcfPqUpdateSelCount',
    'gcfPqBindRows',
    'gcfPqRequireSelection',
    'gcfPqConfirmDelete',
    'gcfPqShowMatching',
    'gcfPqSplitEsc',
    'gcfPqShowSplitPreview',
    'gcfPqExecuteSplit',
  ]
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('gcfPqSplitEsc', () => {
  it('escapes HTML-significant characters', () => {
    expect(globalThis.gcfPqSplitEsc('<b>"quoted" & tags</b>'))
      .toBe('&lt;b&gt;&quot;quoted&quot; &amp; tags&lt;/b&gt;')
  })
})

describe('_pqUrl', () => {
  it('returns an empty string when #pq-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._pqUrl('urlRowsJson')).toBe('')
  })
})

describe('gcfPqCheckedCount / gcfPqUpdateSelCount', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <input type="checkbox" id="pq-check-all">
      <span id="pq-sel-count"></span>
      <input type="checkbox" class="pq-row-check" checked>
      <input type="checkbox" class="pq-row-check">
    `
  })

  it('counts only checked rows', () => {
    expect(globalThis.gcfPqCheckedCount()).toBe(1)
  })

  it('updates the selection count label and master-checkbox indeterminate state', () => {
    globalThis.gcfPqUpdateSelCount()
    expect(document.getElementById('pq-sel-count').textContent).toBe('1 selected')
    expect(document.getElementById('pq-check-all').indeterminate).toBe(true)
  })
})

describe('gcfPqRequireSelection / gcfPqConfirmDelete', () => {
  beforeEach(() => {
    document.body.innerHTML = `<input type="checkbox" class="pq-row-check">`
  })

  it('gcfPqRequireSelection returns false and alerts when nothing is checked', () => {
    const alerts = []
    globalThis.alert = (msg) => alerts.push(msg)
    expect(globalThis.gcfPqRequireSelection()).toBe(false)
    expect(alerts).toEqual(['Select at least one pocket query first.'])
  })
})
