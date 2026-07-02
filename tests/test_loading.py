"""Loader tests: the raw store is the system of record, so a response body is written to disk
verbatim BEFORE it is parsed. A corrupt/schema-deviating page must survive on disk (for later
schema validation) and then fail loudly — never be silently lost.
"""
from __future__ import annotations

import json

import pytest

from quadra.loading.paginate import (
    CompletenessError,
    RawParseError,
    fetch_single,
    iter_pages,
)


class FakeResp:
    """Minimal stand-in for requests.Response: .json() decodes .text exactly like requests."""

    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status
        self.headers: dict = {}

    def json(self):
        return json.loads(self.text)  # raises ValueError on invalid JSON, as requests does


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, path, params):
        resp = self._responses[self.calls]
        self.calls += 1
        return resp


def _writer(tmp_path, written):
    def write_raw(page, resp):
        p = tmp_path / f"page-{page:04d}.json"
        p.write_text(resp.text)  # persist verbatim
        written.append(p)
        return p
    return write_raw


def test_corrupt_page_persisted_before_raise(tmp_path):
    corrupt = '{"data": [ this is not json'
    client = FakeClient([FakeResp(corrupt)])
    written: list = []
    with pytest.raises(RawParseError) as exc:
        list(iter_pages(client, "/data/feedin", {"asset_id": "X"}, 500, _writer(tmp_path, written)))
    # the verbatim body is on disk even though it could not be parsed ...
    assert written and written[0].read_text() == corrupt
    # ... and the raised error names the saved file for inspection.
    assert str(written[0]) in str(exc.value)


def test_fetch_single_corrupt_persisted_before_raise(tmp_path):
    corrupt = "<html>502 bad gateway</html>"
    client = FakeClient([FakeResp(corrupt)])
    written: list = []
    with pytest.raises(RawParseError):
        fetch_single(client, "/data/prices", {"product": "all"}, _writer(tmp_path, written))
    assert written and written[0].read_text() == corrupt


def test_happy_path_persists_then_paginates(tmp_path):
    p1 = json.dumps({"data": [1, 2], "meta": {"total_records": 3, "has_next_page": True}})
    p2 = json.dumps({"data": [3], "meta": {"total_records": 3, "has_next_page": False}})
    client = FakeClient([FakeResp(p1), FakeResp(p2)])
    written: list = []
    pages = list(iter_pages(client, "/x", {}, 500, _writer(tmp_path, written)))
    assert [pg.page for pg in pages] == [1, 2]
    assert len(written) == 2 and all(w.exists() for w in written)


def test_completeness_gate_still_fires(tmp_path):
    # server claims 5 records but only delivers 2 -> lost data must raise (bytes still saved).
    p1 = json.dumps({"data": [1, 2], "meta": {"total_records": 5, "has_next_page": False}})
    client = FakeClient([FakeResp(p1)])
    written: list = []
    with pytest.raises(CompletenessError):
        list(iter_pages(client, "/x", {}, 500, _writer(tmp_path, written)))
    assert written and written[0].exists()  # persisted before the gate fired
