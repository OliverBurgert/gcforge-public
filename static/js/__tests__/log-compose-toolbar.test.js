/**
 * Tests for the log-compose toolbar string-building helpers.
 *
 * These live in an inline <script> in
 *   templates/geocaches/partials/_log_compose_toolbar.html
 * (an IIFE that exposes window.gcfFmt* and operates on a textarea). There is
 * no .js file to load, so we read the template, extract the <script> body,
 * strip the Django template tags ({% trans %}, {% url %}) down to plain
 * strings, and indirect-eval it — the same promotion-to-global pattern as
 * smoke.test.js, just sourced from a template.
 *
 * The helpers are pure DOM string-builders on a jsdom <textarea>:
 *   - gcfFmtWrap          wrap selection / insert markers at cursor
 *   - gcfFmtInsert        insert literal text at cursor (smiley picker)
 *   - gcfFmtInsertLine    insert text on its own line, blank line before (HR)
 *   - gcfFmtLines         prefix each selected line (bullets / quote)
 *   - gcfFmtNumbered      number each selected line
 *   - gcfFmtInsertTemplate insert a template body, substituting [find_count]
 *
 * gcfFmtInsertTemplate mirrors geocaches/log_format.py::expand_placeholders,
 * which intentionally leaves [find_count] for the client to fill from the
 * "Find #" (sequence_number) input — see the docstring there. We pin that
 * client-side substitution here.
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(__dirname, '..', '..', '..')

// Read a template, pull out its inline <script> body, neutralise the Django
// template tags that appear inside JS string literals, and indirect-eval it
// so the IIFE's window.* assignments land on globalThis (= window in jsdom).
function loadTemplateScript(relPath) {
  let html = readFileSync(resolve(repoRoot, relPath), 'utf8')
  const m = html.match(/<script>([\s\S]*?)<\/script>/)
  if (!m) throw new Error('no <script> block in ' + relPath)
  let code = m[1]
  // {% trans "X" %} / {% trans 'X' %}  → the literal X (these sit inside JS
  // string quotes in the template, so dropping the tag leaves a valid string).
  code = code.replace(/\{%\s*trans\s+(["'])(.*?)\1\s*%\}/g, '$2')
  // {% url 'name' %} → a dummy path (only used inside fetch() we never call).
  code = code.replace(/\{%\s*url\s+[^%]*?%\}/g, '/_test_url/')
  ;(0, eval)(code)
}

beforeAll(() => {
  loadTemplateScript('templates/geocaches/partials/_log_compose_toolbar.html')
})

// Build a toolbar + textarea, set selection, return the textarea.
function makeTextarea({ value = '', start = 0, end = start } = {}) {
  document.body.innerHTML = `
    <div data-textarea-id="logFormText"></div>
    <textarea id="logFormText"></textarea>`
  const ta = document.getElementById('logFormText')
  ta.value = value
  ta.selectionStart = start
  ta.selectionEnd = end
  // The toolbar resolves the textarea via _ta(), which walks up from the
  // click source or `event.currentTarget`. With no event in scope it falls
  // back to getElementById('logFormText') — exactly the textarea we built.
  return ta
}

// ── gcfFmtWrap ───────────────────────────────────────────────────────────────

describe('gcfFmtWrap', () => {
  it('wraps the current selection and keeps the inner text selected', () => {
    const ta = makeTextarea({ value: 'hello world', start: 6, end: 11 }) // "world"
    globalThis.gcfFmtWrap('**', '**')
    expect(ta.value).toBe('hello **world**')
    // Selection brackets the inner text (between the markers).
    expect(ta.value.substring(ta.selectionStart, ta.selectionEnd)).toBe('world')
  })

  it('inserts empty markers at the cursor when there is no selection', () => {
    const ta = makeTextarea({ value: 'ab', start: 1, end: 1 })
    globalThis.gcfFmtWrap('*', '*')
    expect(ta.value).toBe('a**b')
    // Cursor sits between the two markers, ready to type.
    expect(ta.selectionStart).toBe(2)
    expect(ta.selectionEnd).toBe(2)
  })
})

// ── gcfFmtInsert ─────────────────────────────────────────────────────────────

describe('gcfFmtInsert', () => {
  it('inserts literal text at the cursor', () => {
    const ta = makeTextarea({ value: 'Nice cache ', start: 11, end: 11 })
    globalThis.gcfFmtInsert('😊')
    expect(ta.value).toBe('Nice cache 😊')
  })

  it('replaces the current selection with the inserted text', () => {
    const ta = makeTextarea({ value: 'aXXb', start: 1, end: 3 })
    globalThis.gcfFmtInsert('Y')
    expect(ta.value).toBe('aYb')
  })
})

// ── gcfFmtInsertLine — blank line guaranteed before the inserted line ─────────

describe('gcfFmtInsertLine', () => {
  it('at the very start of an empty textarea, no leading newlines', () => {
    const ta = makeTextarea({ value: '', start: 0, end: 0 })
    globalThis.gcfFmtInsertLine('---')
    expect(ta.value).toBe('---')
  })

  it('after text with no trailing newline, inserts a blank line (\\n\\n) before', () => {
    const ta = makeTextarea({ value: 'prev', start: 4, end: 4 })
    globalThis.gcfFmtInsertLine('---')
    expect(ta.value).toBe('prev\n\n---')
  })

  it('after a single trailing newline, adds just one more to make a blank line', () => {
    const ta = makeTextarea({ value: 'prev\n', start: 5, end: 5 })
    globalThis.gcfFmtInsertLine('---')
    expect(ta.value).toBe('prev\n\n---')
  })

  it('after an existing blank line, adds no extra leading newline', () => {
    const ta = makeTextarea({ value: 'prev\n\n', start: 6, end: 6 })
    globalThis.gcfFmtInsertLine('---')
    expect(ta.value).toBe('prev\n\n---')
  })

  it('adds a trailing newline when text follows the insertion point', () => {
    const ta = makeTextarea({ value: 'prevnext', start: 4, end: 4 })
    globalThis.gcfFmtInsertLine('---')
    expect(ta.value).toBe('prev\n\n---\nnext')
  })
})

// ── gcfFmtLines — prefix each line, blank line before the block ───────────────

describe('gcfFmtLines', () => {
  it('prefixes a single line in place (no selection) with a leading blank line', () => {
    const ta = makeTextarea({ value: 'item one', start: 0, end: 0 })
    globalThis.gcfFmtLines('* ')
    // lineStart === 0 → no blank line needed.
    expect(ta.value).toBe('* item one')
  })

  it('prefixes every selected line', () => {
    const ta = makeTextarea({ value: 'a\nb\nc', start: 0, end: 5 })
    globalThis.gcfFmtLines('* ')
    expect(ta.value).toBe('* a\n* b\n* c')
  })

  it('inserts a blank line before the block when preceded by text', () => {
    // "intro\nitem" — cursor on the "item" line (offset 6).
    const ta = makeTextarea({ value: 'intro\nitem', start: 6, end: 6 })
    globalThis.gcfFmtLines('> ')
    expect(ta.value).toBe('intro\n\n> item')
  })

  it('leaves empty lines unprefixed', () => {
    const ta = makeTextarea({ value: 'a\n\nb', start: 0, end: 4 })
    globalThis.gcfFmtLines('* ')
    expect(ta.value).toBe('* a\n\n* b')
  })
})

// ── gcfFmtNumbered ───────────────────────────────────────────────────────────

describe('gcfFmtNumbered', () => {
  it('numbers each selected non-empty line sequentially', () => {
    const ta = makeTextarea({ value: 'a\nb\nc', start: 0, end: 5 })
    globalThis.gcfFmtNumbered()
    expect(ta.value).toBe('1. a\n2. b\n3. c')
  })

  it('skips blank lines without advancing the counter', () => {
    const ta = makeTextarea({ value: 'a\n\nb', start: 0, end: 4 })
    globalThis.gcfFmtNumbered()
    expect(ta.value).toBe('1. a\n\n2. b')
  })

  it('inserts a blank line before the block when preceded by text', () => {
    const ta = makeTextarea({ value: 'intro\nx\ny', start: 6, end: 9 })
    globalThis.gcfFmtNumbered()
    expect(ta.value).toBe('intro\n\n1. x\n2. y')
  })
})

// ── gcfFmtInsertTemplate — [find_count] client-side substitution ─────────────
// Mirrors geocaches/log_format.py::expand_placeholders, which deliberately
// leaves [find_count] for the client to fill from the "Find #" input.

describe('gcfFmtInsertTemplate', () => {
  function setupWithSeq(seqValue) {
    document.body.innerHTML = `
      <div data-textarea-id="logFormText"></div>
      <textarea id="logFormText"></textarea>
      <input name="sequence_number" value="${seqValue}">`
    const ta = document.getElementById('logFormText')
    ta.selectionStart = ta.selectionEnd = 0
    return ta
  }

  it('substitutes [find_count] with the current sequence_number value', () => {
    const ta = setupWithSeq('42')
    globalThis.gcfFmtInsertTemplate('My #[find_count] find!')
    expect(ta.value).toBe('My #42 find!')
  })

  it('replaces every occurrence of [find_count] in the body', () => {
    const ta = setupWithSeq('7')
    globalThis.gcfFmtInsertTemplate('[find_count] / [find_count]')
    expect(ta.value).toBe('7 / 7')
  })

  it('substitutes an empty string when the Find # input is blank', () => {
    const ta = setupWithSeq('')
    globalThis.gcfFmtInsertTemplate('Find ##[find_count] done')
    expect(ta.value).toBe('Find ## done')
  })

  it('substitutes an empty string when there is no sequence_number input', () => {
    document.body.innerHTML = `
      <div data-textarea-id="logFormText"></div>
      <textarea id="logFormText"></textarea>`
    const ta = document.getElementById('logFormText')
    ta.selectionStart = ta.selectionEnd = 0
    globalThis.gcfFmtInsertTemplate('n=[find_count]')
    expect(ta.value).toBe('n=')
  })

  it('leaves other (server-expanded) placeholders untouched', () => {
    // expand_placeholders handles [name]/[gc_code]/… server-side; the client
    // only knows [find_count]. An un-expanded [name] passes through verbatim.
    const ta = setupWithSeq('3')
    globalThis.gcfFmtInsertTemplate('TFTC [name]! find [find_count]')
    expect(ta.value).toBe('TFTC [name]! find 3')
  })

  it('inserts the body at the cursor, preserving surrounding text', () => {
    document.body.innerHTML = `
      <div data-textarea-id="logFormText"></div>
      <textarea id="logFormText"></textarea>
      <input name="sequence_number" value="1">`
    const ta = document.getElementById('logFormText')
    ta.value = 'AB'
    ta.selectionStart = ta.selectionEnd = 1
    globalThis.gcfFmtInsertTemplate('-[find_count]-')
    expect(ta.value).toBe('A-1-B')
  })
})
