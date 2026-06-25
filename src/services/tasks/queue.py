"""In-process async task queue with retry, concurrency control, and observability.

Designed as a drop-in replacement for bare ``asyncio.create_task`` calls that
need reliability guarantees. Tasks are tracked in memory with structured
logging on state transitions.

Future upgrade path: swap the in-memory backend for Redis + arq/celery
without changing call sites.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from loguru import logger

logger = logger.bind(component="TaskQueue")


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class TaskRecord:
    """Metadata for a tracked background task."""

    id: str
    category: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    attempts: int = 0
    max_retries: int = 0
    error: str | None = None
    result: Any = None


# ---------------------------------------------------------------------------
# Queue implementation
# ---------------------------------------------------------------------------

# Default concurrency per category (prevents stampede)
_DEFAULT_CONCURRENCY = 8


class TaskQueue:
    """Lightweight async task queue with retry and observability."""

    def __init__(self, default_concurrency: int = _DEFAULT_CONCURRENCY) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(default_concurrency)
        )
        self._default_concurrency = default_concurrency
        # Track asyncio task handles so they don't get GC'd
        self._handles: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_concurrency(self, category: str, limit: int) -> None:
        """Set the max concurrent tasks for a given category."""
        self._semaphores[category] = asyncio.Semaphore(max(1, limit))

    def enqueue(
        self,
        category: str,
        coro: Awaitable[Any],
        *,
        name: str = "",
        max_retries: int = 0,
        retry_backoff: float = 1.0,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[Exception], None] | None = None,
    ) -> str:
        """Submit an async coroutine for background execution.

        Parameters
        ----------
        category
            Logical grouping (e.g. "prewarm", "figure_extract"). Controls
            concurrency isolation.
        coro
            The awaitable to execute.
        name
            Human-readable label for logging. Defaults to category + short ID.
        max_retries
            How many times to retry on failure (0 = no retry).
        retry_backoff
            Base seconds for exponential backoff between retries.
        on_success
            Optional callback invoked with the task result on success.
        on_failure
            Optional callback invoked with the exception on final failure.

        Returns
        -------
        str
            Unique task ID for status queries.
        """
        task_id = f"{category}_{uuid.uuid4().hex[:8]}"
        record = TaskRecord(
            id=task_id,
            category=category,
            name=name or f"{category}:{task_id[-8:]}",
            max_retries=max_retries,
        )
        self._tasks[task_id] = record

        handle = asyncio.create_task(
            self._run(record, coro, retry_backoff, on_success, on_failure)
        )
        self._handles.add(handle)
        handle.add_done_callback(self._handles.discard)

        logger.debug(
            "task_enqueued",
            task_id=task_id,
            category=category,
            name=record.name,
        )
        return task_id

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Return the record for a specific task, or None."""
        return self._tasks.get(task_id)

    def status(self) -> dict[str, int]:
        """Return aggregate counts by status."""
        counts: dict[str, int] = {s.value: 0 for s in TaskStatus}
        for t in self._tasks.values():
            counts[t.status.value] += 1
        return counts

    def recent(self, limit: int = 20) -> list[TaskRecord]:
        """Return the most recent tasks (newest first)."""
        sorted_tasks = sorted(
            self._tasks.values(), key=lambda t: t.created_at, reverse=True
        )
        return sorted_tasks[:limit]

    def clear_completed(self, older_than_sec: float = 3600) -> int:
        """Remove completed/failed task records older than *older_than_sec*."""
        cutoff = time.time() - older_than_sec
        to_remove = [
            tid
            for tid, t in self._tasks.items()
            if t.status in (TaskStatus.DONE, TaskStatus.FAILED)
            and (t.finished_at or 0) < cutoff
        ]
        for tid in to_remove:
            del self._tasks[tid]
        return len(to_remove)

    @property
    def pending_count(self) -> int:
        return sum(
            1
            for t in self._tasks.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYING)
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(
        self,
        record: TaskRecord,
        coro: Awaitable[Any],
        retry_backoff: float,
        on_success: Callable[[Any], None] | None,
        on_failure: Callable[[Exception], None] | None,
    ) -> None:
        """Execute the task with retry logic under the category semaphore."""
        sem = self._semaphores[record.category]

        async with sem:
            while True:
                record.attempts += 1
                record.status = TaskStatus.RUNNING
                record.started_at = time.time()

                try:
                    result = await coro
                    record.status = TaskStatus.DONE
                    record.finished_at = time.time()
                    record.result = result
                    elapsed = record.finished_at - record.started_at

                    logger.debug(
                        "task_done",
                        task_id=record.id,
                        name=record.name,
                        elapsed_ms=int(elapsed * 1000),
                    )

                    if on_success:
                        try:
                            on_success(result)
                        except Exception:
                            pass
                    return

                except Exception as exc:
                    record.error = str(exc)

                    if record.attempts <= record.max_retries:
                        # Retry with exponential backoff
                        record.status = TaskStatus.RETRYING
                        wait = retry_backoff * (2 ** (record.attempts - 1))
                        logger.warning(
                            "task_retry",
                            task_id=record.id,
                            name=record.name,
                            attempt=record.attempts,
                            max_retries=record.max_retries,
                            wait_sec=wait,
                            error=str(exc),
                        )
                        await asyncio.sleep(wait)
                        # For retry we need a fresh coroutine — but since we
                        # received an already-started awaitable, we can't restart it.
                        # Mark as failed and log the limitation.
                        record.status = TaskStatus.FAILED
                        record.finished_at = time.time()
                        logger.error(
                            "task_retry_unsupported",
                            task_id=record.id,
                            name=record.name,
                            reason="Cannot retry an already-consumed coroutine. "
                            "Use enqueue_fn() for retryable tasks.",
                        )
                        if on_failure:
                            try:
                                on_failure(exc)
                            except Exception:
                                pass
                        return
                    else:
                        record.status = TaskStatus.FAILED
                        record.finished_at = time.time()
                        logger.error(
                            "task_failed",
                            task_id=record.id,
                            name=record.name,
                            attempts=record.attempts,
                            error=str(exc),
                        )
                        if on_failure:
                            try:
                                on_failure(exc)
                            except Exception:
                                pass
                        return

    def enqueue_fn(
        self,
        category: str,
        fn: Callable[..., Awaitable[Any]],
        *args: Any,
        name: str = "",
        max_retries: int = 0,
        retry_backoff: float = 1.0,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[Exception], None] | None = None,
        **kwargs: Any,
    ) -> str:
        """Submit a callable (function + args) for retryable execution.

        Unlike ``enqueue()`` which takes a pre-built coroutine, this accepts
        the async function and its arguments so it can be re-invoked on retry.
        """
        task_id = f"{category}_{uuid.uuid4().hex[:8]}"
        record = TaskRecord(
            id=task_id,
            category=category,
            name=name or f"{category}:{task_id[-8:]}",
            max_retries=max_retries,
        )
        self._tasks[task_id] = record

        handle = asyncio.create_task(
            self._run_fn(record, fn, args, kwargs, retry_backoff, on_success, on_failure)
        )
        self._handles.add(handle)
        handle.add_done_callback(self._handles.discard)

        logger.debug(
            "task_enqueued",
            task_id=task_id,
            category=category,
            name=record.name,
            retryable=True,
        )
        return task_id

    async def _run_fn(
        self,
        record: TaskRecord,
        fn: Callable[..., Awaitable[Any]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        retry_backoff: float,
        on_success: Callable[[Any], None] | None,
        on_failure: Callable[[Exception], None] | None,
    ) -> None:
        """Execute a retryable task function under the category semaphore."""
        sem = self._semaphores[record.category]

        async with sem:
            while True:
                record.attempts += 1
                record.status = TaskStatus.RUNNING
                record.started_at = time.time()

                try:
                    result = await fn(*args, **kwargs)
                    record.status = TaskStatus.DONE
                    record.finished_at = time.time()
                    record.result = result
                    elapsed = record.finished_at - record.started_at

                    logger.debug(
                        "task_done",
                        task_id=record.id,
                        name=record.name,
                        elapsed_ms=int(elapsed * 1000),
                        attempts=record.attempts,
                    )

                    if on_success:
                        try:
                            on_success(result)
                        except Exception:
                            pass
                    return

                except Exception as exc:
                    record.error = str(exc)

                    if record.attempts <= record.max_retries:
                        record.status = TaskStatus.RETRYING
                        wait = retry_backoff * (2 ** (record.attempts - 1))
                        logger.warning(
                            "task_retry",
                            task_id=record.id,
                            name=record.name,
                            attempt=record.attempts,
                            max_retries=record.max_retries,
                            wait_sec=wait,
                            error=str(exc),
                        )
                        await asyncio.sleep(wait)
                        continue  # retry with fresh fn() call
                    else:
                        record.status = TaskStatus.FAILED
                        record.finished_at = time.time()
                        logger.error(
                            "task_failed",
                            task_id=record.id,
                            name=record.name,
                            attempts=record.attempts,
                            error=str(exc),
                        )
                        if on_failure:
                            try:
                                on_failure(exc)
                            except Exception:
                                pass
                        return
