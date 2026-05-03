# Notion Integration Improvements — Design Spec

## Overview

Two features that turn the existing Notion export button into a useful research knowledge base workflow:

1. **Structured outline export with hierarchical auto-tags** — LLM distills the report into a clean outline, auto-generates domain/topic tags, and pushes it as a database row.
2. **Settings modal with Notion connection status** — gear icon opens a modal showing whether Notion is configured and which database is linked.

Single-user, personal tool. No OAuth. Config lives in `.env`.

---

## Feature 1: Structured Outline Export

### User flow

1. User generates a report (existing flow, no changes).
2. User clicks the "Notion" button on the finished report.
3. Frontend POSTs to `POST /export-notion` with `report_text`, `title`, `audience`, `model` (existing form fields, no changes).
4. Backend sends report text through a light LLM call (~1024 max tokens) with an outline prompt.
5. LLM returns a structured outline markdown block + a `TAGS:` line at the end.
6. Backend parses out the tags, converts outline markdown to Notion blocks via existing `markdown_to_blocks`.
7. Pushes as a database row with typed properties.
8. Returns Notion page URL; frontend opens it in a new tab.

### Outline format (LLM output)

```markdown
# [Report Title]

## Key Takeaways
- Takeaway 1
- Takeaway 2
- Takeaway 3

## Paper Summaries

### [Paper 1 Title]
- Key finding 1
- Key finding 2
- Methodology note
- [Source](url)

### [Paper 2 Title]
- Key finding 1
- Key finding 2
- [Source](url)

## Connections & Themes
- Cross-cutting insight 1
- Cross-cutting insight 2

TAGS: Domain/Topic, Domain/Topic, Domain/Topic
```

### Hierarchical tag scheme

Tags use a two-level `Domain/Topic` format:

- **Domain** (broad category): `NLP`, `Computer Vision`, `Reinforcement Learning`, `Robotics`, `ML Theory`, `Systems`, `Optimization`, `Generative Models`, `Multimodal`, `Healthcare AI`, `Graph Learning`
- **Topic** (specific within domain): `NLP/Transformers`, `NLP/Summarization`, `CV/Object Detection`, `ML Theory/Generalization`

The prompt includes a canonical domain list so the LLM reuses existing categories rather than inventing synonyms. 2-3 tags per report. Slash-separated so Notion multi-select groups them visually and filters work at both levels (`NLP` matches all NLP topics, `NLP/Transformers` drills down).

### Database properties

| Property   | Notion Type   | Source                          |
|------------|---------------|---------------------------------|
| Name       | title         | First heading extracted from report |
| Type       | select        | Hardcoded `"Report"`            |
| Audience   | select        | From form field                 |
| Model      | rich_text     | From form field                 |
| Source URL  | url           | From form field                 |
| Tags       | multi_select  | LLM-generated, 2-3 per report  |

`Created time` is automatic in Notion — no need to set it.

### Tag parsing

The LLM is instructed to emit `TAGS: tag1, tag2, tag3` as the final line. Backend parsing:

1. Split LLM output on last line starting with `TAGS:`.
2. Strip the `TAGS:` prefix, split on `,`, strip whitespace.
3. Pass as `metadata["tags"]` to `NotionExporter`.
4. Fallback: if no `TAGS:` line found, use empty list (export still succeeds, just no tags).

### Error handling

Existing error handling in `/export-notion` covers:
- Missing `NOTION_API_KEY` → 400
- Missing parent page/database ID → 400
- Notion API rejection (wrong ID, not shared) → 502 with Notion's error message
- Frontend shows errors via `alert()`

New failure mode:
- LLM outline generation fails → fall back to exporting raw report text (no outline). Log warning, don't block the export.

### Files changed

| File | Change |
|------|--------|
| `src/api/routers/report_notes.py` | Add LLM outline generation step in `export_notion()` before calling `NotionExporter`. Parse tags from output. |
| `src/services/notion/notion_exporter.py` | Add `Tags` multi_select to `_database_properties()`. |
| `src/prompts/templates/notion/outline.md` | **New file.** System prompt for structured outline + tag generation. |
| `static/js/report.js` | Fix `handleNotionClick` popup-blocker issue (same `window.open` fix applied to blog button). |

---

## Feature 2: Settings Modal with Notion Connection Status

### User flow

1. User clicks a gear icon on the main page toolbar (next to theme toggle).
2. Modal opens showing Notion connection status.
3. User reads status, closes modal.

### Modal contents

- **Status indicator**: green dot + "Connected" or red dot + "Not configured"
- **Database ID**: last 8 characters of the configured database ID (masked). Hidden if not configured.
- **Setup instructions** (shown only when not configured): "Set `NOTION_API_KEY` and `NOTION_PARENT_DATABASE_ID` in your `.env` file, then restart the server."
- **Close button**

### Status endpoint

`GET /api/notion-status`

Response:
```json
{
  "connected": true,
  "database_id": "...a1b2c3d4",
  "has_api_key": true,
  "has_parent": true
}
```

No secrets exposed. `database_id` is masked (only last 8 chars). Endpoint checks whether `config.NOTION_API_KEY` and either `config.NOTION_PARENT_PAGE_ID` or `config.NOTION_PARENT_DATABASE_ID` are non-empty strings.

### Files changed

| File | Change |
|------|--------|
| `src/api/routes/admin.py` | Add `GET /api/notion-status` endpoint. |
| `src/pages/main_page.py` | Add gear icon button to toolbar. |
| `static/js/settings.js` | Add modal injection and fetch-on-click handler. |
| `static/css/base.css` | Minimal styles for the settings modal (reuse `.lux-modal` pattern from notes dialog). |

---

## Out of scope

- OAuth / multi-user workspace auth
- Multiple workspace support
- Database URL override from the modal (config stays in `.env`)
- Embedding visualization (separate feature)
- Full LLM-generated notes export (using `NotesGenerator`) — just outline format

## Security

- Internal integration token stored server-side in `.env`, never sent to browser.
- `/api/notion-status` returns boolean + masked ID only.
- Integration can only access pages/databases explicitly shared with it.
- No read/delete permissions unless manually enabled on the integration.
- No OAuth needed for single-user.
