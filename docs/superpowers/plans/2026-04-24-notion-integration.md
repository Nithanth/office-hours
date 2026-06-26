# Notion Integration Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Notion export button into a structured research knowledge base — LLM-generated outlines with hierarchical auto-tags, plus a settings modal showing connection status.

**Architecture:** The existing `/export-notion` endpoint gains an LLM outline generation step before calling `NotionExporter`. A new `GET /api/notion-status` endpoint powers a settings modal injected by `settings.js`. The Notion button in `report.js` gets the same popup-blocker fix already applied to the blog button.

**Tech Stack:** FastAPI, FastHTML (PyHTML components), vanilla JS, existing LLMService + NotionExporter, Notion API

---

## File Map

| File | Action | Responsibility |
| ---- | ------ | -------------- |
| `src/prompts/templates/notion/outline.md` | Create | System prompt for LLM outline + tag generation |
| `src/services/notion/notion_exporter.py` | Modify | Add `Tags` multi_select + `Type` select to `_database_properties` |
| `src/api/routers/report_notes.py` | Modify | Add LLM outline generation in `export_notion()`, parse tags |
| `src/api/routes/admin.py` | Modify | Add `GET /api/notion-status` endpoint |
| `static/js/report.js` | Modify | Fix Notion popup blocker, add audience/model to form data |
| `static/js/settings.js` | Modify | Add gear icon click handler, modal injection, status fetch |
| `src/pages/main_page.py` | Modify | Add gear icon button to top bar |

---

### Task 1: Create the Notion outline prompt template

**Files:**

- Create: `src/prompts/templates/notion/outline.md`

- [ ] **Step 1: Create the prompt template file**

````markdown
# Notion Outline Generator

You are a research assistant that distills academic reports into structured outlines for a Notion knowledge base.

## Instructions

Given a research report, produce a clean structured outline in Markdown. The outline should be scannable — someone should grasp the key findings in 30 seconds.

## Output Format

```
# [Report Title — derive from content]

## Key Takeaways
- 3-5 bullet points capturing the most important findings

## Paper Summaries

### [Paper 1 Title]
- Key finding or contribution
- Methodology or approach (1 line)
- Notable result or metric
- [Source](url-if-available)

### [Paper 2 Title]
- (same structure)

## Connections & Themes
- 2-3 cross-cutting insights linking the papers together

TAGS: Domain/Topic, Domain/Topic, Domain/Topic
```

## Tag Taxonomy

Generate 2-3 tags using the format `Domain/Topic`. Reuse domains from this canonical list when they fit:

- NLP, Computer Vision, Reinforcement Learning, Robotics, ML Theory, Systems, Optimization, Generative Models, Multimodal, Healthcare AI, Graph Learning, Speech, Information Retrieval, Security, Recommender Systems, Scientific Computing

Topics should be specific within the domain, e.g.: `NLP/Summarization`, `CV/Object Detection`, `ML Theory/Generalization`, `Generative Models/Diffusion`.

If the paper topic doesn't fit a canonical domain, create a new one — but keep it concise (1-2 words).

## Rules

- The TAGS line MUST be the very last line of your output.
- Keep bullet points to 1 sentence each.
- Do not include the word "TAGS" anywhere else in the output.
- Output only the Markdown outline. No meta-commentary.
````

- [ ] **Step 2: Verify the template loads**

Run: `cd /Users/nithanth/Github/lux_scholar && PYTHONPATH=$(pwd)/src venv/bin/python -c "from prompts.prompt_builder import _load_template; print(_load_template('notion/outline.md')[:80])"`

Expected: First 80 chars of the prompt printed without error.

- [ ] **Step 3: Commit**

```bash
git add src/prompts/templates/notion/outline.md
git commit -m "feat: add Notion outline prompt template with hierarchical tag taxonomy"
```

---

### Task 2: Add Tags and Type to Notion database properties

**Files:**

- Modify: `src/services/notion/notion_exporter.py:92-120`

- [ ] **Step 1: Update `_database_properties` to include Tags and Type**

