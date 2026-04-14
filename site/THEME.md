# GCForge Website Theme Proposal

## Concept: "Cave to Landscape"

The visual identity draws from the idea of emerging from a cave into a vibrant,
green landscape — mirroring the project's goal of opening up geocaching management
from a dark, complex tool (GSAK) into something welcoming and modern.

---

## Color Palette

### Primary Colors

| Name           | Hex       | Usage                              |
|----------------|-----------|------------------------------------|
| Cave Black     | `#1a1a2e` | Hero background, dark sections     |
| Cave Charcoal  | `#16213e` | Secondary dark backgrounds         |
| Forest Green   | `#2d6a4f` | Primary brand color, buttons       |
| Canopy Green   | `#40916c` | Secondary green, hover states      |
| Spring Green   | `#52b788` | Highlights, active states          |
| Meadow Light   | `#b7e4c7` | Light backgrounds, tints           |
| Mint Cream     | `#d8f3dc` | Page backgrounds, cards            |

### Accent Colors

| Name           | Hex       | Usage                              |
|----------------|-----------|------------------------------------|
| Amber Glow     | `#e09f3e` | CTA buttons, warnings, treasure    |
| Terracotta     | `#9e5a3c` | Earth accents, secondary CTAs      |
| Cache Blue     | `#3a86a8` | Links, info callouts               |

### Neutrals

| Name           | Hex       | Usage                              |
|----------------|-----------|------------------------------------|
| Stone White    | `#f8f9fa` | Main background                    |
| Pebble Gray    | `#e9ecef` | Borders, dividers                  |
| Slate          | `#6c757d` | Muted text                         |
| Charcoal       | `#343a40` | Body text                          |
| Ink            | `#212529` | Headings                           |

---

## Typography

### Font Stack

- **Headings:** `"Inter", "Segoe UI", system-ui, sans-serif` — clean, modern
- **Body:** `"Inter", "Segoe UI", system-ui, sans-serif`
- **Code/Technical:** `"JetBrains Mono", "Fira Code", monospace`

### Scale

| Element   | Size     | Weight |
|-----------|----------|--------|
| H1        | 3rem     | 700    |
| H2        | 2rem     | 700    |
| H3        | 1.5rem   | 600    |
| H4        | 1.25rem  | 600    |
| Body      | 1rem     | 400    |
| Small     | 0.875rem | 400    |

---

## Visual Motifs

### Cave Portal (Hero Section)

- Dark cave frame using CSS gradients and shadows
- Text and content "emerges" from the cave into light
- No actual image needed — achieved with pure CSS

### Topographic Lines

- Subtle repeating SVG pattern of contour lines
- Used as section backgrounds at low opacity (5-8%)
- Reinforces map/outdoor theme

### Terrain Gradients

- Sections transition from dark (cave) to green (landscape) to light (meadow)
- Creates visual journey as user scrolls

---

## Component Style

### Buttons

- Rounded corners (`border-radius: 8px`)
- Primary: Forest Green bg, white text, subtle box-shadow
- Secondary: Outline style with Cache Blue border
- Amber Glow for download/CTA buttons

### Cards

- White background, subtle border (`#e9ecef`)
- Light shadow on hover
- 12px border-radius

### Navigation

- Sticky top bar, semi-transparent dark (Cave Black at 95%)
- White text, green accent on active/hover
- Logo left, nav links right

---

## Page Sections (Landing Page)

1. **Hero** — Cave-portal visual, title, tagline, CTA buttons
2. **Features** — Grid of key capabilities with icons
3. **Screenshots** — Gallery with lightbox
4. **Download** — Platform-specific download cards
5. **Documentation** — Quick links to guides
6. **Footer** — Links, license, repo, credits

---

## Placeholder Assets

Until real assets are created:

- **Logo:** Text-based logo with styled `<span>` elements
- **Hero illustration:** CSS-only cave portal effect
- **Screenshots:** Placeholders with descriptive labels
- **Icons:** Unicode/emoji or inline SVG path icons

---

## Inspiration References

- GitHub's own docs site (clean, functional)
- Notion's landing page (gradient + illustration)
- Obsidian's site (dark theme, tool-focused)
- Geocaching.com (green brand, outdoor feel)
