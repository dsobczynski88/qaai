"""Unit tests for the Tier-3 disk caching mechanism.

Covers DiskCacheManager, JamaProjectCache cache modes, the per-method cache
helpers on PyJamaTraceMatrix, and an end-to-end behavioral check that a USE
re-run makes zero Jama API calls. None of these tests need a live Jama server.
"""
import os
import re
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from pyjama.utils.cache_manager import CacheMode, DiskCacheManager
from pyjama.utils.jama_constants import CACHE_TIMESTAMP_FORMAT
from pyjama.utils.jama_project_cache import JamaProjectCache
from pyjama.jama.pyjama import PyJamaTraceMatrix


# ----------------------------------------------------------------------
# DiskCacheManager
# ----------------------------------------------------------------------
class TestDiskCacheManager:
    def test_cache_mode_values(self):
        assert CacheMode("off") is CacheMode.OFF
        assert CacheMode("use") is CacheMode.USE
        assert CacheMode("refresh") is CacheMode.REFRESH

    def test_timestamp_format(self):
        ts = DiskCacheManager.timestamp()
        assert re.match(r"^\d{4}(_\d{2}){5}_\d{6}$", ts), ts
        # round-trips through the documented format
        datetime.strptime(ts, CACHE_TIMESTAMP_FORMAT)

    def test_should_recompute_off(self, tmp_path):
        mgr = DiskCacheManager(mode=CacheMode.OFF, cache_root=str(tmp_path))
        assert mgr.should_recompute("k") is True
        mgr.mark_refreshed("k")
        assert mgr.should_recompute("k") is True  # still recompute
        assert mgr.writes_enabled() is False

    def test_should_recompute_use(self, tmp_path):
        mgr = DiskCacheManager(mode=CacheMode.USE, cache_root=str(tmp_path))
        assert mgr.should_recompute("k") is False
        assert mgr.writes_enabled() is True

    def test_should_recompute_refresh(self, tmp_path):
        mgr = DiskCacheManager(mode=CacheMode.REFRESH, cache_root=str(tmp_path))
        assert mgr.should_recompute("k") is True
        mgr.mark_refreshed("k")
        assert mgr.should_recompute("k") is False        # same key reused this session
        assert mgr.should_recompute("other") is True     # different key still recomputes
        assert mgr.writes_enabled() is True

    def test_jsonl_round_trip(self, tmp_path):
        mgr = DiskCacheManager(cache_root=str(tmp_path))
        folder = str(tmp_path / "x")
        rows = [{"id": "REQ-1", "type": "requirement"}, {"id": "TEST-1", "type": "test_case"}]
        path = mgr.write_jsonl(folder, "prefix_", "ts1", rows)
        assert os.path.exists(path)
        assert mgr.read_jsonl(path) == rows

    def test_json_round_trip(self, tmp_path):
        mgr = DiskCacheManager(cache_root=str(tmp_path))
        folder = str(tmp_path / "x")
        obj = {"timestamp": "t", "projects": {"A": {"id": 1}}}
        path = mgr.write_json(folder, "p_", "ts1", obj)
        assert mgr.read_json(path) == obj

    def test_newest_file(self, tmp_path):
        mgr = DiskCacheManager(cache_root=str(tmp_path))
        folder = str(tmp_path / "x")
        assert mgr.newest_file(folder, "resp_") is None  # folder may not exist yet
        p1 = mgr.write_jsonl(folder, "resp_", "ts1", [{"a": 1}])
        p2 = mgr.write_jsonl(folder, "resp_", "ts2", [{"a": 2}])
        # force distinct mtimes
        os.utime(p1, (time.time() - 100, time.time() - 100))
        os.utime(p2, (time.time(), time.time()))
        assert mgr.newest_file(folder, "resp_") == p2
        # suffix/prefix filtering
        mgr.write_json(folder, "other_", "ts3", {"b": 1})
        assert mgr.newest_file(folder, "other_", ".json").endswith(".json")
        assert mgr.newest_file(folder, "nomatch_") is None


# ----------------------------------------------------------------------
# JamaProjectCache cache modes
# ----------------------------------------------------------------------
class FakeProjectClient:
    """Counts get_projects() calls."""
    def __init__(self):
        self.calls = 0

    def get_projects(self):
        self.calls += 1
        return [{"id": 5, "fields": {"name": "Proj A"}}]