In `src/services/notion/notion_exporter.py`, replace the `_database_properties` function (lines 92-120):

```python
def _database_properties(title: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Build properties for a database row.

    Unknown columns are silently dropped by the Notion API only if the schema
    matches; callers should ensure the database has these properties or extend
    this mapping.
    """
    props: dict[str, Any] = {
        "Name": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
    }

    if url := metadata.get("source_url"):
        props["Source URL"] = {"url": url}
    if audience := metadata.get("audience"):
        props["Audience"] = {"select": {"name": audience}}
    if model := metadata.get("model"):
        props["Model"] = {"rich_text": [{"type": "text", "text": {"content": model[:200]}}]}
    if authors := metadata.get("authors"):
        joined = ", ".join(authors) if isinstance(authors, list) else str(authors)
        props["Authors"] = {
            "rich_text": [{"type": "text", "text": {"content": joined[:2000]}}]
        }
    if year := metadata.get("year"):
        try:
            props["Year"] = {"number": int(year)}
        except (TypeError, ValueError):
            pass
    if export_type := metadata.get("type"):
        props["Type"] = {"select": {"name": export_type}}
    if tags := metadata.get("tags"):
        tag_list = tags if isinstance(tags, list) else [tags]
        props["Tags"] = {
            "multi_select": [{"name": t.strip()[:100]} for t in tag_list if t.strip()]
        }

    return props
```

- [ ] **Step 2: Verify module imports cleanly**

Run: `cd /Users/nithanth/Github/lux_scholar && PYTHONPATH=$(pwd)/src venv/bin/python -c "from services.notion.notion_exporter import NotionExporter; print('OK')"`

