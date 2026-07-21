from app.executor.simulated import SimulatedExecutor
from test_client import JobCleanup


def test_job_cleanup_cancels_once_and_can_be_disarmed(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, path, *, headers):
            calls.append(("post", path, headers))
            return Response()

    monkeypatch.setattr("test_client.httpx.Client", Client)

    cleanup = JobCleanup("http://service", "job-1", {"Authorization": "Bearer token"})
    cleanup.cancel()
    cleanup.cancel()
    assert [call for call in calls if call[0] == "post"] == [
        ("post", "/jobs/job-1/cancel", {"Authorization": "Bearer token"})
    ]

    disarmed = JobCleanup("http://service", "job-2", {})
    disarmed.disarm()
    disarmed.cancel()
    assert not any(call[0] == "post" and call[1] == "/jobs/job-2/cancel" for call in calls)


async def test_simulated_executor_stops_when_cancelled():
    checks = 0

    async def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return True

    result = await SimulatedExecutor().run_benchmark(
        ["one", "two"],
        "agent",
        should_cancel=should_cancel,
    )
    assert checks == 1
    assert result.task_results == []