class TestJamaProjectCacheModes:
    def test_off_always_calls_api_and_writes_nothing(self, tmp_path):
        client = FakeProjectClient()
        folder = str(tmp_path / "proj")
        mgr = DiskCacheManager(mode=CacheMode.OFF, cache_root=str(tmp_path / "cache"))
        pc = JamaProjectCache(client, cache_manager=mgr, cache_folder=folder)
        assert pc.resolve_project_id("Proj A") == 5
        assert pc.resolve_project_id("Proj A") == 5
        assert client.calls == 2  # refreshed every call
        # OFF never writes a cache file
        assert not (os.path.exists(folder) and os.listdir(folder))

    def test_refresh_once_per_session(self, tmp_path):
        client = FakeProjectClient()
        folder = str(tmp_path / "proj")
        mgr = DiskCacheManager(mode=CacheMode.REFRESH, cache_root=str(tmp_path / "cache"))
        pc = JamaProjectCache(client, cache_manager=mgr, cache_folder=folder)
        assert pc.resolve_project_id("Proj A") == 5
        assert pc.resolve_project_id("Proj A") == 5
        assert client.calls == 1  # only the first resolve refreshes
        assert os.listdir(folder)  # a cache file was written

    def test_use_reads_from_disk_on_new_instance(self, tmp_path):
        folder = str(tmp_path / "proj")
        c1 = FakeProjectClient()
        mgr1 = DiskCacheManager(mode=CacheMode.USE, cache_root=str(tmp_path / "cache"))
        JamaProjectCache(c1, cache_manager=mgr1, cache_folder=folder).resolve_project_id("Proj A")
        assert c1.calls == 1  # miss -> refresh
        # new instance loads the saved directory from disk -> no API call
        c2 = FakeProjectClient()
        mgr2 = DiskCacheManager(mode=CacheMode.USE, cache_root=str(tmp_path / "cache"))
        pc2 = JamaProjectCache(c2, cache_manager=mgr2, cache_folder=folder)
        assert pc2.resolve_project_id("Proj A") == 5
        assert c2.calls == 0

    def test_use_default_folder_under_shared_cache_root(self, tmp_path):
        """With no folder override, project files land under <cache_root>/projects/."""
        client = FakeProjectClient()
        mgr = DiskCacheManager(mode=CacheMode.USE, cache_root=str(tmp_path / "cache"))
        pc = JamaProjectCache(client, cache_manager=mgr)
        assert pc.resolve_project_id("Proj A") == 5
        projects_dir = os.path.join(str(tmp_path / "cache"), "projects")
        names = os.listdir(projects_dir)
        assert any(n.startswith("pyjamaapi_project_directory_") for n in names)


# ----------------------------------------------------------------------
# PyJamaTraceMatrix cache helpers
# ----------------------------------------------------------------------
def make_api(tmp_path, mode=CacheMode.USE):
    """Build a PyJamaTraceMatrix with a Mock client and a tmp-scoped cache."""
    client = MagicMock()
    api = PyJamaTraceMatrix(
        client,
        data_path=str(tmp_path / "data"),
        log_path=str(tmp_path / "logs"),
        project_cache_folder=str(tmp_path / "pcache"),
        cache_mode=mode,
    )
    # Redirect cache writes into the tmp tree
    api._cache = DiskCacheManager(mode=mode, cache_root=str(tmp_path / "cache"), logger=api._logger)
    return api


class TestIdsDerivation:
    def test_test_suite_ids_rows_dedupes(self):
        payload = [{
            "requirement": {"req_id": "REQ-1", "text": "x"},
            "test_cases": [{"test_id": "TEST-1"}, {"test_id": "TEST-1"}],
            "design_docs": [],
        }]
        rows = PyJamaTraceMatrix._test_suite_ids_rows(payload)
        assert rows == [
            {"id": "REQ-1", "type": "requirement"},
            {"id": "TEST-1", "type": "test_case"},
        ]

    def test_test_case_ids_rows(self):
        payload = [{
            "test_case": {"test_id": "TEST-9"},
            "requirements": [{"req_id": "REQ-9"}],
            "design_docs": [],
        }]
        rows = PyJamaTraceMatrix._test_case_ids_rows(payload)
        assert rows == [
            {"id": "TEST-9", "type": "test_case"},
            {"id": "REQ-9", "type": "requirement"},
        ]