Expected: `OK` (or ImportError for notion_client if not installed — that's fine, the import is lazy)

- [ ] **Step 3: Commit**

```bash
git add src/services/notion/notion_exporter.py
git commit -m "feat: add Tags (multi_select) and Type (select) to Notion database properties"
```

---

### Task 3: Add LLM outline generation to the Notion export endpoint

**Files:**

- Modify: `src/api/routers/report_notes.py:249-309`

- [ ] **Step 1: Update the `export_notion` function to generate an outline via LLM**

Replace the `export_notion` function (lines 256-309 in `report_notes.py`) with:

```python
_NOTION_OUTLINE_PROMPT: str = _load_template("notion/outline.md")


def _parse_tags(llm_output: str) -> tuple[str, list[str]]:
    """Split the TAGS: line from the outline and return (outline, tags)."""
    lines = llm_output.rstrip().split("\n")
    tags: list[str] = []
    outline = llm_output

    # Walk backwards to find the TAGS line (should be last non-empty line)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.upper().startswith("TAGS:"):
            raw = stripped[len("TAGS:"):].strip()
            tags = [t.strip() for t in raw.split(",") if t.strip()]
            outline = "\n".join(lines[:i]).rstrip()
            break
        # Stop searching if we hit a non-TAGS content line
        break

    return outline, tags


@limiter.limit("10/5minute")
@router.post("/export-notion")
async def export_notion(
    request: Request,
    report_text: str = Form(...),
    title: str = Form("Lux Scholar Report"),
    source_url: str = Form(""),
    audience: str = Form(""),
    model: str = Form(""),
) -> Response:
    """Generate a structured outline, then push it to Notion."""

    logger.info("notion_export: request received")

    if not report_text.strip():
        raise HTTPException(status_code=400, detail="Report text required")

    if not config.NOTION_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="Notion integration not configured. Set NOTION_API_KEY in .env.",
        )
    if not (config.NOTION_PARENT_PAGE_ID or config.NOTION_PARENT_DATABASE_ID):
        raise HTTPException(
            status_code=400,
            detail="Set NOTION_PARENT_PAGE_ID or NOTION_PARENT_DATABASE_ID in .env.",
        )

    # --- Generate structured outline via LLM ---
    llm_service: LLMService | None = get_llm_service()
    outline_md = report_text.strip()  # fallback: raw report
    tags: list[str] = []

    if llm_service:
        model_key = config.DEFAULT_MODEL_KEY
        model_cfg = config.AVAILABLE_MODELS[model_key]
        try:
            user_prompt = (
                "Distill the following research report into a structured outline.\n\n"
                "REPORT:\n" + report_text.strip()[:config.PAPER_MAX_CONTENT_SNIPPET_CHARS]
            )
            raw_outline = await llm_service.generate_completion(
                provider_name=model_cfg["provider"],
                model_name=model_cfg["id"],
                prompt=user_prompt,
                max_tokens=1024,
                system_prompt=_NOTION_OUTLINE_PROMPT,
            )
            outline_md, tags = _parse_tags(raw_outline)
            logger.info("notion_outline_generated", chars=len(outline_md), tags=tags)
        except Exception as err:
            logger.warning("notion_outline_fallback", error=str(err))
            # Fall through — outline_md stays as raw report text
    else:
        logger.warning("notion_export: LLM unavailable, exporting raw report")

    from services.notion.notion_exporter import NotionExporter, NotionExportError

    exporter = NotionExporter(api_key=config.NOTION_API_KEY)
    try:
        url = await exporter.export_report(
            title=title,
            markdown=outline_md,
            parent_page_id=config.NOTION_PARENT_PAGE_ID,
            parent_database_id=config.NOTION_PARENT_DATABASE_ID,
            metadata={
                "source_url": source_url or None,
                "audience": audience or None,
                "model": model or None,
                "type": "Report",
                "tags": tags,
            },
        )
    except NotionExportError as err:
        logger.error("notion_export_failed", error=str(err))
        raise HTTPException(status_code=502, detail=str(err)) from err
    finally:
        await exporter.close()

    logger.info("notion_export_success", url=url)
    return Response(
        content=f'{{"url": "{url}"}}',
        media_type="application/json",
    )
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `cd /Users/nithanth/Github/lux_scholar && PYTHONPATH=$(pwd)/src venv/bin/python -c "from api.routers.report_notes import router; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/api/routers/report_notes.py
git commit -m "feat: generate LLM outline with auto-tags before Notion export"
```

---

### Task 4: Fix Notion popup blocker and send audience/model metadata

**Files:**

- Modify: `static/js/report.js:380-421`

- [ ] **Step 1: Update `handleNotionClick` to open window synchronously and include metadata**

Replace the `handleNotionClick` function in `report.js` (lines 380-421):

```javascript
function handleNotionClick(ev) {
  const btn = ev.target.closest("#report-notion-btn");
  if (!btn) return;

  const reportEl = document.getElementById("report-content");
  if (!reportEl) return;

  btn.disabled = true;
  const origHtml = btn.innerHTML;
  btn.innerHTML =
    '<span class="lux-spinner small" style="vertical-align:middle;"></span> Exporting…';

  // Open window synchronously so browsers don't block it as a popup.
  const notionWindow = window.open("about:blank", "_blank");

  const cleaned = getCleanReportText(reportEl);
  const fd = new FormData();
  fd.append("report_text", cleaned);

  // Pull title from the first heading in the report, if any.
  const firstH = reportEl.querySelector("h1, h2, h3");
  fd.append("title", firstH ? firstH.textContent.trim() : "Lux Scholar Report");

  // Include audience and model so Notion gets structured properties.
  const audSel = document.getElementById("aud-select");
  const modelSel = document.getElementById("model-select");
  if (audSel && audSel.value) fd.append("audience", audSel.value);
  if (modelSel && modelSel.value) fd.append("model", modelSel.value);

  fetch("/export-notion", { method: "POST", body: fd })
    .then((r) => {
      if (!r.ok) return r.json().then((d) => { throw new Error(d.detail || `Server responded ${r.status}`); });
      return r.json();
    })
    .then((data) => {
      if (data.url && notionWindow) {
        notionWindow.location.href = data.url;
        if (typeof window.showToast === "function") {
          window.showToast("Exported to Notion!");
        }
      } else if (data.url) {
        window.open(data.url, "_blank");
      }
    })
    .catch((err) => {
      console.error("[lux] notion export failed", err);
      if (notionWindow) notionWindow.close();
      // eslint-disable-next-line no-alert
      alert(err.message || "Failed to export to Notion. Check your NOTION_API_KEY in .env.");
    })
    .finally(() => {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    });
}
```

- [ ] **Step 2: Commit**

```bash
git add static/js/report.js
git commit -m "fix: prevent popup blocker on Notion export, send audience/model metadata"
```

---

### Task 5: Add Notion status endpoint

**Files:**

- Modify: `src/api/routes/admin.py`

- [ ] **Step 1: Add the `GET /api/notion-status` endpoint**

Add this at the end of `admin.py` (after line 59):

```python
@router.get("/api/notion-status", tags=["admin"])
async def notion_status() -> dict[str, Any]:
    """Return Notion integration connection status (no secrets exposed)."""
    has_key = bool(_cfg.config.NOTION_API_KEY)
    db_id = _cfg.config.NOTION_PARENT_DATABASE_ID or ""
    page_id = _cfg.config.NOTION_PARENT_PAGE_ID or ""
    has_parent = bool(db_id or page_id)

    # Mask the database/page ID — show only last 8 chars
    target_id = db_id or page_id
    masked_id = f"...{target_id[-8:]}" if len(target_id) > 8 else target_id

    return {
        "connected": has_key and has_parent,
        "database_id": masked_id if has_parent else "",
        "has_api_key": has_key,
        "has_parent": has_parent,
        "target_type": "database" if db_id else ("page" if page_id else "none"),
    }
```

Also add the missing `Any` import — update the existing import line at the top of the file:

```python
from typing import Any
```

- [ ] **Step 2: Verify the endpoint loads**

Run: `cd /Users/nithanth/Github/lux_scholar && PYTHONPATH=$(pwd)/src venv/bin/python -c "from api.routes.admin import router; print([r.path for r in router.routes])"`

Expected: List including `'/api/notion-status'`

- [ ] **Step 3: Commit**

```bash
git add src/api/routes/admin.py
git commit -m "feat: add GET /api/notion-status endpoint for settings modal"
```

---

### Task 6: Add gear icon to the top bar

**Files:**

- Modify: `src/pages/main_page.py:700-718`

- [ ] **Step 1: Add a gear icon button to the top bar**

In the `home_page` function, find the top bar `Div` (around line 683). Add a gear icon button after the dark toggle button. Replace lines 703-718:

```python
            BUTTON(
                "💬 Assistant",
                id="open-global-chat",
                type="button",
                hx_get="/chat-modal?paper_id=global&title=Research%20Assistant",
                hx_target="#modal-container",
                hx_swap="innerHTML",
            ),
            BUTTON(
                I(cls="fa-solid fa-gear"),
                id="lux-settings-btn",
                type="button",
                title="Settings",
                style="border:none;background:transparent;cursor:pointer;font-size:1rem;padding:4px 8px;",
            ),
            BUTTON(
                "🌙 Dark",
                id="lux-dark-toggle",
                aria_label="Toggle dark mode",
                type="button",
            ),
            cls="top-bar",
```

- [ ] **Step 2: Verify the page renders**

Run: `cd /Users/nithanth/Github/lux_scholar && PYTHONPATH=$(pwd)/src venv/bin/python -c "from pages.main_page import home_page, SharedPageData; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/pages/main_page.py
git commit -m "feat: add gear icon to top bar for settings modal"
```

---

### Task 7: Add settings modal with Notion connection status

**Files:**

- Modify: `static/js/settings.js`

- [ ] **Step 1: Add the settings modal handler**

Add this block at the end of the `init()` function in `settings.js`, just before the closing `}` of `init` (before line 484):

```javascript
    // ------------------------------------------------------------------
    // Settings modal (gear icon) — shows Notion connection status
    // ------------------------------------------------------------------
    const settingsBtn = document.getElementById('lux-settings-btn');
    if (settingsBtn) {
      settingsBtn.addEventListener('click', () => {
        let dlg = document.getElementById('settings-dialog');
        if (!dlg) {
          document.body.insertAdjacentHTML(
            'beforeend',
            `<dialog id="settings-dialog" class="lux-modal">
              <form method="dialog">
                <h3 style="margin:0 0 16px">Settings</h3>
                <div id="notion-status-area" style="margin-bottom:16px;">
                  <h4 style="margin:0 0 8px;font-size:0.95rem;">Notion Integration</h4>
                  <div id="notion-status-content">
                    <span class="lux-spinner small" style="display:inline-block;vertical-align:middle;"></span>
                    Checking…
                  </div>
                </div>
                <menu style="display:flex;justify-content:flex-end;">
                  <button value="close">Close</button>
                </menu>
              </form>
            </dialog>`
          );
          dlg = document.getElementById('settings-dialog');
        }

        // Fetch Notion status every time the modal opens
        const statusEl = document.getElementById('notion-status-content');
        if (statusEl) {
          statusEl.innerHTML = '<span class="lux-spinner small" style="display:inline-block;vertical-align:middle;"></span> Checking…';
          fetch('/api/notion-status')
            .then(r => r.json())
            .then(data => {
              if (data.connected) {
                statusEl.innerHTML =
                  '<span style="color:#22c55e;font-weight:600;">● Connected</span>' +
                  '<div style="margin-top:6px;font-size:0.85rem;color:var(--fg-muted,#888);">' +
                  'Target: ' + (data.target_type === 'database' ? 'Database' : 'Page') +
                  ' <code>' + data.database_id + '</code>' +
                  '</div>';
              } else {
                let detail = '';
                if (!data.has_api_key) detail += '<div>✗ NOTION_API_KEY not set</div>';
                if (!data.has_parent) detail += '<div>✗ No database or page ID configured</div>';
                statusEl.innerHTML =
                  '<span style="color:#ef4444;font-weight:600;">● Not configured</span>' +
                  '<div style="margin-top:6px;font-size:0.85rem;color:var(--fg-muted,#888);">' +
                  detail +
                  '<div style="margin-top:4px;">Set <code>NOTION_API_KEY</code> and <code>NOTION_PARENT_DATABASE_ID</code> in <code>.env</code>, then restart.</div>' +
                  '</div>';
              }
            })
            .catch(() => {
              statusEl.innerHTML = '<span style="color:#eab308;">● Could not check status</span>';
            });
        }

        dlg.showModal();
      });
    }
