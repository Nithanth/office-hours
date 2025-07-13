"""Chat session/state management utilities.

Stores per-paper chat histories, handles moderation flags, and exposes the
singleton ``ChatState`` so any component can fetch or append messages.

```python
from services.chat import get_chat_state, ChatState
```
"""

from services.chat.chat_state import ChatState, get_chat_state  # noqa: F401 – re-export

__all__: list[str] = ["ChatState", "get_chat_state"]
