/**
 * Smoke tests for static/js/import-fieldnotes.js (extracted from
 * templates/geocaches/import/import_fieldnotes.html — see
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
  globalThis.interpolate = (fmt, args) => fmt.replace('%s', args[0])
  document.body.innerHTML = ''
  const code = readFileSync(resolve(jsDir, 'import-fieldnotes.js'), 'utf8')
  ;(0, eval)(code)
})

describe('import-fieldnotes.js top-level functions', () => {
  const names = ['gcfFieldnotesPathChange', 'gcfFieldnotesFetchMissing', '_fnUrl']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_fnUrl', () => {
  it('returns an empty string when #fieldnotes-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._fnUrl('mapSyncUrl')).toBe('')
  })
})

describe('gcfFieldnotesPathChange', () => {
  it('submits the import-file form when the path is non-empty', () => {
    document.body.innerHTML = '<form id="form-import-file"></form>'
    const form = document.getElementById('form-import-file')
    let submitted = false
    form.submit = () => { submitted = true }
    globalThis.gcfFieldnotesPathChange.call({ value: '  C:\\notes.txt  ' })
    expect(submitted).toBe(true)
  })

  it('does not submit when the path is blank', () => {
    document.body.innerHTML = '<form id="form-import-file"></form>'
    const form = document.getElementById('form-import-file')
    let submitted = false
    form.submit = () => { submitted = true }
    globalThis.gcfFieldnotesPathChange.call({ value: '   ' })
    expect(submitted).toBe(false)
  })
})
