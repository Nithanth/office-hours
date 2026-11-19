# Office Hours — The Reading Room

**Look A: an editorial research workspace.** A branch-only visual concept, not a replacement for the live app.

## Screenshots

| Page | Daylight | Evening |
| --- | --- | --- |
| Home | [Light](screenshots/look-a-home-light.png) | [Dark](screenshots/look-a-home-dark.png) |
| Topic | [Light](screenshots/look-a-topic-light.png) | [Dark](screenshots/look-a-topic-dark.png) |
| Reader | [Light](screenshots/look-a-reader-light.png) | [Dark](screenshots/look-a-reader-dark.png) |

## View locally

From the repository root:

```sh
python -m http.server 8021 --directory mockups/reading-room --bind 0.0.0.0
```

Open `index.html`, `topic.html`, or `reader.html` on that server. Each page accepts `?theme=light` or `?theme=dark`; the sidebar moon/sun control changes and remembers the theme. Google Fonts supplies Instrument Serif and DM Sans, with local system fallbacks.

## Direction

- **Home:** one calm research question, ongoing topic collections, and a place to resume reading.
- **Topic:** a readable synthesis with source citations, a living collection, and quieter tracking controls.
- **Reader:** an academic page in the centre, a contents rail, and an evidence-linked conversation alongside it.
- **Evening:** forest-charcoal chrome, softer typography and warm paper; the document stays readable instead of being inverted.

## Scope and boundaries

All content is sample data in `app.js`. No app routes, APIs, databases, credentials, or production styles are used or changed. Searching opens the sample topic; tabs, navigation, theme switching, focus mode, source highlighting, and a sample chat interaction work locally. Other controls explain their prototype status. The paper is an illustrative typeset excerpt, **not a working PDF viewer**; AI replies are clearly marked fixtures. Counts represent an illustrative workspace, not connected data.

The live app's search/report/topic/reader capabilities informed the layout; implementing this direction in FastHTML and integrating real state would be separate work after design selection. Nothing is deployed, merged, or proposed as a pull request.

## Verification

Verified all six desktop page/theme states in Chrome, theme persistence across navigation, topic fixture tabs, reader source highlighting, focus mode, and labeled fixture chat. At 390 × 844, none of the pages overflowed horizontally in either theme. Home and reader placeholders exceed 4.5:1 contrast in both themes. Reader body is 11 px at desktop (an academic-page preview); the chat companion is 12 px. The paper scrolls independently, with no redundant outer Reader scrollbar. The preview is not an accessible, integrated production PDF implementation.
