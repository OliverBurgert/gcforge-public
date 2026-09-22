/**
 * Smoke tests for static/js/import-form.js (the submit-spinner wiring
 * predates WP-12; the tag-suggestion chips were extracted from
 * templates/geocaches/partials/_import_base.html as part of it — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'import-form.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
  document.dispatchEvent(new Event('DOMContentLoaded'))
}

describe('import-form submit spinner', () => {
  it('disables the submit button and shows the spinner on submit', () => {
    document.body.innerHTML = `
      <form id="import-form">
        <button type="submit" class="import-btn"></button>
        <span class="import-spinner d-none"></span>
      </form>
    `
    loadScript()
    document.getElementById('import-form').dispatchEvent(new Event('submit'))
    expect(document.querySelector('.import-btn').disabled).toBe(true)
    expect(document.querySelector('.import-spinner').classList.contains('d-none')).toBe(false)
  })

  it('does nothing when #import-form is absent', () => {
    document.body.innerHTML = ''
    expect(() => loadScript()).not.toThrow()
  })
})

describe('import tag suggestions', () => {
  it('does nothing when the tags input/box or config is absent', () => {
    document.body.innerHTML = ''
    expect(() => loadScript()).not.toThrow()
  })

  it('fetches the config tags URL and adds a clickable chip per name', async () => {
    document.body.innerHTML = `
      <div id="import-base-config" data-tags-url="/tags/json/"></div>
      <input id="import-tags-input" value="">
      <div id="import-tag-suggestions"></div>
    `
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve(['alpha']) }))
    loadScript()
    expect(globalThis.fetch).toHaveBeenCalledWith('/tags/json/')
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))
    const box = document.getElementById('import-tag-suggestions')
    expect(box.children.length).toBe(1)
    box.children[0].dispatchEvent(new Event('click'))
    expect(document.getElementById('import-tags-input').value).toBe('alpha')
  })
})
