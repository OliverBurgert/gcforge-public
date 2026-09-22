/**
 * Smoke tests for static/js/log-image-dialog.js (extracted from
 * templates/geocaches/partials/_log_image_dialog.html — see
 * docs/architecture-review-2026-09-workplan.md WP-12).
 *
 * The script wires up event listeners on the dialog's own elements at
 * top level (no DOMContentLoaded wrapper — same as the inline <script> it
 * replaces, safe because the script tag comes right after the modal markup
 * in document order), so the DOM it expects is built before loading it.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const jsDir = resolve(__dirname, '..')

beforeAll(() => {
  globalThis.gettext = (s) => s
  document.body.innerHTML = `
    <div id="logImageDialogConfig" data-default-max-px="1024" data-default-strip-exif="true"></div>
    <div id="logImageModal">
      <input type="file" id="imgDlgFile">
      <div id="imgDlgFileInfo"></div>
      <input id="imgDlgTitle">
      <textarea id="imgDlgDesc"></textarea>
      <div id="imgDlgDescWrap"></div>
      <input type="checkbox" id="imgDlgSpoiler">
      <div id="imgDlgSpoilerWrap"></div>
      <select id="imgDlgRotate"><option value="0">0</option></select>
      <select id="imgDlgMaxPx"><option value="1024">1024</option></select>
      <input type="checkbox" id="imgDlgStripExif">
      <div id="imgDlgError"></div>
      <div id="imgDlgPreviewBox">
        <span id="imgDlgPreviewPlaceholder"></span>
        <img id="imgDlgPreview">
      </div>
      <button id="imgDlgAttach"></button>
    </div>
  `
  globalThis.bootstrap = { Modal: function () { this.show = () => {}; this.hide = () => {}; } }
  ;(0, eval)(readFileSync(resolve(jsDir, 'gcf-common.js'), 'utf8'))
  const code = readFileSync(resolve(jsDir, 'log-image-dialog.js'), 'utf8')
  ;(0, eval)(code)
})

describe('log-image-dialog.js public API', () => {
  const names = ['gcfImageOpen', 'gcfImageEdit', 'gcfImageRemove', 'gcfImageSetPlatforms']
  it.each(names)('%s is a function', (name) => {
    expect(typeof globalThis[name]).toBe('function')
  })
})

describe('gcfImageOpen / gcfImageSetPlatforms', () => {
  it('shows the description field for gc but not oc_de', () => {
    globalThis.gcfImageSetPlatforms(['gc'])
    globalThis.gcfImageOpen()
    expect(document.getElementById('imgDlgDescWrap').style.display).toBe('')
    expect(document.getElementById('imgDlgSpoilerWrap').style.display).toBe('none')
  })

  it('shows the spoiler field for oc_de', () => {
    globalThis.gcfImageSetPlatforms(['oc_de'])
    globalThis.gcfImageOpen()
    expect(document.getElementById('imgDlgSpoilerWrap').style.display).toBe('')
  })

  it('defaults the max-size field from config data attributes', () => {
    globalThis.gcfImageOpen()
    expect(document.getElementById('imgDlgMaxPx').value).toBe('1024')
    expect(document.getElementById('imgDlgStripExif').checked).toBe(true)
  })
})
