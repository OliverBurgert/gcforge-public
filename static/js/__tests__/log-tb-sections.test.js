/**
 * Smoke tests for static/js/log-tb-sections.js (extracted from
 * templates/geocaches/partials/_log_tb_sections.html — see
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
  const code = readFileSync(resolve(jsDir, 'log-tb-sections.js'), 'utf8')
  ;(0, eval)(code)
})

describe('log-tb-sections.js top-level functions', () => {
  const names = ['gcfTbToggle', 'gcfTbInvToggle', '_gcfTbSummaryGlobal', '_gcfTbSerialiseGlobal']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_gcfEsc', () => {
  it('escapes HTML-significant characters', () => {
    expect(globalThis._gcfEsc('<b>"quoted" & tags</b>'))
      .toBe('&lt;b&gt;&quot;quoted&quot; &amp; tags&lt;/b&gt;')
  })

  it('returns empty string for null/undefined', () => {
    expect(globalThis._gcfEsc(null)).toBe('')
    expect(globalThis._gcfEsc(undefined)).toBe('')
  })
})

describe('_tbSecUrl', () => {
  it('returns an empty string when #tb-sections-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._tbSecUrl('urlVerify')).toBe('')
  })
})

describe('gcfTbToggle / _gcfTbSummaryGlobal', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <span id="tb-summary"></span>
      <div id="tb-sections">
        <div class="tb-row" data-tb-row="cache">
          <input type="radio" name="r" value="" checked>
          <input type="radio" name="r" value="discover">
          <div class="tb-mini" style="display:none"></div>
        </div>
      </div>
    `
  })

  it('shows the mini panel and updates the summary count when a non-empty action is picked', () => {
    const row = document.querySelector('.tb-row')
    const discoverRadio = row.querySelectorAll('input[type=radio]')[1]
    discoverRadio.checked = true
    globalThis.gcfTbToggle(discoverRadio)
    expect(row.querySelector('.tb-mini').style.display).toBe('')
    expect(document.getElementById('tb-summary').textContent).toBe('(1 selected)')
  })

  it('hides the mini panel and clears the summary when the empty action is picked', () => {
    const row = document.querySelector('.tb-row')
    const radios = row.querySelectorAll('input[type=radio]')
    globalThis.gcfTbToggle(radios[1])
    radios[0].checked = true
    globalThis.gcfTbToggle(radios[0])
    expect(row.querySelector('.tb-mini').style.display).toBe('none')
    expect(document.getElementById('tb-summary').textContent).toBe('')
  })
})

describe('_gcfTbSerialiseGlobal', () => {
  it('emits tb_action/tb_ref/tb_text hidden fields for a checked cache-row radio', () => {
    document.body.innerHTML = `
      <form id="f">
        <div id="tb-sections">
          <div class="tb-row" data-tb-row="cache" data-tb-ref="TB123">
            <input type="radio" checked value="discover">
          </div>
        </div>
      </form>
    `
    const form = document.getElementById('f')
    globalThis._gcfTbSerialiseGlobal(form)
    expect(form.querySelector('[name="tb_action_0"]').value).toBe('discover')
    expect(form.querySelector('[name="tb_ref_0"]').value).toBe('TB123')
  })
})
