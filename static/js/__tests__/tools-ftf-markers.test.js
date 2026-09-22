/**
 * Smoke tests for static/js/tools-ftf-markers.js (extracted from
 * templates/geocaches/tools/tools_ftf_markers.html — see
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
  document.body.innerHTML = ''
  const code = readFileSync(resolve(jsDir, 'tools-ftf-markers.js'), 'utf8')
  ;(0, eval)(code)
})

describe('tools-ftf-markers.js top-level functions', () => {
  const names = ['_ftfGetCsrf', '_ftfVerifyOne', 'gcfVerifySingleFTF', 'gcfVerifyAllFTF']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_ftfGetCsrf', () => {
  it('reads the csrfmiddlewaretoken input value', () => {
    document.body.innerHTML = '<input name="csrfmiddlewaretoken" value="tok">'
    expect(globalThis._ftfGetCsrf()).toBe('tok')
  })

  it('returns an empty string when the token input is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._ftfGetCsrf()).toBe('')
  })
})

describe('gcfVerifyAllFTF', () => {
  it('does nothing when there are no verify buttons', async () => {
    document.body.innerHTML = ''
    await expect(globalThis.gcfVerifyAllFTF()).resolves.toBeUndefined()
  })

  it('verifies each button in sequence and shows completion', async () => {
    document.body.innerHTML = `
      <button id="btn-verify-all"></button>
      <span id="verify-progress" class="d-none"></span>
      <span id="verify-done"></span>
      <span id="verify-total"></span>
      <span id="verify-complete" class="d-none"></span>
      <button class="ftf-verify-btn" data-pk="1" data-url="/verify/1/"></button>
      <div id="ftf-row-1"></div>
    `
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, text: () => Promise.resolve('<tr id="ftf-row-1"></tr>') }))
    await globalThis.gcfVerifyAllFTF()
    expect(document.getElementById('btn-verify-all').disabled).toBe(true)
    expect(document.getElementById('verify-complete').classList.contains('d-none')).toBe(false)
    expect(document.getElementById('btn-verify-all').textContent).toBe('Done')
  })
})
