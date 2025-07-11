"""Project-wide pytest configuration.

If ``pytest-asyncio`` is installed it will be registered automatically so
``async def`` tests work without explicit marks. If not installed we fall
back gracefully, but async tests will error - install the package via
``pip install -r requirements-dev.txt``.
"""

import importlib

pytest_plugins: list[str] = []

if importlib.util.find_spec("pytest_asyncio") is not None:
    # Register plugin so pytest picks up async support.
    pytest_plugins.append("pytest_asyncio")
