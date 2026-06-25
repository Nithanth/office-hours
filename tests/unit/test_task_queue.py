"""Unit tests for the background task queue."""

import asyncio

import pytest

from services.tasks.queue import TaskQueue, TaskStatus


@pytest.fixture
def queue():
    return TaskQueue(default_concurrency=4)


@pytest.mark.asyncio
async def test_enqueue_simple_success(queue: TaskQueue):
    """A simple coroutine completes and is tracked."""

    async def _job():
        return 42

    task_id = queue.enqueue("test", _job(), name="simple_job")
    # Give the event loop a moment to run the task
    await asyncio.sleep(0.05)

    record = queue.get_task(task_id)
    assert record is not None
    assert record.status == TaskStatus.DONE
    assert record.result == 42
    assert record.attempts == 1


@pytest.mark.asyncio
async def test_enqueue_failure_no_retry(queue: TaskQueue):
    """A failing task with no retries ends up FAILED."""

    async def _bad():
        raise ValueError("oops")

    task_id = queue.enqueue("test", _bad(), name="fail_job")
    await asyncio.sleep(0.05)

    record = queue.get_task(task_id)
    assert record is not None
    assert record.status == TaskStatus.FAILED
    assert "oops" in (record.error or "")


@pytest.mark.asyncio
async def test_enqueue_fn_with_retry(queue: TaskQueue):
    """enqueue_fn retries a failing function up to max_retries."""
    call_count = 0

    async def _flaky(x: int):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("not yet")
        return x * 2

    task_id = queue.enqueue_fn(
        "test",
        _flaky,
        5,
        name="retry_job",
        max_retries=3,
        retry_backoff=0.01,
    )
    # Wait for retries to complete
    await asyncio.sleep(0.5)

    record = queue.get_task(task_id)
    assert record is not None
    assert record.status == TaskStatus.DONE
    assert record.result == 10
    assert record.attempts == 3


@pytest.mark.asyncio
async def test_status_counts(queue: TaskQueue):
    """status() returns correct aggregate counts."""

    async def _ok():
        return True

    async def _fail():
        raise ValueError("x")

    queue.enqueue("cat_a", _ok())
    queue.enqueue("cat_a", _ok())
    queue.enqueue("cat_b", _fail())
    await asyncio.sleep(0.05)

    counts = queue.status()
    assert counts["done"] == 2
    assert counts["failed"] == 1


@pytest.mark.asyncio
async def test_concurrency_limiting(queue: TaskQueue):
    """Tasks respect the category concurrency limit."""
    queue.set_concurrency("limited", 2)

    running = []
    max_concurrent = 0

    async def _track():
        nonlocal max_concurrent
        running.append(1)
        current = len(running)
        if current > max_concurrent:
            max_concurrent = current
        await asyncio.sleep(0.05)
        running.pop()

    for _ in range(5):
        queue.enqueue("limited", _track())

    await asyncio.sleep(0.5)
    assert max_concurrent <= 2


@pytest.mark.asyncio
async def test_clear_completed(queue: TaskQueue):
    """clear_completed removes old finished records."""

    async def _ok():
        return True

    queue.enqueue("test", _ok())
    await asyncio.sleep(0.05)

    # Should not clear (too recent)
    removed = queue.clear_completed(older_than_sec=0)
    assert removed == 1  # older_than_sec=0 means everything is older

    assert len(queue.recent()) == 0
