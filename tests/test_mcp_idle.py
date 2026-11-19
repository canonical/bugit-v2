import asyncio

from bugit_v2.mcp_server.idle import IdleTracker, watch_idle


async def run_request(tracker: IdleTracker, app_body):
    async def app(scope, receive, send):
        await app_body()

    mw = tracker.wrap(app)
    await mw({"type": "http"}, None, None)


async def test_tracks_in_flight_requests_and_resets_clock():
    t = {"now": 100.0}
    tracker = IdleTracker(clock=lambda: t["now"])
    seen = []

    async def body():
        seen.append(tracker.active)
        t["now"] = 150.0

    await run_request(tracker, body)
    assert seen == [1] and tracker.active == 0
    assert tracker.idle_seconds() == 0  # clock reset at request end
    t["now"] = 170.0
    assert tracker.idle_seconds() == 20


async def test_exception_still_decrements():
    tracker = IdleTracker(clock=lambda: 0.0)

    async def body():
        raise RuntimeError("x")

    try:
        await run_request(tracker, body)
    except RuntimeError:
        pass
    assert tracker.active == 0


async def test_lifespan_scopes_are_not_counted():
    tracker = IdleTracker(clock=lambda: 0.0)
    seen = []

    async def app(scope, receive, send):
        seen.append(tracker.active)

    await tracker.wrap(app)({"type": "lifespan"}, None, None)
    assert seen == [0]


async def test_watch_idle_exits_only_when_idle_and_not_busy():
    t = {"now": 0.0}
    tracker = IdleTracker(clock=lambda: t["now"])
    busy = {"v": True}

    class Server:
        should_exit = False

    server = Server()
    task = asyncio.create_task(
        watch_idle(server, tracker, timeout=10, is_busy=lambda: busy["v"], poll=0.01)
    )
    t["now"] = 100.0
    await asyncio.sleep(0.05)
    assert server.should_exit is False  # busy (e.g. a job is running)
    busy["v"] = False
    await asyncio.wait_for(task, 1)
    assert server.should_exit is True


async def test_watch_idle_disabled_when_timeout_zero():
    tracker = IdleTracker(clock=lambda: 0.0)

    class Server:
        should_exit = False

    await asyncio.wait_for(
        watch_idle(Server(), tracker, timeout=0, is_busy=lambda: False, poll=0.01), 1
    )
