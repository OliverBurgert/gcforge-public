/**
 * Smoke tests for static/js/ignore-lists-import-gsak.js (extracted from
 * templates/geocaches/tools/ignore_lists_import_gsak.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'ignore-lists-import-gsak.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('ignore-lists-import-gsak.js', () => {
  it('does nothing when #gsak_check_all is absent', () => {
    document.body.innerHTML = ''
    expect(() => loadScript()).not.toThrow()
  })

  it('checking "check all" checks every gsak-db-check box', () => {
    document.body.innerHTML = `
      <input type="checkbox" id="gsak_check_all">
      <input type="checkbox" class="gsak-db-check">
      <input type="checkbox" class="gsak-db-check">
    `
    loadScript()
    const checkAll = document.getElementById('gsak_check_all')
    checkAll.checked = true
    checkAll.dispatchEvent(new Event('change'))
    document.querySelectorAll('.gsak-db-check').forEach((cb) => expect(cb.checked).toBe(true))
  })

  it('unchecking one db box unchecks "check all"', () => {
    document.body.innerHTML = `
      <input type="checkbox" id="gsak_check_all" checked>
      <input type="checkbox" class="gsak-db-check" checked>
      <input type="checkbox" class="gsak-db-check" checked>
    `
    loadScript()
    const boxes = document.querySelectorAll('.gsak-db-check')
    boxes[0].checked = false
    boxes[0].dispatchEvent(new Event('change'))
    expect(document.getElementById('gsak_check_all').checked).toBe(false)
  })

  it('checking the last unchecked db box re-checks "check all"', () => {
    document.body.innerHTML = `
      <input type="checkbox" id="gsak_check_all">
      <input type="checkbox" class="gsak-db-check" checked>
      <input type="checkbox" class="gsak-db-check">
    `
    loadScript()
    const boxes = document.querySelectorAll('.gsak-db-check')
    boxes[1].checked = true
    boxes[1].dispatchEvent(new Event('change'))
    expect(document.getElementById('gsak_check_all').checked).toBe(true)
  })
})
