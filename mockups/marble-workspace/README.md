# Office Hours — marble workspace study

A restrained second-round mockup retaining the **actual Office Hours light/dark palette, blue accent, marble images, ASCII wordmark, Inter and JetBrains Mono**. This is not a rebrand, landing page, or production implementation.

## Preview locally

```sh
python -m http.server 8022 --directory mockups/marble-workspace --bind 0.0.0.0
```

Open `index.html`, `topic.html`, or `reader.html`. Use `?theme=light` or `?theme=dark` for a fixed state; the header theme control also remembers your choice. The standalone directory contains copies of the repository’s original marble JPGs. Fonts use Google Fonts with local system fallbacks.

## What changed

- **Home:** a working research desk rather than a promotional hero; a direct search field, continuing research threads, last reading position, and a note in context.
- **Topic:** numbered source references live in a reading margin beside the report. Papers, notes and tracked searches are separate views, without losing the collection context.
- **Reader:** source text, marks and conversation remain next to one another. Clicking a source jumps to the highlighted passage; focus mode removes the chat when it is not needed.
- **Visuals:** warm paper and charcoal surfaces from `static/css/variables.css`; the exact original `marble-light.jpg` and `marble-dark.jpg`; restrained blue selection marks instead of a new accent system. Marble is a desk behind the content, never behind body text.

## Screenshots

Captured at 1440 px wide with fonts loaded. Home and topic use full-page captures; the reader is shown at a 1440 × 1100 viewport.

| Page | Light | Dark |
| --- | --- | --- |
| Home | [Light](screenshots/marble-home-light.png) | [Dark](screenshots/marble-home-dark.png) |
| Topic | [Light](screenshots/marble-topic-light.png) | [Dark](screenshots/marble-topic-dark.png) |
| Reader | [Light](screenshots/marble-reader-light.png) | [Dark](screenshots/marble-reader-dark.png) |

Browser-verified: theme persistence through home → topic → reader, home and topic views, search navigation, source highlighting, and focused reading. No page-level horizontal overflow at 390 × 844 in either theme. Marble assets match the originals byte-for-byte.

## Boundaries

All research content and counts are illustrative fixtures. Search opens a sample topic; chat appends a clearly labeled sample response. The paper is a typeset excerpt, not a live PDF. Theme switching, source highlighting, focus mode, view switching and local navigation work. Other controls explain their prototype status. No production routes, styles, APIs, databases or account settings are modified. No PR or deployment is part of this study.