```

- [ ] **Step 2: Verify JS has no syntax errors**

Run: `cd /Users/nithanth/Github/lux_scholar && npx --yes acorn --ecma2020 static/js/settings.js > /dev/null && echo "OK"`

Expected: `OK` (or install acorn if not available — alternatively just check the server starts)

- [ ] **Step 3: Commit**

```bash
git add static/js/settings.js
git commit -m "feat: add settings modal with Notion connection status display"
```

---

### Task 8: End-to-end verification

- [ ] **Step 1: Start the dev server**

Run: `cd /Users/nithanth/Github/lux_scholar && PYTHONPATH=$(pwd)/src venv/bin/python -m uvicorn main:app --reload --port 8000`

Expected: Server starts without import errors.

- [ ] **Step 2: Verify the status endpoint**

Run: `curl -s http://localhost:8000/api/notion-status | python3 -m json.tool`

Expected: JSON with `connected`, `database_id`, `has_api_key`, `has_parent`, `target_type` fields.

- [ ] **Step 3: Test the full flow in browser**

1. Open `http://localhost:8000`
2. Verify gear icon appears in the top bar
3. Click gear icon — modal shows Notion connection status (green if configured, red if not)
4. Run a search, generate a report
5. Click "Notion" button on the finished report
6. Verify: a new tab opens, LLM generates outline, page appears in Notion database with Tags and Type properties

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Notion integration — structured outline export with auto-tags and settings modal"
```
