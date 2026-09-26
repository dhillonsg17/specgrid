# SpecGrid

Single-file landing page for SpecGrid's aftermarket catalog audit service.

## Files

- `index.html` — the complete page, including CSS, SVG icons, and lightweight JavaScript.
- `.gitignore` — excludes local files, secrets, and generated test artifacts.
- `docs/HEADLESS_MVP.md` — data contract, entity resolution, rule semantics, and implementation plan for the future local audit engine.

## Preview

Open `index.html` directly, or serve this directory:

```sh
python3 -m http.server 8080 --bind 127.0.0.1
```

Visit http://127.0.0.1:8080. No package installation or build is needed.

## Deployment

Serve `index.html` from any static host. Repository upload does not automatically publish a website or configure `specgrid.io`.

Tailwind's browser CDN and Google Fonts are loaded in the HTML head as requested. Custom CSS provides the page's core layout and styling even if these resources are unavailable. Tailwind describes its browser CDN as a development tool; a later production-hardening step can compile and inline the used utilities while retaining a single HTML file.

## Content and maintenance

- All audit CTAs open `mailto:hello@specgrid.io`. The mailbox must be configured separately.
- The audit preview contains clearly labeled fictional sample records, not customer results or a functioning scanner.
- The inline wheel-network mark is shared by the header, footer, and favicon. The sample constraint illustrates powertrain scope; the copy covers the wider aftermarket.
- There is no form, upload endpoint, analytics, or customer-data storage.
- Edit brand tokens in `:root` within the HTML. Sections are semantic and labeled for accessibility.
- Fonts fall back to system sans-serif and monospace. Motion respects the operating system's reduced-motion setting; content remains readable without JavaScript.
- The copyright year updates automatically, with a static fallback.

## Git workflow

Keep website source at the root of its dedicated repository. Use `codex/` branches and pull requests for subsequent changes. Keep screenshots, scratch scripts, dependency folders, and private catalog data out of the repository.
