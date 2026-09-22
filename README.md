# GCForge

**Forge your geocaching workflow.**

GCForge is an open-source geocache management tool — the spiritual successor to GSAK (Geocaching Swiss Army Knife), built with modern architecture. It runs locally on your machine as a desktop application.

> **Early beta.** This is a young project. Expect rough edges, missing features, and the occasional bug. Your feedback shapes what gets built next.

---

## What it does

- Import caches from **GPX / Pocket Queries**, **GSAK databases**, and **Adventure Lab exports**
- Filter, sort, tag, and manage your cache collection
- View caches on an interactive **map** with draw-to-filter support
- Enrich caches with **elevation** and **location** data
- Export to GPX
- Supports both **Geocaching.com** (GC codes) and **Opencaching.de** (OC codes) caches

**Not yet included:** GPS device transfer, scripting/macros, Geocaching.com live API (Opencaching API is supported), offline maps, mobile app.

---

## Download

Head to the [Releases page](https://github.com/OliverBurgert/gcforge-public/releases) and download the archive for your platform:

| Platform                   | File                   |
|----------------------------|------------------------|
| Windows (64-bit)           | `GCForge-windows.zip`  |
| Linux (64-bit)             | `GCForge-linux.tar.gz` |
| macOS (Apple Silicon)      | `GCForge-macos.zip`    |

Extract the archive and run the `GCForge` executable (`GCForge.exe` on Windows). The app starts a local server and opens your browser automatically. Your data lives in `~/.gcforge/`.

- **Windows:** if SmartScreen warns ("Windows protected your PC"), click **More info → Run anyway**.
- **macOS:** the app isn't Apple-signed yet, so macOS quarantines it. Open **Terminal**, `cd` into the extracted `GCForge` folder, run `xattr -dr com.apple.quarantine .` once, then `./GCForge`.

Full install instructions: [gcforge.spazierenmitziel.online/docs/install.html](https://gcforge.spazierenmitziel.online/docs/install.html)

---

## Getting started

1. **Extract and run** the app using the archive above
2. **Import your caches** — from a GPX/Pocket Query, a GSAK `.db3` file, or an Adventure Lab export
3. **Set a home location** — used for distance and bearing calculations
4. **Filter and explore** your collection in list or map view

See the [importing guide](https://gcforge.spazierenmitziel.online/docs/importing.html) for step-by-step instructions.

---

## Feedback & community

This is an early release aimed at a small test group. Feedback is genuinely valuable at this stage — it directly influences what gets prioritised next.

**[GitHub Discussions](https://github.com/OliverBurgert/gcforge-public/discussions)** is the place for:

- Questions and how-tos
- Bug reports and unexpected behaviour
- Feature ideas and workflow suggestions

If you find a bug: a brief description of what you did, what you expected, and what happened instead is all that's needed. Screenshots welcome.

---

## License

Licensed under the [Apache License 2.0](LICENSE).

Contributions are welcome. Before your first pull request is merged, you will be asked to agree to the [Contributor License Agreement](CLA.md) — a short statement that you grant the maintainer the right to use your contribution.

---

## Building from source

Requires Python 3.14+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/OliverBurgert/gcforge-public.git
cd gcforge
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

Then open `http://127.0.0.1:8000` in your browser.
