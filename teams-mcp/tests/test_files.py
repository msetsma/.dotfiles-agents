"""Working-set files: classification, recording exclusion and thread filtering."""

import json
from pathlib import Path

from teams_browser.api.files import is_recording, list_files, parse_file
from teams_browser.models import TokenInfo, TokenSet

from .helpers import make_jwt

FIXTURES = Path(__file__).parent / "fixtures"


def _tokens() -> TokenSet:
    return TokenSet(
        substrate=TokenInfo(token=make_jwt({"aud": "https://substrate.office.com"}), expires_at=None)
    )


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.payload


def _fixture():
    return json.loads((FIXTURES / "working_set_files.json").read_text())


def test_is_recording():
    assert is_recording("mp4", "Video") is True
    assert is_recording("xlsx", "Excel") is False
    assert is_recording(None, "Video") is True
    assert is_recording("MP4", None) is True


def test_parse_file_maps_visualization_fields():
    raw = _fixture()["value"][0]
    shared = parse_file(raw)
    assert shared.name == "Plan.xlsx"
    assert shared.kind == "Excel"
    assert shared.extension == "xlsx"
    assert shared.url == "https://example.sharepoint.com/plan.xlsx"
    assert shared.preview_url.endswith("plan.png")
    assert shared.is_recording is False


def test_parse_file_recording_keeps_thread_id():
    shared = parse_file(_fixture()["value"][1])
    assert shared.is_recording is True
    assert shared.thread_id == "19:meeting_YWJjZGVm@thread.v2"


def test_parse_file_web_link_without_extension():
    shared = parse_file(_fixture()["value"][3])
    assert shared.kind == "WebLink"
    assert shared.extension is None
    assert shared.url == "https://example.com/board"


def test_list_files_excludes_recordings_when_asked():
    client = _StubClient(_fixture())
    files = list_files(_tokens(), include_recordings=False, client=client)
    assert all(not f.is_recording for f in files)
    assert len(files) == 3


def test_list_files_requests_visualization_select():
    client = _StubClient(_fixture())
    list_files(_tokens(), client=client)
    params = client.calls[0][1]["params"]
    assert "Visualization" in params["$select"]
    assert "MeetingThreadId" in params["$select"]


def test_list_files_thread_filter():
    client = _StubClient(_fixture())
    list_files(_tokens(), thread_id="19:meeting_YWJjZGVm@thread.v2", client=client)
    assert "MeetingThreadId eq" in client.calls[0][1]["params"]["$filter"]
