/**
 * Smoke tests for static/js/import-gsak-locations.js (extracted from
 * templates/geocaches/import/import_gsak_locations.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'import-gsak-locations.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('import-gsak-locations.js', () => {
  it('does nothing when #check-all is absent', () => {
    document.body.innerHTML = ''
    expect(() => loadScript()).not.toThrow()
  })

  it('toggles every .loc-check to match "check all"', () => {
    document.body.innerHTML = `
      <input type="checkbox" id="check-all">
      <input type="checkbox" class="loc-check" checked>
      <input type="checkbox" class="loc-check">
    `
    loadScript()
    const checkAll = document.getElementById('check-all')
    checkAll.checked = false
    checkAll.dispatchEvent(new Event('change'))
    document.querySelectorAll('.loc-check').forEach((cb) => expect(cb.checked).toBe(false))

    checkAll.checked = true
    checkAll.dispatchEvent(new Event('change'))
    document.querySelectorAll('.loc-check').forEach((cb) => expect(cb.checked).toBe(true))
  })
})
