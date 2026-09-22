/**
 * Smoke tests for static/js/user-profile.js (extracted from
 * templates/preferences/user_profile.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'user-profile.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('user-profile.js top-level functions', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    loadScript()
  })

  const names = ['_gcfUserProfileCsrf', '_gcfUserProfileSaveAutoVisit', 'gcfUserProfileInitAutoVisit']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_gcfUserProfileCsrf', () => {
  it('reads the csrfmiddlewaretoken input value', () => {
    document.body.innerHTML = '<input name="csrfmiddlewaretoken" value="tok">'
    loadScript()
    expect(globalThis._gcfUserProfileCsrf()).toBe('tok')
  })
})

describe('gcfUserProfileInitAutoVisit', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <input name="csrfmiddlewaretoken" value="tok">
      <div data-tb-auto-save-url="/save/">
        <div class="tb-inv-edit" data-ref="TB123">
          <input type="checkbox" data-tb-auto-enabled>
          <div data-tb-auto-text-wrap style="display:none">
            <textarea data-tb-auto-text></textarea>
            <span data-tb-auto-status></span>
            <button data-tb-auto-save></button>
          </div>
        </div>
      </div>
    `
  })

  it('toggles the text wrap visibility and saves when the checkbox changes', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }))
    loadScript()
    const cb = document.querySelector('[data-tb-auto-enabled]')
    const wrap = document.querySelector('[data-tb-auto-text-wrap]')
    cb.checked = true
    cb.dispatchEvent(new Event('change'))
    expect(wrap.style.display).toBe('')
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    expect(globalThis.fetch.mock.calls[0][0]).toBe('/save/')
  })

  it('saves when the Save button is clicked', () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }))
    loadScript()
    document.querySelector('[data-tb-auto-save]').dispatchEvent(new Event('click'))
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })
})
