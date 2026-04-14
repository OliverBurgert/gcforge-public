# Assets Needed

This file tracks visual assets needed for the website. Until real assets are created,
the site uses CSS-only effects and placeholder text.

## Priority: High

### Screenshots (replace placeholders in index.html)
- [ ] Cache list view — main table with sorting/filtering
- [ ] Map view — interactive map with drawn search areas
- [  ] Cache detail panel — full cache info, logs, waypoints
- [ ] Import/progress — background task progress UI

**Guidelines:**
- Resolution: at least 1200px wide
- Format: PNG or WebP
- Show realistic data (no personally identifiable info)
- Dark theme screenshots preferred (matches site theme)

### Logo
- [ ] Primary logo (full text + icon)
- [ ] Icon only (favicon, app icon)
- [ ] Dark background variant
- [ ] Light background variant

**Concept:** Anvil + pickaxe/cave motif, or clean typographic logo.
See THEME.md for visual direction.

## Priority: Medium

### Hero Illustration
- [ ] Cave-to-landscape illustration for hero section
- [ ] Alternative: animated SVG of the cave portal concept

### Social Preview / OG Image
- [ ] Open Graph image (1200x630px) for social media sharing
- [ ] Should include logo, tagline, and key visual

### Feature Icons
- [ ] Replace emoji placeholders with custom SVG icons
- [ ] Style: outlined, monochrome green or white
- [ ] Set of 6 icons matching feature cards

## Priority: Low

### Decorative Elements
- [ ] Topographic line SVG pattern (currently CSS-generated)
- [ ] Cave rock texture for dark sections
- [ ] Subtle background patterns

### Documentation
- [ ] Diagram: GSAK → GCForge data migration flow
- [ ] Diagram: Architecture overview
- [ ] Screenshots for doc pages

## File Naming Convention

```
img/
  logo/
    logo-full-dark.svg
    logo-full-light.svg
    logo-icon.svg
    favicon.ico
  screenshots/
    cache-list.png
    map-view.png
    cache-detail.png
    import-progress.png
  hero/
    cave-hero.svg
    cave-hero.png
  social/
    og-image.png
  icons/
    feature-import.svg
    feature-tags.svg
    feature-map.svg
    feature-filter.svg
    feature-migrate.svg
    feature-plugins.svg
```

## Design Notes

- All icons should work at 24px, 48px, and 96px sizes
- Screenshots should be cropped to 16:10 aspect ratio
- Use consistent drop shadows on screenshots
- Maintain the "cave to landscape" theme — dark frames, green accents
