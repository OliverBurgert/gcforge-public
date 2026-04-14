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

Head to the [Releases page](https://github.com/GCForge/gcforge/releases) and download the installer for your platform:

| Platform | File                      |
|----------|---------------------------|
| Windows  | `GCForge-Setup-x.y.z.exe` |
| Linux    | `GCForge-x.y.z.AppImage`  |
| macOS    | `GCForge-x.y.z.dmg`       |

Run the installer, launch GCForge from your Start Menu / Applications, and your browser will open to the app automatically.

Full install instructions: [gcforge.spazierenmitziel.online/docs/getting-started.html](https://gcforge.spazierenmitziel.online/docs/getting-started.html)

---

## Getting started

1. **Install** using the installer above
2. **Import your caches** — from a GPX/Pocket Query, a GSAK `.db3` file, or an Adventure Lab export
3. **Set a home location** — used for distance and bearing calculations
4. **Filter and explore** your collection in list or map view

See the [importing guide](https://gcforge.spazierenmitziel.online/docs/importing.html) for step-by-step instructions.

---

## Feedback & community

This is an early release aimed at a small test group. Feedback is genuinely valuable at this stage — it directly influences what gets prioritised next.

**[GitHub Discussions](https://github.com/GCForge/gcforge/discussions)** is the place for:

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
git clone https://github.com/GCForge/gcforge.git
cd gcforge
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

Then open `http://127.0.0.1:8000` in your browser.
