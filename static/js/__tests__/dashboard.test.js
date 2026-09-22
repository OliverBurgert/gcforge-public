/**
 * Smoke tests for static/js/dashboard.js (extracted from
 * templates/geocaches/dashboard.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  document.body.innerHTML = '<div id="dashboard-tabs"></div>'
  globalThis.htmx = { trigger: () => {} }
  const code = readFileSync(resolve(jsDir, 'dashboard.js'), 'utf8')
  ;(0, eval)(code)
})

describe('dashboard.js top-level functions', () => {
  const names = ['gcfDashMinChange', 'gcfDashMissing', 'gcfCopyCalUrl', 'gcfShowHome', 'gcfCalAddMissing']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('_dashUrl', () => {
  it('returns an empty string when #dashboard-config is absent', () => {
    document.body.innerHTML = ''
    expect(globalThis._dashUrl('urlMissing')).toBe('')
  })
})

describe('gcfDashMinChange', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <table id="heatmap"><tbody><tr>
        <td class="gcf-cell" data-count="5" data-color="rgb(1,1,1)" style=""></td>
        <td class="gcf-cell" data-count="1" data-color="rgb(2,2,2)" style=""></td>
      </tr></tbody></table>
    `
  })

  it('whites out cells below the minimum and restores their color at min=1', () => {
    globalThis.gcfDashMinChange('heatmap', '3')
    const cells = document.querySelectorAll('td.gcf-cell')
    expect(cells[0].style.background).toBe('rgb(1, 1, 1)')
    expect(cells[1].style.background).toBe('rgb(255, 255, 255)')

    globalThis.gcfDashMinChange('heatmap', '1')
    expect(cells[1].style.background).toBe('rgb(2, 2, 2)')
  })

  it('does nothing when the section is missing', () => {
    expect(() => globalThis.gcfDashMinChange('does-not-exist', '3')).not.toThrow()
  })
})

describe('gcfDashMissing', () => {
  // _dashUrl reads #dashboard-config once at script-load time (same pattern
  // as _pqUrl/_tbUrl in the other extracted files), so this test can't see a
  // populated config — it only pins the which/stat_type/minimum query string
  // that gets appended after whatever _dashUrl('urlMissing') returns.
  it('builds which/stat_type/minimum query params onto the missing-caches URL', () => {
    document.body.innerHTML = `
      <select id="dash-stat-type"><option value="traditional" selected>t</option></select>
      <select id="min-sel"><option value="2" selected>2</option></select>
    `
    delete window.location
    window.location = { href: '' }
    globalThis.gcfDashMissing('all', 'min-sel')
    expect(window.location.href.endsWith('?which=all&stat_type=traditional&minimum=2')).toBe(true)
  })
})
