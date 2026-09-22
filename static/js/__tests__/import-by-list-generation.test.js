/**
 * Smoke tests for static/js/import-by-list-generation.js (extracted from
 * templates/geocaches/import/import_by_list_generation.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'import-by-list-generation.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('import-by-list-generation.js', () => {
  it('does nothing when the tags input/box or config is absent', () => {
    document.body.innerHTML = ''
    expect(() => loadScript()).not.toThrow()
  })

  it('fetches the config tags URL and adds a clickable chip per name', async () => {
    document.body.innerHTML = `
      <div id="list-gen-config" data-tags-url="/tags/json/"></div>
      <input id="list-gen-tags-input" value="">
      <div id="list-gen-tag-suggestions"></div>
    `
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve(['alpha']) }))
    loadScript()
    expect(globalThis.fetch).toHaveBeenCalledWith('/tags/json/')
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))
    const box = document.getElementById('list-gen-tag-suggestions')
    expect(box.children.length).toBe(1)
    box.children[0].dispatchEvent(new Event('click'))
    expect(document.getElementById('list-gen-tags-input').value).toBe('alpha')
  })
})
