/**
 * Tests for profile-switch.js — the "Switching to <name>..." reconnecting
 * page's polling helper (gcfProfileSwitchPoll). See
 * docs/multi-profile-plan.md §11 / R8.
 *
 * Real (not faked) timers with a tiny delayMs, matching the project's
 * existing async-test style (offline-map.test.js) rather than introducing
 * vi.useFakeTimers().
 */

import { describe, it, expect, afterEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

function loadScript(filename) {
  const code = readFileSync(resolve(jsDir, filename), 'utf8')
  ;(0, eval)(code)
}

loadScript('profile-switch.js')

afterEach(() => {
  document.body.innerHTML = ''
})

describe('gcfProfileSwitchPoll', () => {
  it('calls onReconnected as soon as fetch resolves', async () => {
    const fetchFn = () => Promise.resolve({ ok: true })
    await new Promise((res) => {
      globalThis.gcfProfileSwitchPoll({
        fetchFn,
        delayMs: 1,
        onReconnected: res,
        onGiveUp: () => { throw new Error('should not give up') },
      })
    })
  })

  it('retries on fetch rejection until it eventually succeeds', async () => {
    let calls = 0
    const fetchFn = () => {
      calls += 1
      return calls < 3 ? Promise.reject(new Error('connection refused')) : Promise.resolve({ ok: true })
    }
    await new Promise((res) => {
      globalThis.gcfProfileSwitchPoll({
        fetchFn,
        delayMs: 1,
        maxAttempts: 10,
        onReconnected: res,
        onGiveUp: () => { throw new Error('should not give up') },
      })
    })
    expect(calls).toBe(3)
  })

  it('gives up after the configured attempt limit without ever succeeding', async () => {
    const fetchFn = () => Promise.reject(new Error('connection refused'))
    const calls = []
    await new Promise((res) => {
      globalThis.gcfProfileSwitchPoll({
        fetchFn: (...args) => { calls.push(args); return fetchFn() },
        delayMs: 1,
        maxAttempts: 4,
        onReconnected: () => { throw new Error('should not reconnect') },
        onGiveUp: res,
      })
    })
    expect(calls.length).toBe(4)
  })

  it('default onGiveUp reveals the manual-link fallback element', async () => {
    document.body.innerHTML = '<a id="gcf-manual-link" style="display:none">manual link</a>'
    const fetchFn = () => Promise.reject(new Error('connection refused'))
    await new Promise((res) => {
      const poll = () => globalThis.gcfProfileSwitchPoll({ fetchFn, delayMs: 1, maxAttempts: 2 })
      // Wrap: default onGiveUp has no completion hook, so poll via a manual
      // attempt counter proxy — spy on the fallback element's style instead.
      poll()
      const check = () => {
        const el = document.getElementById('gcf-manual-link')
        if (el.style.display === '') {
          res()
        } else {
          setTimeout(check, 5)
        }
      }
      check()
    })
    expect(document.getElementById('gcf-manual-link').style.display).toBe('')
  })

  it('default onReconnected reloads the page', async () => {
    // jsdom's window.location.reload throws "Not implemented" and the
    // property isn't directly assignable — replace the whole object, same
    // pattern as cache-list-urls.test.js's stubLocation().
    const reloadSpy = { called: false }
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      value: { origin: originalLocation.origin, reload: () => { reloadSpy.called = true } },
      writable: true,
      configurable: true,
    })
    try {
      const fetchFn = () => Promise.resolve({ ok: true })
      await new Promise((res) => {
        // Can't await location.reload() directly (no callback), so poll the spy.
        globalThis.gcfProfileSwitchPoll({ fetchFn, delayMs: 1 })
        const check = () => (reloadSpy.called ? res() : setTimeout(check, 5))
        check()
      })
      expect(reloadSpy.called).toBe(true)
    } finally {
      Object.defineProperty(window, 'location', { value: originalLocation, writable: true, configurable: true })
    }
  })
})
