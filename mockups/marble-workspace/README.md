# Office Hours — marble workspace study

A restrained second-round mockup retaining the **actual Office Hours light/dark palette, blue accent, marble images, ASCII wordmark, Inter and JetBrains Mono**. This is not a rebrand, landing page, or production implementation.

## Preview locally

```sh
python -m http.server 8022 --directory mockups/marble-workspace --bind 0.0.0.0
```

Open `index.html`, `topic.html`, or `reader.html`. Use `?theme=light` or `?theme=dark` for a fixed state; the header theme control also remembers your choice. The standalone directory contains copies of the repository’s original marble JPGs. Fonts use Google Fonts with local system fallbacks.

The new Paper map view is under the topic tabs, or open `topic.html?view=map&theme=light` / `topic.html?view=map&theme=dark` directly.

## What changed

- **Home:** a working research desk rather than a promotional hero; a direct search field, continuing research threads, last reading position, and a note in context.
- **Topic:** numbered source references live in a reading margin beside the report. Papers, notes and tracked searches are separate views, without losing the collection context.
- **Paper map:** a topic view with four labeled subtopics and 24 illustrative papers. Reading status uses both color and shape: hollow warm-gray circles (To read), filled blue circles (Reading), and green squares (Done). Select a subtopic or paper to filter the adjacent list and scope Topic chat; clear the selection to restore the whole collection. Coordinates stay fixed throughout. Chat responses and reading-status edits are local fixtures, not saved or sent anywhere.
- **Reader:** opens with just the paper and conversation. **Contents & marks** opens an on-demand drawer; close it, press Escape, or select a passage to return to reading. Dark mode uses a dark paper by default; **Original page** toggles the paper colors independently, and switching the app theme resets the paper to that theme’s default. Clicking a source jumps to the highlighted passage; focus mode removes the chat when it is not needed.
- **Visuals:** warm paper and charcoal surfaces from `static/css/variables.css`; the exact original `marble-light.jpg` and `marble-dark.jpg`; restrained blue selection marks instead of a new accent system. Marble is a desk behind the content, never behind body text.

## Screenshots

Captured at 1440 px wide with fonts loaded. Home and topic use full-page captures; the reader is shown at a 1440 × 1100 viewport.

| Page | Light | Dark |
| --- | --- | --- |
| Home | [Light](screenshots/marble-home-light.png) | [Dark](screenshots/marble-home-dark.png) |
| Topic | [Light](screenshots/marble-topic-light.png) | [Dark](screenshots/marble-topic-dark.png) |
| Reader | [Light](screenshots/marble-reader-light.png) | [Dark](screenshots/marble-reader-dark.png) |
| Paper map | [Light](screenshots/marble-map-light.png) | [Dark](screenshots/marble-map-dark.png) |
| Selected subtopic + chat | [Light](screenshots/marble-map-selected-light.png) | [Dark](screenshots/marble-map-selected-dark.png) |

Optional reader states: [original light paper within the dark app](screenshots/marble-reader-dark-original.png) · [contents and marks open](screenshots/marble-reader-dark-contents.png).

Browser-verified: theme persistence through home → topic → reader, home and topic views, search navigation, source highlighting, and focused reading. The paper follows app-theme changes; its Original page switch leaves the app/chat theme untouched. Contents & marks supports Escape, backdrop dismissal, focus return and saved-passage jumps on desktop and mobile. No page-level horizontal overflow at 390 × 844 in either theme. Marble assets match the originals byte-for-byte.

Map browser checks cover four subtopics (8 / 6 / 5 / 5 papers), list/chat filtering, source selection, clearing filters, reading-status changes, unchanged dot positions, theme switching, topic-tab navigation, and keyboard controls. Mobile label/marker overlap was corrected; direct dot selection passes at 390 × 844 and 320 × 844 in both themes without page-level horizontal overflow. At 320 px, one label extends slightly outside the inner plotting area but remains visible.

## Boundaries

All research content and counts are illustrative fixtures. Search opens a sample topic; chat appends a clearly labeled sample response. The paper is a typeset excerpt, not a live PDF. Theme switching, source highlighting, focus mode, view switching and local navigation work. Other controls explain their prototype status. No production routes, styles, APIs, databases or account settings are modified. No PR or deployment is part of this study.
