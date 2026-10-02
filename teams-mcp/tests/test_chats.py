"""Chats: parsing, HTML flattening and endpoint filtering."""

import json

import pytest

from teams_browser.api.chats import (
    conversation_kind,
    find_conversation,
    html_to_text,
    list_conversations,
    list_messages,
    parse_conversation,
    parse_message,
)
from teams_browser.errors import ResourceNotFound
from teams_browser.models import RegionConfig, TokenSet

from .helpers import make_jwt

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def _region() -> RegionConfig:
    return RegionConfig(
        region="amer",
        partition="03",
        region_partition="amer-03",
        has_partition=True,
        middle_tier_url="https://teams.microsoft.com/api/mt/part/amer-03",
        chat_service_url="https://teams.microsoft.com/api/chatsvc/amer",
        csa_service_url="https://teams.microsoft.com/api/csa/amer",
        teams_base_url="https://teams.microsoft.com",
    )


def _tokens() -> TokenSet:
    from teams_browser.models import TokenInfo

    return TokenSet(
        skype_token="skype-token-value",
        spaces=TokenInfo(token=make_jwt({"aud": "spaces"}), expires_at=None),
    )


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.payload


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


# -- html ------------------------------------------------------------------- #


def test_html_to_text_strips_markup_and_quotes():
    text = html_to_text(
        "<p>Hello <at id=\"0\">Roe, Rick</at></p><blockquote><p>old</p></blockquote><p>new</p>"
    )
    assert "Hello Roe, Rick" in text
    assert "new" in text
    assert "old" not in text
    assert "<" not in text


def test_html_to_text_entities_and_nbsp():
    assert html_to_text("<p>Blue&nbsp;works &amp; ships</p>") == "Blue works & ships"


def test_html_to_text_empty():
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


# -- parsing ---------------------------------------------------------------- #


def test_parse_message_extracts_everything():
    raw = _fixture("chatsvc_messages.json")["messages"][0]
    message = parse_message(raw)
    assert message.sender == "Doe, Jane"
    assert message.mentions == ["Roe, Rick"]
    assert [a.name for a in message.attachments if a.kind != "image"] == ["plan.xlsx"]
    assert any(a.kind == "image" for a in message.attachments)
    assert message.reactions == {"like": 2}
    assert message.is_system is False
    assert message.timestamp.year == 2026  # 7-digit fraction parsed


def test_parse_message_marks_thread_activity_as_system():
    raw = _fixture("chatsvc_messages.json")["messages"][2]
    assert parse_message(raw).is_system is True


def test_list_messages_sorted_oldest_first():
    payload = {"messages": list(reversed(_fixture("chatsvc_messages.json")["messages"]))}
    messages = list_messages(_region(), _tokens(), "19:x@thread.tacv2", client=_StubClient(payload))
    stamps = [m.timestamp for m in messages]
    assert stamps == sorted(stamps)


def test_list_messages_uses_skype_auth_header():
    client = _StubClient({"messages": []})
    list_messages(_region(), _tokens(), "19:x", client=client)
    headers = client.calls[0][1]["headers"]
    assert headers["Authentication"] == "skypetoken=skype-token-value"


# -- conversations ---------------------------------------------------------- #


def test_conversation_kind_classification():
    assert conversation_kind({"id": "19:a@thread.tacv2"}) == "channel"
    assert conversation_kind({"id": "19:meeting_a@thread.v2"}) == "meeting"
    assert conversation_kind({"id": "19:a_b@unq.gbl.spaces"}) == "one_to_one"
    assert conversation_kind({"id": "19:a@thread.v2"}) == "group"
    assert (
        conversation_kind(
            {"id": "19:a@thread.v2", "threadProperties": {"productThreadType": "TeamsTeam"}}
        )
        == "channel"
    )


def test_parse_conversation_uses_channel_topic_and_team():
    raw = _fixture("chatsvc_conversations.json")["conversations"][0]
    conversation = parse_conversation(raw)
    assert conversation.kind == "channel"
    assert conversation.topic == "Data Platform"
    assert conversation.team_id == "00000000-0000-0000-0000-000000000001"
    assert conversation.is_favorite is True
    assert conversation.last_message_preview == "Standup moved to 9:30"
    assert conversation.last_message_at is not None


def test_parse_conversation_meeting_topic():
    raw = _fixture("chatsvc_conversations.json")["conversations"][1]
    assert parse_conversation(raw).topic == "Design Review"


def test_list_conversations_parses_fixture():
    client = _StubClient(_fixture("chatsvc_conversations.json"))
    conversations = list_conversations(_region(), _tokens(), client=client)
    assert [c.kind for c in conversations] == ["channel", "meeting", "one_to_one"]


def test_find_conversation_by_topic():
    client = _StubClient(_fixture("chatsvc_conversations.json"))
    found = find_conversation(_region(), _tokens(), "design review", client=client)
    assert found.kind == "meeting"


def test_find_conversation_missing_raises():
    client = _StubClient(_fixture("chatsvc_conversations.json"))
    with pytest.raises(ResourceNotFound):
        find_conversation(_region(), _tokens(), "nonexistent", client=client)


def test_find_conversation_by_thread_id_checks_access():
    client = _StubClient({"messages": []})
    found = find_conversation(_region(), _tokens(), "19:meeting_abc@thread.v2", client=client)
    assert found.id == "19:meeting_abc@thread.v2"
    assert "/messages" in client.calls[0][0]


def test_missing_skype_token_raises():
    from teams_browser.errors import TokenExpired

    with pytest.raises(TokenExpired):
        list_conversations(_region(), TokenSet(), client=_StubClient({}))
