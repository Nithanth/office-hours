"""Jinja2 HTML template directory.

The app uses *FileSystemLoader* pointed here; no runtime code is executed -
this package marker just ensures relative imports like

```python
from templates import email
```

resolve in IDEs and type checkers.
"""
