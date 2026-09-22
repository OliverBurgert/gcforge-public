/**
 * Smoke tests for static/js/notifications.js (extracted from
 * templates/geocaches/notifications.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')
const code = readFileSync(resolve(jsDir, 'notifications.js'), 'utf8')

function loadScript() {
  ;(0, eval)(code)
}

describe('notifications.js', () => {
  it('does not throw when neither modal is present', () => {
    document.body.innerHTML = ''
    expect(() => loadScript()).not.toThrow()
  })

  it('populates the edit modal fields from the triggering button\'s data-* attributes', () => {
    document.body.innerHTML = `
      <div id="editModal" data-edit-url-template="/notifications/0/edit/">
        <form>
          <input name="name"><input name="radius_km"><input name="latitude">
          <input name="longitude"><input name="recipient_email">
          <select name="location_id"><option value=""></option><option value="7">Loc 7</option></select>
          <input type="checkbox" name="log_event_ids" value="1">
          <input type="checkbox" name="log_event_ids" value="2">
        </form>
      </div>
    `
    loadScript()
    const editModal = document.getElementById('editModal')
    const btn = document.createElement('button')
    btn.dataset.pk = '42'
    btn.dataset.name = 'My cache'
    btn.dataset.radius = '10'
    btn.dataset.latitude = '48.1'
    btn.dataset.longitude = '9.2'
    btn.dataset.recipient = 'a@b.com'
    btn.dataset.locationId = '7'
    btn.dataset.logEvents = '1, 2'

    const evt = new Event('show.bs.modal')
    evt.relatedTarget = btn
    editModal.dispatchEvent(evt)

    expect(editModal.querySelector('form').action).toContain('/notifications/42/edit/')
    expect(editModal.querySelector('[name=name]').value).toBe('My cache')
    expect(editModal.querySelector('[name=radius_km]').value).toBe('10')
    expect(editModal.querySelector('[name=latitude]').value).toBe('48.1')
    expect(editModal.querySelector('[name=longitude]').value).toBe('9.2')
    expect(editModal.querySelector('[name=recipient_email]').value).toBe('a@b.com')
    expect(editModal.querySelector('[name=location_id]').value).toBe('7')
    const checkboxes = editModal.querySelectorAll('[name=log_event_ids]')
    expect(checkboxes[0].checked).toBe(true)
    expect(checkboxes[1].checked).toBe(true)
  })

  it('pre-fills bulk-create lat/lon when a location is picked', () => {
    document.body.innerHTML = `
      <div id="bulkCreateModal">
        <select name="location_id">
          <option value=""></option>
          <option value="3" data-lat="48.5" data-lon="9.5">Loc 3</option>
        </select>
        <input name="latitude"><input name="longitude">
      </div>
    `
    loadScript()
    const sel = document.querySelector('#bulkCreateModal [name=location_id]')
    sel.value = '3'
    sel.dispatchEvent(new Event('change'))
    expect(document.querySelector('#bulkCreateModal [name=latitude]').value).toBe('48.5')
    expect(document.querySelector('#bulkCreateModal [name=longitude]').value).toBe('9.5')
  })

  it('pre-fills the OC tab (#pane-oc) per-card form lat/lon when a location is picked', () => {
    document.body.innerHTML = `
      <div id="pane-oc">
        <form>
          <select name="location_id">
            <option value=""></option>
            <option value="9" data-lat="1.1" data-lon="2.2">Loc 9</option>
          </select>
          <input name="latitude"><input name="longitude">
        </form>
      </div>
    `
    loadScript()
    const sel = document.querySelector('#pane-oc [name=location_id]')
    sel.value = '9'
    sel.dispatchEvent(new Event('change'))
    expect(document.querySelector('#pane-oc [name=latitude]').value).toBe('1.1')
    expect(document.querySelector('#pane-oc [name=longitude]').value).toBe('2.2')
  })
})
