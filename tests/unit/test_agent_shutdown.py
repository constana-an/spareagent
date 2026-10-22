"""Stopping the worker while a task runs must report the hand-back before returning."""

import asyncio

from packages.worker.agent import WorkerAgent


async def test_cancelled_run_waits_for_the_hand_back_report():
    reported = []

    async def execute(assignment):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Stands in for _execute's shielded WORKER_SHUTDOWN finalize call.
            await asyncio.shield(asyncio.sleep(0.05))
            reported.append("WORKER_SHUTDOWN")
            raise

    agent = WorkerAgent(client=None)
    agent.execute = execute
    run = asyncio.create_task(agent._execute_with_grace({}, asyncio.Event()))
    await asyncio.sleep(0.01)
    run.cancel()  # what Device.shutdown() and a timed-out app shutdown do
    try:
        await run
    except asyncio.CancelledError:
        pass
    assert reported == ["WORKER_SHUTDOWN"]
