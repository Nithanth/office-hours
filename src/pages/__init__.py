"""Pure static frontend pages served via FastAPI `StaticFiles`.

No Python runtime is required at import - this exists solely so tools treat
`pages` as a package when resolving template paths. HTML/JS assets live next
door under `static/`.
"""
