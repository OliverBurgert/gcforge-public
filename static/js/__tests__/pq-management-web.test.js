/**
 * Smoke tests for static/js/pq-management-web.js (extracted from
 * templates/geocaches/pq_management_web.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'pq-management-web.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('pq-management-web.js top-level functions', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve([]) }))
  })

  const names = ['gcfPqWebWireTagSuggestions', '_pqWebUrl']
  it.each(names)('%s is a function', (name) => {
    loadScript()
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_pqWebUrl', () => {
  it('returns an empty string when #pq-web-config is absent', () => {
    document.body.innerHTML = ''
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve([]) }))
    loadScript()
    expect(globalThis._pqWebUrl('tagsUrl')).toBe('')
  })
})

describe('gcfPqWebWireTagSuggestions', () => {
  it('adds a clickable chip per name that appends to the input value', () => {
    document.body.innerHTML = `
      <input id="tags-in" value="">
      <div id="tags-box"></div>
    `
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve([]) }))
    loadScript()
    globalThis.gcfPqWebWireTagSuggestions('tags-in', 'tags-box', ['alpha', 'beta'])
    const box = document.getElementById('tags-box')
    expect(box.children.length).toBe(2)
    box.children[0].dispatchEvent(new Event('click'))
    expect(document.getElementById('tags-in').value).toBe('alpha')
    box.children[1].dispatchEvent(new Event('click'))
    expect(document.getElementById('tags-in').value).toBe('alpha, beta')
  })

  it('does nothing when the input or box is missing', () => {
    document.body.innerHTML = ''
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve([]) }))
    loadScript()
    expect(() => globalThis.gcfPqWebWireTagSuggestions('missing-in', 'missing-box', ['x'])).not.toThrow()
  })
})

describe('page load', () => {
  it('fetches the tags URL from config and wires both tag inputs', async () => {
    document.body.innerHTML = `
      <div id="pq-web-config" data-tags-url="/tags/json/"></div>
      <input id="pq-web-ready-tags-input"><div id="pq-web-ready-tag-suggestions"></div>
      <input id="pq-web-active-tags-input"><div id="pq-web-active-tag-suggestions"></div>
    `
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve(['t1']) }))
    loadScript()
    expect(globalThis.fetch).toHaveBeenCalledWith('/tags/json/')
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))
    expect(document.getElementById('pq-web-ready-tag-suggestions').children.length).toBe(1)
    expect(document.getElementById('pq-web-active-tag-suggestions').children.length).toBe(1)
  })
})
