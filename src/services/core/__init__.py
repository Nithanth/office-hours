"""Core provider clients shared across services.

Contains *thin* wrappers around external APIs so the rest of the codebase can
stay provider-agnostic.

Key modules:
• ``llm_service`` - unified async interface to OpenAI/Gemini/Claude/Together.
• ``exa_client`` - lightweight search/grounding client.

These are re-exported for convenience:

```python
from services.core import LLMService, ExaClient
```
"""

from services.core.exa_client import ExaClient  # noqa: F401 - re-export
from services.core.llm_service import LLMService  # noqa: F401 - re-export

__all__: list[str] = ["LLMService", "ExaClient"]
