/**
 * Smoke tests for static/js/gcf-common.js — the shared-helper consolidation
 * from WP-12's final commit (docs/architecture-review-2026-09-workplan.md).
 * Every other test file that evals a script depending on one of these
 * helpers loads gcf-common.js first; this file tests the helpers directly.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  const code = readFileSync(resolve(jsDir, 'gcf-common.js'), 'utf8')
  ;(0, eval)(code)
})

describe('gcf-common.js top-level functions', () => {
  const names = ['_gcfEsc', '_gcfFmtDistSafe', '_gcfFmtDistRound', '_gcfTbCsrf']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_gcfEsc', () => {
  it('escapes &, <, >, " and \'', () => {
    expect(globalThis._gcfEsc(`<b>"quoted" & 'tags'</b>`))
      .toBe('&lt;b&gt;&quot;quoted&quot; &amp; &#39;tags&#39;&lt;/b&gt;')
  })

  it('returns an empty string for null/undefined', () => {
    expect(globalThis._gcfEsc(null)).toBe('')
    expect(globalThis._gcfEsc(undefined)).toBe('')
  })

  it('stringifies non-string, non-nullish values (e.g. 0) instead of treating them as empty', () => {
    expect(globalThis._gcfEsc(0)).toBe('0')
  })
})

describe('_gcfFmtDistSafe', () => {
  it('formats sub-km distances as whole metres', () => {
    expect(globalThis._gcfFmtDistSafe(42)).toBe('42 m')
  })

  it('formats km distances with 2 decimals', () => {
    expect(globalThis._gcfFmtDistSafe(1500)).toBe('1.50 km')
  })

  it('returns an em dash for non-finite input', () => {
    expect(globalThis._gcfFmtDistSafe(NaN)).toBe('—')
    expect(globalThis._gcfFmtDistSafe(Infinity)).toBe('—')
  })
})

describe('_gcfFmtDistRound', () => {
  it('formats sub-km distances with Math.round', () => {
    expect(globalThis._gcfFmtDistRound(42.6)).toBe('43 m')
  })

  it('formats km distances with 2 decimals', () => {
    expect(globalThis._gcfFmtDistRound(1500)).toBe('1.50 km')
  })
})

describe('_gcfTbCsrf', () => {
  it('prefers the csrfmiddlewaretoken input over the cookie', () => {
    document.body.innerHTML = '<input name="csrfmiddlewaretoken" value="input-tok">'
    Object.defineProperty(document, 'cookie', { value: 'csrftoken=cookie-tok', configurable: true })
    expect(globalThis._gcfTbCsrf()).toBe('input-tok')
  })

  it('falls back to the csrftoken cookie when no input is present', () => {
    document.body.innerHTML = ''
    Object.defineProperty(document, 'cookie', { value: 'csrftoken=cookie-tok; other=x', configurable: true })
    expect(globalThis._gcfTbCsrf()).toBe('cookie-tok')
  })

  it('returns an empty string when neither is present', () => {
    document.body.innerHTML = ''
    Object.defineProperty(document, 'cookie', { value: '', configurable: true })
    expect(globalThis._gcfTbCsrf()).toBe('')
  })
})