class TestIdentifierHelpers:
    def test_write_and_load_per_identifier(self, tmp_path):
        api = make_api(tmp_path)
        identifiers = ["GID-1", "GID-2", "PRQ-3"]
        api_id_to_identifier = {11: "GID-1", 22: "GID-2", 33: "PRQ-3"}
        ordered_api_ids = [11, 22]  # PRQ-3 produced no entry
        result = [
            {"requirement": {"req_id": "DOC-1", "text": "a"}},
            {"requirement": {"req_id": "DOC-2", "text": "b"}},
        ]
        api._write_identifier_responses(
            "bidirectional_trace", identifiers, ordered_api_ids, result, api_id_to_identifier
        )
        loaded = api._load_identifier_responses("bidirectional_trace", identifiers)
        # PRQ-3 contributes an empty list; order follows input identifiers
        assert loaded == result

    def test_load_returns_none_on_any_missing(self, tmp_path):
        api = make_api(tmp_path)
        api._write_identifier_responses(
            "bidirectional_trace", ["GID-1"], [11], [{"requirement": {"req_id": "D", "text": "t"}}],
            {11: "GID-1"},
        )
        # GID-9 has no file -> whole call is a miss
        assert api._load_identifier_responses("bidirectional_trace", ["GID-1", "GID-9"]) is None

    def test_rtm_round_trip(self, tmp_path):
        api = make_api(tmp_path)
        identifiers = ["GID-1", "PRQ-2"]
        result = {"user_needs": [], "system_requirements": [], "requirements": [{"req_id": "R"}],
                  "test_cases": [], "design_docs": []}
        api._write_rtm_response(identifiers, result)
        # order-independent hash
        assert api._load_rtm_response(["PRQ-2", "GID-1"]) == result
        assert api._load_rtm_response(["GID-9"]) is None


# ----------------------------------------------------------------------
# End-to-end behavioral check (mock client counts API calls)
# ----------------------------------------------------------------------
class CountingSuiteClient:
    """Minimal mock supporting get_test_suite_reviewer_structure."""
    def __init__(self):
        self.calls = 0

    def get_baselines_versioneditems(self, baseline_id):
        self.calls += 1
        return [{"id": 201, "itemType": 71, "fields": {"documentKey": "TEST-1"}}]

    def get_items_upstream_relationships(self, item_id):
        self.calls += 1
        return [{"fromItem": 101}]

    def get_item(self, item_id):
        self.calls += 1
        return {"id": 101, "fields": {"documentKey": "REQ-1", "description": "Req text"}}

    def get_items_downstream_related(self, req_id):
        self.calls += 1
        return [
            {"id": 201, "fields": {"documentKey": "TEST-1", "name": "TC one",
                                   "setup$71": "setup text", "testCaseSteps": []}},
            {"id": 301, "fields": {"documentKey": "DES-1", "name": "Design", "description": "dd"}},
        ]


def _build_api(tmp_path, client, mode):
    api = PyJamaTraceMatrix(
        client,
        data_path=str(tmp_path / "data"),
        log_path=str(tmp_path / "logs"),
        project_cache_folder=str(tmp_path / "pcache"),
        cache_mode=mode,
    )
    api._cache = DiskCacheManager(mode=mode, cache_root=str(tmp_path / "cache"), logger=api._logger)
    return api


class TestBehavioralCaching:
    def test_use_second_run_makes_no_api_calls(self, tmp_path):
        client = CountingSuiteClient()
        api = _build_api(tmp_path, client, CacheMode.USE)

        r1 = api.get_test_suite_reviewer_structure("BASE-100")
        calls_after_first = client.calls
        assert calls_after_first > 0
        assert r1[0]["requirement"]["req_id"] == "REQ-1"
        assert r1[0]["test_cases"][0]["in_review_baseline"] is True

        r2 = api.get_test_suite_reviewer_structure("BASE-100")
        assert client.calls == calls_after_first  # zero new API calls
        assert r2 == r1  # reconstructed losslessly from disk

        # cache files exist
        folder = os.path.join(str(tmp_path / "cache"), "baselines", "BASE-100")
        names = os.listdir(folder)
        assert any("test_suite_reviewer_structure_response_" in n for n in names)
        assert any("test_suite_reviewer_structure_ids_" in n for n in names)

    def test_off_never_caches(self, tmp_path):
        client = CountingSuiteClient()
        api = _build_api(tmp_path, client, CacheMode.OFF)

        api.get_test_suite_reviewer_structure("BASE-100")
        after_first = client.calls
        api.get_test_suite_reviewer_structure("BASE-100")
        assert client.calls == after_first * 2  # recomputed every time
        assert not os.path.exists(os.path.join(str(tmp_path / "cache"), "baselines"))
