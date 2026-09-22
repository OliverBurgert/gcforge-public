/**
 * Smoke tests for static/js/base.js (extracted from templates/base.html —
 * gcfSubmitScope, back-to-list logo restore, gcApiValidated indicator, and
 * the task-dock auto-reload delegated to templates/geocaches/partials/
 * _task_status.html's data-task-reload-* attributes). See
 * docs/architecture-review-2026-09-workplan.md WP-12.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

function loadScript() {
  const code = readFileSync(resolve(jsDir, 'base.js'), 'utf8')
  ;(0, eval)(code)
}

function stubReload() {
  let reloaded = false
  Object.defineProperty(window, 'location', {
    configurable: true, writable: true,
    value: { reload: () => { reloaded = true } },
  })
  return () => reloaded
}

describe('base.js top-level function', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    try { sessionStorage.clear() } catch (e) { /* ignore */ }
  })

  it('gcfSubmitScope is a function', () => {
    loadScript()
    expect(typeof globalThis.gcfSubmitScope).toBe('function')
  })
})

describe('task dock auto-reload', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <div id="task-status-dock"></div>
      <form id="filter-form"></form>
    `
    try { sessionStorage.clear() } catch (e) { /* ignore */ }
    loadScript()
  })

  it('reloads once for a non-Preview/Sync failed task, guarded by sessionStorage', () => {
    const wasReloaded = stubReload()
    const dock = document.getElementById('task-status-dock')
    dock.innerHTML = '<div data-task-reload-id="42" data-task-reload-name="Import GPX"></div>'
    document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: dock } }))
    expect(wasReloaded()).toBe(true)
    expect(sessionStorage.getItem('gcf_reloaded_42')).toBe('1')
  })

  it('does not reload for a task whose name starts with "Preview " or "Sync "', () => {
    const wasReloaded = stubReload()
    const dock = document.getElementById('task-status-dock')
    dock.innerHTML = '<div data-task-reload-id="43" data-task-reload-name="Preview import"></div>'
    document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: dock } }))
    expect(wasReloaded()).toBe(false)
  })

  it('ignores swaps whose target is not the task dock', () => {
    const wasReloaded = stubReload()
    const other = document.createElement('div')
    other.id = 'cache-table-container'
    document.body.appendChild(other)
    document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: other } }))
    expect(wasReloaded()).toBe(false)
  })
})
