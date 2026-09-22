/**
 * Smoke tests for static/js/trackable-detail.js (extracted from
 * templates/geocaches/trackable_detail.html — see
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
  document.body.innerHTML = ''
  ;(0, eval)(readFileSync(resolve(jsDir, 'gcf-common.js'), 'utf8'))
  const code = readFileSync(resolve(jsDir, 'trackable-detail.js'), 'utf8')
  ;(0, eval)(code)
})

describe('trackable-detail.js top-level functions', () => {
  const names = [
    'gcfTbDetailRefresh', 'gcfTbDetailActionModalOpen', 'gcfTbDetailActionSubmit',
    'gcfTbDetailCoordsSubmit', 'gcfTbDetailCoordsClear', 'gcfTbDetailMapReady', '_gcfTbCsrf',
  ]
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_tbdUrl', () => {
  it('returns an empty string when #tb-detail-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._tbdUrl('refreshUrl')).toBe('')
  })
})

describe('_gcfTbCsrf', () => {
  it('reads the csrfmiddlewaretoken input value', () => {
    document.body.innerHTML = '<input name="csrfmiddlewaretoken" value="tok123">'
    expect(globalThis._gcfTbCsrf()).toBe('tok123')
  })

  it('returns an empty string when no token input is present', () => {
    document.body.innerHTML = ''
    expect(globalThis._gcfTbCsrf()).toBe('')
  })
})

describe('gcfTbDetailActionModalOpen', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <h6 id="actionModalLabel"></h6>
      <textarea id="action-text">stale</textarea>
      <div id="action-modal-error" class=""></div>
      <div id="action-modal-hint" class=""></div>
    `
  })

  it('resets the modal for the clicked action button', () => {
    const btn = document.createElement('button')
    btn.dataset.action = 'mark_missing'
    btn.dataset.label = 'Mark Missing'
    globalThis.gcfTbDetailActionModalOpen.call(btn)
    expect(document.getElementById('actionModalLabel').textContent).toBe('Mark Missing')
    expect(document.getElementById('action-text').value).toBe('')
    expect(document.getElementById('action-modal-error').classList.contains('d-none')).toBe(true)
    expect(document.getElementById('action-modal-hint').classList.contains('d-none')).toBe(true)
  })
})

describe('gcfTbDetailCoordsClear', () => {
  it('clears the lat/lon inputs and re-submits the coords form', () => {
    document.body.innerHTML = `
      <form id="coords-form">
        <input id="coords-lat" value="48.1">
        <input id="coords-lon" value="16.3">
      </form>
    `
    let submitted = false
    document.getElementById('coords-form').addEventListener('submit', (e) => {
      e.preventDefault()
      submitted = true
    })
    globalThis.gcfTbDetailCoordsClear()
    expect(document.getElementById('coords-lat').value).toBe('')
    expect(document.getElementById('coords-lon').value).toBe('')
    expect(submitted).toBe(true)
  })
})

describe('gcfTbDetailMapReady', () => {
  it('does not throw when there are no map-init globals or log rows', () => {
    document.body.innerHTML = ''
    expect(() => globalThis.gcfTbDetailMapReady()).not.toThrow()
  })
})
