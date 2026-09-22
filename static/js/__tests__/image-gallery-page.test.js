/**
 * Smoke tests for static/js/image-gallery-page.js (extracted from
 * templates/geocaches/tools/image_gallery_page.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'image-gallery-page.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('image-gallery-page.js top-level functions', () => {
  it('exposes gcfGalleryPollTask and gcfGalleryInitMaps', () => {
    document.body.innerHTML = ''
    loadScript()
    expect(typeof globalThis.gcfGalleryPollTask).toBe('function')
    expect(typeof globalThis.gcfGalleryInitMaps).toBe('function')
  })
})

describe('gcfGalleryPollTask', () => {
  it('does nothing when #gallery-poll is absent', () => {
    document.body.innerHTML = ''
    expect(() => loadScript()).not.toThrow()
  })

  it('polls the status URL from data-status-url and reloads when the task finishes', async () => {
    vi.useFakeTimers()
    document.body.innerHTML = '<div id="gallery-poll" data-status-url="/status/1/"></div>'
    globalThis.fetch = vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ state: 'completed' }) }))
    delete window.location
    window.location = { reload: vi.fn() }

    loadScript()
    await vi.advanceTimersByTimeAsync(1500)
    await Promise.resolve()
    await Promise.resolve()

    expect(globalThis.fetch).toHaveBeenCalledWith('/status/1/')
    expect(window.location.reload).toHaveBeenCalled()
    vi.useRealTimers()
  })
})

describe('gcfGalleryInitMaps', () => {
  it('does nothing when there are no [data-gallery-map] elements', () => {
    document.body.innerHTML = ''
    globalThis.gcfLoadMapLibre = vi.fn()
    loadScript()
    expect(globalThis.gcfLoadMapLibre).not.toHaveBeenCalled()
  })

  it('does nothing when gcfLoadMapLibre is unavailable', () => {
    document.body.innerHTML = '<div data-gallery-map data-lat="48" data-lon="9" data-zoom="13"></div>'
    delete globalThis.gcfLoadMapLibre
    expect(() => loadScript()).not.toThrow()
  })

  it('calls gcfLoadMapLibre when map placeholders exist', () => {
    document.body.innerHTML = '<div data-gallery-map data-lat="48" data-lon="9" data-zoom="13"></div>'
    globalThis.gcfLoadMapLibre = vi.fn()
    loadScript()
    expect(globalThis.gcfLoadMapLibre).toHaveBeenCalledTimes(1)
  })
})
