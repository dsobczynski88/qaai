"""Success-gating in `_run_batch_review`: results from a run that errors or comes
out incomplete are purged from the cache and never reused, while one bad item no
longer aborts the whole batch.

Pure unit test — a stub graph and a fake cache manager stand in for the real
LangGraph pipeline and ReviewCacheManager (no LLM / no JAMA).
"""
import json
import logging

import pytest

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

    async def purge_entity(self, entity_id, prompt_set=None):
        self.purged.append((entity_id, prompt_set))


def _viewer_writer(outputs_path):
    text = outputs_path.read_text(encoding="utf-8")
    return f"{outputs_path}.html" if text.strip() else None


async def _run(items, cache, tmp_path):
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
        cache_manager=cache,
        prompt_set="test_suite_reviewer_v4",
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
