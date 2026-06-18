"""The "View log" feature: the run's problem notes are embedded into every
generated viewer (RTM / test-case / hazard) as a self-contained JSON block, and
an empty log still renders cleanly (no leftover placeholder, button hidden).

Pure unit test — build_viewer* are plain string-substitution renderers, so any
JSON-serializable record dict works as input.
"""
from qaai.viewer.generator import build_viewer, build_viewer_tc, build_viewer_hz


RECORDS = [{"requirement": {"req_id": "REQ-1"}}]
ALL_BUILDERS = (build_viewer, build_viewer_tc, build_viewer_hz)


def test_log_embedded_in_viewer():
    log = [
        {"item_id": "REQ-9", "level": "warning",
         "text": "No test cases are traced to this requirement."},
        {"item_id": "REQ-3", "level": "error", "text": "Requirement REQ-3: review errored — item skipped."},
    ]
    html = build_viewer(RECORDS, "src.jsonl", "run-1", log_entries=log)

    assert "{{LOG}}" not in html  # placeholder substituted
    assert '<script id="LOG" type="application/json">' in html
    assert 'id="view-log-btn"' in html
    # Notes are embedded verbatim so the button can echo them.
    assert "No test cases are traced to this requirement." in html
    assert "REQ-9" in html and "REQ-3" in html


def test_empty_log_renders_empty_array_and_hidden_button():
    html = build_viewer(RECORDS, "src.jsonl", "run-1")  # no log_entries

    assert "{{LOG}}" not in html
    assert '<script id="LOG" type="application/json">[]</script>' in html
    # The button ships hidden; shared.js reveals it only when the log is non-empty.
    assert 'id="view-log-btn" hidden' in html


def test_all_three_viewers_embed_log():
    log = [{"item_id": "X1", "level": "error", "text": "boom-marker"}]
    for build in ALL_BUILDERS:
        html = build(RECORDS, "src", "run", log_entries=log)
        assert "{{LOG}}" not in html
        assert "boom-marker" in html


def test_log_script_close_tag_is_escaped():
    """A note containing '</script>' must not break out of the embedded JSON."""
    log = [{"item_id": "X", "level": "warning", "text": "danger </script> text"}]
    html = build_viewer(RECORDS, "src", "run", log_entries=log)
    assert "</script> text" not in html  # raw close tag escaped
    assert "<\\/script> text" in html
