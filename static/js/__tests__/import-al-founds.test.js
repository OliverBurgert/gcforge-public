/**
 * Smoke tests for static/js/import-al-founds.js (extracted from
 * templates/geocaches/import/import_al_founds.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  globalThis.gcfLoadMapLibre = () => {}
  globalThis.fetch = () => Promise.resolve({ json: () => Promise.resolve([]) })
  document.body.innerHTML = ''
  const code = readFileSync(resolve(jsDir, 'import-al-founds.js'), 'utf8')
  ;(0, eval)(code)
})

describe('import-al-founds.js top-level functions', () => {
  const names = ['gcfAlFoundsSubmitImport', 'gcfAlFoundsAfterSwap', '_alFoundsUrl']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_alFoundsUrl', () => {
  it('returns an empty string when #al-founds-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._alFoundsUrl('importUrl')).toBe('')
  })
})

describe('gcfAlFoundsSubmitImport', () => {
  it('alerts and does nothing when no items are checked', () => {
    document.body.innerHTML = ''
    const alertSpy = vi.spyOn(globalThis, 'alert').mockImplementation(() => {})
    globalThis.gcfAlFoundsSubmitImport()
    expect(alertSpy).toHaveBeenCalledWith('No Adventures selected.')
    alertSpy.mockRestore()
  })
})

describe('gcfAlFoundsAfterSwap', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="import-controls" style="display:none"></div>
      <div id="preview-area"></div>
    `
    delete window._gcfAlPendingImport
    delete window._gcfAlMap
  })

  it('reveals import-controls when the preview swap has checked items', () => {
    document.body.innerHTML += '<input class="al-item-check" type="checkbox">'
    globalThis.gcfAlFoundsAfterSwap({ detail: { target: document.getElementById('preview-area') } })
    expect(document.getElementById('import-controls').style.display).toBe('')
  })

  it('hides import-controls when the preview swap has no items', () => {
    globalThis.gcfAlFoundsAfterSwap({ detail: { target: document.getElementById('preview-area') } })
    expect(document.getElementById('import-controls').style.display).toBe('none')
  })

  it('does nothing when there is no pending import and the target is unrelated', () => {
    const other = document.createElement('div')
    other.id = 'import-status-area'
    document.body.appendChild(other)
    expect(() => globalThis.gcfAlFoundsAfterSwap({ detail: { target: other } })).not.toThrow()
  })
})
