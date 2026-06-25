"""Background task queue for managing long-running async operations.

Provides a lightweight in-process task queue with:
- Named tasks with status tracking (pending → running → done/failed)
- Configurable retry with exponential backoff
- Concurrency limiting per task category
- Structured logging of task lifecycle events
- Query API for introspection/debugging

Usage::

    from services.tasks import task_queue

    # Simple fire-and-forget
    task_queue.enqueue("prewarm", prewarm_paper(url, paper_id))

    # With retry
    task_queue.enqueue("extract_figures", extract_figs(pdf_path), max_retries=2)

    # Check status
    status = task_queue.status()  # {"pending": 3, "running": 2, ...}
"""

from services.tasks.queue import TaskQueue

# Singleton instance used throughout the app.
task_queue = TaskQueue()

__all__ = ["TaskQueue", "task_queue"]
