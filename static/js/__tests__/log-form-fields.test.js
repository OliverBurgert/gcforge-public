/**
 * Smoke tests for static/js/log-form-fields.js (extracted from
 * templates/geocaches/partials/_log_form_fields.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  document.body.innerHTML = ''
  const code = readFileSync(resolve(jsDir, 'log-form-fields.js'), 'utf8')
  ;(0, eval)(code)
})

describe('log-form-fields.js top-level functions', () => {
  const names = ['gcfInsertPassphrase', 'gcfInsertFieldNote']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('gcfInsertPassphrase', () => {
  it('fills and focuses the passphrase input when present', () => {
    document.body.innerHTML = '<input id="logFormPassphrase">'
    globalThis.gcfInsertPassphrase('sesame')
    expect(document.getElementById('logFormPassphrase').value).toBe('sesame')
  })

  it('does nothing when the input is absent', () => {
    document.body.innerHTML = ''
    expect(() => globalThis.gcfInsertPassphrase('sesame')).not.toThrow()
  })
})

describe('gcfInsertFieldNote', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <form>
        <select name="log_type">
          <option value="Found it">Found it</option>
          <option value="Didn't find it">DNF</option>
        </select>
        <input name="logged_at">
        <textarea id="logFormText"></textarea>
      </form>
    `
  })

  it('inserts text at the cursor and updates log type + date when given', () => {
    const ta = document.getElementById('logFormText')
    ta.value = 'before after'
    ta.selectionStart = ta.selectionEnd = 7
    globalThis.gcfInsertFieldNote('X', "Didn't find it", '2026-01-01T10:00')
    expect(ta.value).toBe('before Xafter')
    expect(document.querySelector('select[name="log_type"]').value).toBe("Didn't find it")
    expect(document.querySelector('input[name="logged_at"]').value).toBe('2026-01-01T10:00')
  })

  it('leaves log type and date untouched when not given', () => {
    const ta = document.getElementById('logFormText')
    ta.value = ''
    ta.selectionStart = ta.selectionEnd = 0
    globalThis.gcfInsertFieldNote('hi')
    expect(ta.value).toBe('hi')
    expect(document.querySelector('select[name="log_type"]').value).toBe('Found it')
    expect(document.querySelector('input[name="logged_at"]').value).toBe('')
  })
})
