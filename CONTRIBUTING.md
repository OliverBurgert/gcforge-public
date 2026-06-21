# Contributing to GCForge

Thank you for your interest in contributing. GCForge is an early-stage project and outside contributions are welcome — especially bug fixes, usability improvements, and translations.

---

## Before you start

**For questions, ideas, or bug reports** open a [Discussion](https://github.com/OliverBurgert/gcforge-public/discussions) rather than a PR. It avoids wasted effort if a change doesn't fit the project's direction.

**For confirmed bugs** you can open an Issue directly with reproduction steps.

**CLA:** Before your first PR is merged you will be asked to agree to the [Contributor License Agreement](CLA.md). You retain copyright; the CLA gives the maintainer the right to use and relicense your contribution. Just add this comment to your PR:

> I have read and agree to the GCForge Contributor License Agreement.

---

## Development setup

Requires **Python 3.14+** and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/OliverBurgert/gcforge-public.git
cd gcforge
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000`. The admin is at `/admin/` — create a superuser with `uv run python manage.py createsuperuser`.

Enable the pre-commit hook (runs linting, Django checks, JS tests, and i18n validation):

```bash
git config core.hooksPath .githooks
```

---

## Making changes

**Branch from `main`, one feature or fix per branch.**

Commit style — [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: short description
fix: short description
chore: short description
docs: short description
```

### Code style

- Linting: `uv run ruff check .` — auto-fix with `uv run ruff check --fix .`
- No unnecessary comments, no over-engineering. Match the scope of the change.
- Don't add features, abstractions, or error handling beyond what the fix requires.

### Tests

- JS smoke tests: `npm run test:js`
- The pre-commit hook runs all checks automatically. Do not use `--no-verify`.

### Translations

GCForge ships in English and German (and other languages in the Future). If your change adds or modifies any user-facing string:

- Wrap it with the appropriate Django i18n helper (`_()`, `{% trans %}`, `gettext()`, etc.)
- Add the German translation in `locale/de/LC_MESSAGES/django.po` in the same commit — don't leave `msgstr` empty.
- If German (or future other languages) are not your native language, ask a LLM for a suggestion and mark it as fuzzy.
- After editing `.po` files, recompile: `uv run python manage.py compilemessages`

See [`docs/localization.md`](docs/localization.md) for the full workflow.

---

## Submitting a pull request

1. Make sure `uv run ruff check .` and `uv run python manage.py check` both pass
2. Run `npm run test:js` if you touched any JavaScript
3. Push your branch and open a PR against `main`
4. Describe what the change does and why — link to the relevant Issue or Discussion
5. Add the CLA agreement comment if it's your first contribution

The maintainer reviews PRs on a best-effort basis. Small, focused PRs are merged much faster than large ones.

---

## What's in scope

- Bug fixes
- Usability improvements to existing features
- Performance improvements
- Translations — see below for details
- Documentation fixes

## Translators wanted

GCForge currently ships in English and German. Geocaching is a global hobby and we would genuinely love to support more languages. If you speak a language well enough to translate UI strings, your help is very welcome.

To add a new language:

1. Open a [Discussion](https://github.com/OliverBurgert/gcforge-public/discussions) to let us know — we'll create the locale skeleton for you
2. Once the `.po` file exists, translate the `msgstr` entries and open a PR
3. You don't need to know Django or Python to contribute a translation — the `.po` format is plain text
4. It is ok to start with an automated translation, but before we officially add a language, we want a competent human for fine-tuning and error checking.

If you'd like to maintain a language long-term (keeping up with new strings as the app develops) please mention that in your Discussion post — it's more valuable than a one-off translation.

---

## What's out of scope (for now)

- Live Geocaching.com API integration — we currently don't have an API key
- Major new features without prior discussion — open an idea thread first so we can agree on approach

---

## Project layout

A quick map of the codebase:

| Path           | Contents                                                  |
|----------------|-----------------------------------------------------------|
| `geocaches/`   | Core app — models, views, services, importers, exporters  |
| `accounts/`    | User accounts and platform credentials                    |
| `preferences/` | Settings, reference points, export presets, log templates |
| `static/js/`   | Frontend JavaScript (HTMX + vanilla JS)                   |
| `templates/`   | Django templates                                          |
| `docs/`        | Planning and reference documents                          |

The architecture is described in more detail in [`docs/PLANNING.md`](docs/PLANNING.md).

---

## Questions

Open a [Discussion](https://github.com/OliverBurgert/gcforge-public/discussions) — it's the right place for anything that isn't a clear bug.
