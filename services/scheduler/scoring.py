"""Worker ranking. Week 1: deterministic tie-break by worker UUID (scoring model is W2)."""

from __future__ import annotations

from db.models import WorkerNode


def rank_workers(workers: list[WorkerNode]) -> list[WorkerNode]:
    return sorted(workers, key=lambda w: str(w.id))
