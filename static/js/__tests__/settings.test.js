/**
 * Smoke tests for static/js/settings.js. The map-init IIFE at the bottom was
 * extracted from templates/preferences/settings.html's own inline <script>
 * as part of WP-12 (docs/architecture-review-2026-09-workplan.md); the rest
 * of the file predates that package.
 */

import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'settings.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('settings.js', () => {
  it('loads without throwing against an empty DOM', () => {
    document.body.innerHTML = ''
    globalThis.gettext = (s) => s
    expect(() => loadScript()).not.toThrow()
  })

  it('exposes gcfFetchPublicGuid', () => {
    expect(typeof globalThis.gcfFetchPublicGuid).toBe('function')
  })
})

describe('settings.js map init', () => {
  it('does not call gcfLoadMapLibre when #settings-config is absent', () => {
    document.body.innerHTML = ''
    globalThis.gettext = (s) => s
    globalThis.gcfLoadMapLibre = vi.fn()
    loadScript()
    expect(globalThis.gcfLoadMapLibre).not.toHaveBeenCalled()
  })

  it('calls gcfLoadMapLibre with the three script URLs from #settings-config', () => {
    document.body.innerHTML = `
      <div id="settings-config"
           data-map-styles-url="/static/js/map-styles.js"
           data-settings-map-url="/static/js/settings-map.js"
           data-offline-map-url="/static/js/offline-map.js"></div>
    `
    globalThis.gettext = (s) => s
    globalThis.gcfLoadMapLibre = vi.fn()
    loadScript()
    expect(globalThis.gcfLoadMapLibre).toHaveBeenCalledTimes(1)
    const opts = globalThis.gcfLoadMapLibre.mock.calls[0][0]
    expect(opts.scripts).toEqual([
      '/static/js/map-styles.js',
      '/static/js/settings-map.js',
      '/static/js/offline-map.js',
    ])
  })
})
