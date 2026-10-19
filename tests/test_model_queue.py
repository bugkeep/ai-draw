import asyncio
import secrets
import time

import pytest

from studio.llm import close_model_clients, model_connection, rate_limit_delay
from studio.settings import ModelConfig
from studio.settings import ModelSettings
from studio.runtime import DrawingRuntime
from studio.store import StudioStore


def test_connection_recovery_queue_is_shared_fair_and_cancellable():
    async def check():
        config = ModelConfig(provider="local", model="drawing-model", api_base="http://localhost:1234/v1")
        connection = model_connection(config)
        assert model_connection(config.model_copy(update={"model": "another-model"})) is connection
        different_key = config.model_copy(update={"api_key": ModelConfig(api_key=secrets.token_urlsafe(32)).api_key})
        assert model_connection(different_key) is not connection
        connection.rate_limited(0.06)
        order, starts, observed = [], [], []

        async def request(index):
            await connection.acquire(observed.append)
            order.append(index)
            starts.append(time.monotonic())

        first = asyncio.create_task(request(0))
        cancelled = asyncio.create_task(request(1))
        third = asyncio.create_task(request(2))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await asyncio.gather(first, third)
        assert order == [0, 2] and starts[1] - starts[0] >= 0.05
        assert observed and all(item["phase"] == "model_wait" for item in observed)
        assert not connection.queue.locked()
        for _ in range(4):
            connection.succeeded()
        assert connection.interval == 0
        await close_model_clients()
    asyncio.run(check())


def test_malformed_retry_after_does_not_abort_recovery():
    assert 1 <= rate_limit_delay("invalid-date", 0) <= 2
    assert 1 <= rate_limit_delay("NaN", 0) <= 2
    assert rate_limit_delay("Infinity", 0) <= 2


def test_queued_cancellation_finishes_while_canvas_lock_is_held(tmp_path):
    async def check():
        store = StudioStore(tmp_path / "queue.sqlite3")
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session("排队停止")
        lock = asyncio.Lock()
        runtime.document_locks[session["document"]["id"]] = lock
        await lock.acquire()
        task = runtime.submit(session["id"], "画一个蓝色圆形")
        worker = runtime.running[task["id"]]
        await asyncio.sleep(0)
        assert store.get_task(task["id"])["status"] == "queued"
        runtime.cancel(task["id"])
        assert store.get_task(task["id"])["status"] == "cancelled" and lock.locked()
        await asyncio.gather(worker, return_exceptions=True)
        await asyncio.sleep(0)
        assert not runtime.busy
        runtime.cancel(task["id"])
        assert store.get_document(session["document"]["id"])["elements"] == []
        lock.release()
        store.close()
    asyncio.run(check())
