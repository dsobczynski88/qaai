"""Success-gating in `_run_batch_review`: results from a run that errors or comes
out incomplete are purged from the cache and never reused, while one bad item no
longer aborts the whole batch. Also covers the live-progress + run-log behaviour
threaded through the background Job.

Pure unit test — a stub graph and a fake cache manager stand in for the real
LangGraph pipeline and ReviewCacheManager (no LLM / no JAMA).
"""
import json
import logging

import pytest

from qaai.api.jobs import Job
from qaai.api.services import _run_batch_review
from qaai.core.constants import OUTPUT_JSONL_FILENAME


logger = logging.getLogger("test.batch_gating")


class _StubGraph:
    """ainvoke dispatches on item['behave']: ok | raise | incomplete."""

    async def ainvoke(self, graph_input, config):
        behave = graph_input["behave"]
        if behave == "raise":
            raise RuntimeError("boom")
        return {"ok": behave == "ok", "entity": graph_input["entity"]}


class _FakeCache:
    def __init__(self):
        self.purged = []

    async def purge_run(self, entity_id, since, prompt_set=None):
        # Run-scoped purge: the batch loop drops only this run's files. The test
        # only cares which entities were purged (and under which prompt set).
        self.purged.append((entity_id, prompt_set))


def _viewer_writer(outputs_path, log_entries=None):
    # Capture the log handed to the viewer so tests can assert on it.
    _viewer_writer.last_log = list(log_entries or [])
    text = outputs_path.read_text(encoding="utf-8")
    return f"{outputs_path}.html" if text.strip() else None


async def _run(items, cache, tmp_path, *, progress=None, missing_records_fn=None):
    return await _run_batch_review(
        logger=logger,
        run_dir=tmp_path,
        items=items,
        graph=_StubGraph(),
        thread_id_fn=lambda i, _item: f"t-{i}",
        graph_input_fn=lambda _i, item: item,
        viewer_writer=_viewer_writer,
        item_noun="thing",
        entity_id_fn=lambda _i, item: item["entity"],
        is_complete_fn=lambda s: s.get("ok") is True,
        missing_records_fn=missing_records_fn,
        cache_manager=cache,
        prompt_set="test_suite_reviewer_v4",
        progress=progress,
    )


async def test_clean_item_kept_errored_and_incomplete_purged(tmp_path):
    cache = _FakeCache()
    items = [
        {"entity": "E0", "behave": "ok"},
        {"entity": "E1", "behave": "raise"},
        {"entity": "E2", "behave": "incomplete"},
    ]

    viewer = await _run(items, cache, tmp_path)
    assert viewer is not None  # batch survived the bad item

    # The errored + incomplete entities are purged (scoped to the run's prompt set);
    # the clean one is left in the cache.
    assert ("E1", "test_suite_reviewer_v4") in cache.purged
    assert ("E2", "test_suite_reviewer_v4") in cache.purged
    assert not any(e == "E0" for e, _ in cache.purged)

    # Output records: the clean item and the (surfaced) incomplete item; the
    # hard-errored item produced no state, so it is absent.
    lines = [
        json.loads(line)
        for line in (tmp_path / OUTPUT_JSONL_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    entities = {rec["entity"] for rec in lines}
    assert entities == {"E0", "E2"}


async def test_all_items_failing_raises(tmp_path):
    cache = _FakeCache()
    items = [
        {"entity": "E0", "behave": "raise"},
        {"entity": "E1", "behave": "raise"},
    ]
    with pytest.raises(ValueError):
        await _run(items, cache, tmp_path)
    assert ("E0", "test_suite_reviewer_v4") in cache.purged
    assert ("E1", "test_suite_reviewer_v4") in cache.purged


async def test_outputs_in_input_order_and_run_concurrently(tmp_path):
    """The parallelized batch fans items out concurrently (more than one in flight
    at once) yet still writes outputs.jsonl in INPUT order, not completion order."""
    import asyncio

    class _ConcurrentGraph:
        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def ainvoke(self, graph_input, config):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            # Earlier items sleep longer, so completion order is the REVERSE of
            # input order — the output ordering assertion below is meaningful only
            # because of this.
            await asyncio.sleep(graph_input["delay"])
            self.in_flight -= 1
            return {"ok": True, "entity": graph_input["entity"]}

    graph = _ConcurrentGraph()
    items = [
        {"entity": "E0", "delay": 0.15},
        {"entity": "E1", "delay": 0.10},
        {"entity": "E2", "delay": 0.05},
        {"entity": "E3", "delay": 0.01},
    ]
    cache = _FakeCache()
    viewer = await _run_batch_review(
        logger=logger,
        run_dir=tmp_path,
        items=items,
        graph=graph,
        thread_id_fn=lambda i, _item: f"t-{i}",
        graph_input_fn=lambda _i, item: item,
        viewer_writer=_viewer_writer,
        item_noun="thing",
        entity_id_fn=lambda _i, item: item["entity"],
        is_complete_fn=lambda s: s.get("ok") is True,
        cache_manager=cache,
    )
    assert viewer is not None
    # Concurrency actually happened (sequential would keep max_in_flight == 1).
    assert graph.max_in_flight >= 2
    # Output stays in INPUT order despite reverse completion order.
    lines = [
        json.loads(line)
        for line in (tmp_path / OUTPUT_JSONL_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [rec["entity"] for rec in lines] == ["E0", "E1", "E2", "E3"]


async def test_progress_counts_and_run_log(tmp_path):
    """The Job's progress counters advance once per item and the problems-only
    run log captures errored / incomplete / missing-input notes — and the same
    log is handed to the viewer writer."""
    cache = _FakeCache()
    job = Job(job_id="j1", filename="x.html")
    items = [
        {"entity": "E0", "behave": "ok"},          # clean
        {"entity": "E1", "behave": "raise"},        # errored
        {"entity": "E2", "behave": "incomplete"},   # incomplete output
        {"entity": "E3", "behave": "ok", "missing": True},  # clean output, missing input
    ]

    def missing_records_fn(item):
        return ["No test cases are traced to this requirement."] if item.get("missing") else []

    viewer = await _run(
        items, cache, tmp_path, progress=job, missing_records_fn=missing_records_fn
    )
    assert viewer is not None

    # begin() set the total; record_item() advanced once per item.
    assert job.total == 4
    assert job.done == 4
    # Missing-input is advisory (E3 still completed) → only E1 + E2 are failures.
    assert job.succeeded == 2
    assert job.failed == 2

    # Problems-only log: errored (E1), incomplete (E2), missing-input (E3); E0 clean → absent.
    by_item = {(m["item_id"], m["level"]) for m in job.messages}
    assert ("E1", "error") in by_item
    assert ("E2", "warning") in by_item
    assert ("E3", "warning") in by_item
    assert not any(m["item_id"] == "E0" for m in job.messages)

    # The viewer received exactly the same run log (shared by reference).
    assert _viewer_writer.last_log == job.messages

    # ETA is None until at least one item is done, and resolves to 0 once finished
    # (done == total ⇒ nothing remaining ⇒ None, not negative).
    assert job.to_status_dict()["eta_seconds"] is None


async def test_status_dict_exposes_progress_fields(tmp_path):
    """to_status_dict carries the fields the frontend poll loop reads."""
    job = Job(job_id="j2", filename="x.html")
    job.begin(3)
    job.record_item(ok=True)
    d = job.to_status_dict()
    assert d["total"] == 3 and d["done"] == 1 and d["succeeded"] == 1 and d["failed"] == 0
    assert d["eta_seconds"] is not None and d["eta_seconds"] >= 0
    assert d["messages"] == []
