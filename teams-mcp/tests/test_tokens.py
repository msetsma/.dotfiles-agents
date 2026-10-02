from teams_browser.auth.tokens import (
    extract_message_cookies,
    extract_region_config,
    extract_tokens,
    find_token,
    get_identity,
)

from .helpers import (
    cookie,
    make_jwt,
    make_session,
    msal_entry,
    region_entry,
    tmp_auth_entry,
    user_details_entry,
)


def test_classic_msal_tokens():
    substrate = make_jwt({"aud": "https://substrate.office.com", "oid": "abc"})
    spaces = make_jwt({"aud": "https://api.spaces.skype.com", "upn": "a@b.com"})
    state = make_session(
        local_storage=[
            msal_entry("https://substrate.office.com/SubstrateSearch-Internal.ReadWrite", substrate),
            msal_entry("https://api.spaces.skype.com/Authorization.ReadWrite", spaces),
        ]
    )
    tokens = extract_tokens(state)
    assert tokens.substrate and tokens.substrate.token == substrate
    assert tokens.spaces and tokens.spaces.token == spaces
    assert tokens.csa is None


def test_tmp_auth_tokens():
    substrate = make_jwt({"aud": "https://substrate.office.com"})
    state = make_session(
        local_storage=[tmp_auth_entry("HTTPS://SUBSTRATE.OFFICE.COM", substrate)]
    )
    tokens = extract_tokens(state)
    assert tokens.substrate and tokens.substrate.token == substrate


def test_expired_tokens_are_ignored():
    expired = make_jwt({"aud": "https://api.spaces.skype.com"}, exp_in=-10)
    state = make_session(local_storage=[msal_entry("api.spaces.skype.com", expired)])
    assert find_token(state, ("api.spaces.skype.com",)) is None


def test_region_partitioned():
    state = make_session(
        local_storage=[
            region_entry(
                "https://teams.microsoft.com/api/mt/part/amer-02",
                "https://teams.microsoft.com/api/chatsvc/amer",
            )
        ]
    )
    region = extract_region_config(state)
    assert region is not None
    assert region.region == "amer"
    assert region.partition == "02"
    assert region.region_partition == "amer-02"
    assert region.has_partition is True


def test_region_non_partitioned():
    state = make_session(
        local_storage=[
            region_entry(
                "https://teams.microsoft.com/api/mt/emea",
                "https://teams.microsoft.com/api/chatsvc/emea",
            )
        ]
    )
    region = extract_region_config(state)
    assert region is not None
    assert region.region_partition == "emea"
    assert region.has_partition is False


def test_message_cookies():
    skype = make_jwt({"skypeid": "orgid:abc"})
    authtoken = "Bearer%3D" + make_jwt({"oid": "abc"})
    state = make_session(
        cookies=[
            cookie("skypetoken_asm", skype, ".asyncgw.teams.microsoft.com"),
            cookie("authtoken", authtoken, "teams.cloud.microsoft"),
        ]
    )
    skype_token, auth_token = extract_message_cookies(state)
    assert skype_token == skype
    assert auth_token and auth_token.startswith("ey")


def test_identity_from_token_claims():
    spaces = make_jwt({"aud": "https://api.spaces.skype.com", "name": "Ada", "upn": "ada@b.com"})
    state = make_session(local_storage=[msal_entry("api.spaces.skype.com", spaces)])
    name, upn = get_identity(state)
    assert name == "Ada"
    assert upn == "ada@b.com"


def test_user_details_licenses():
    state = make_session(local_storage=[user_details_entry("8:orgid:abc")])
    tokens = extract_tokens(state)
    assert tokens.spaces is None  # sanity
    from teams_browser.auth.tokens import extract_user_details

    details = extract_user_details(state)
    assert details is not None
    assert details.mri == "8:orgid:abc"
    assert details.licenses["isTranscriptEnabled"] is True
