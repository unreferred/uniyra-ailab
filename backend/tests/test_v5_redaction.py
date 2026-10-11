"""Unit tests for app.services.redaction (Marathon B). No DB, pure unit."""

from app.services.redaction import (
    REDACTED,
    SECRET_PATTERNS,
    SENSITIVE_KEY_NAMES,
    contains_secret,
    redact,
    redact_text,
    safe_metadata,
    scrub_error,
)

OPENAI_KEY = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"
OPENAI_PROJ_KEY = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"
GITHUB_TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
SLACK_TOKEN = "xoxb-" + "123456789012" + "-" + "1234567890123" + "-" + "abcdefghijklmnopqrstuvwxyzABCDEF"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA7Q0mZ0lPqWl4hZq3nP1sT2uV3wX4yZ5aB6cD7eF8gH9iJ0kL\n"
    "-----END RSA PRIVATE KEY-----"
)
BEARER_TOKEN = "abcdefghijklmnopqrstuvwxyz123456"
GENERIC_SECRET = "supersecretvalue123"


def test_redacted_constant_is_bracket_form():
    assert REDACTED == "[REDACTED]"


def test_secret_patterns_present():
    assert isinstance(SECRET_PATTERNS, tuple)
    assert len(SECRET_PATTERNS) >= 8


def test_openai_keys_redacted():
    for key in (OPENAI_KEY, OPENAI_PROJ_KEY):
        out = redact_text(f"here is the key {key} end")
        assert key not in out
        assert REDACTED in out


def test_github_tokens_redacted():
    out = redact_text(f"token={GITHUB_TOKEN}")
    assert GITHUB_TOKEN not in out
    assert REDACTED in out


def test_aws_access_key_id_redacted():
    out = redact_text(f"aws id {AWS_ACCESS_KEY_ID} here")
    assert AWS_ACCESS_KEY_ID not in out
    assert REDACTED in out


def test_aws_secret_access_key_assignment_redacted():
    out = redact_text(f"aws_secret_access_key = {AWS_SECRET}")
    assert AWS_SECRET not in out
    assert REDACTED in out


def test_slack_tokens_redacted():
    out = redact_text(f"slack {SLACK_TOKEN} done")
    assert SLACK_TOKEN not in out
    assert REDACTED in out


def test_jwts_redacted():
    out = redact_text(f"jwt {JWT} done")
    assert JWT not in out
    assert REDACTED in out


def test_private_key_block_redacted():
    out = redact_text(f"before\n{PRIVATE_KEY}\nafter")
    assert "MIIEpAIBAAKCAQEA" not in out
    assert "PRIVATE KEY" not in out
    assert out == f"before\n{REDACTED}\nafter"


def test_authorization_bearer_redacted():
    out = redact_text(f"Authorization: Bearer {BEARER_TOKEN}")
    assert BEARER_TOKEN not in out
    assert REDACTED in out
    assert out.startswith("Authorization: Bearer ")
    assert out == f"Authorization: Bearer {REDACTED}"


def test_generic_api_key_assignment_redacted():
    out = redact_text(f'api_key = "{GENERIC_SECRET}"')
    assert GENERIC_SECRET not in out
    assert out == f'api_key = "{REDACTED}"'


def test_sensitive_key_names_fragments():
    for fragment in ("api_key", "secret", "token", "password", "private_key"):
        assert fragment in SENSITIVE_KEY_NAMES


def test_redact_deep_walks_and_keeps_keys():
    payload = {
        "outer": {"inner": [f"prefix {OPENAI_KEY} suffix"]},
        "keep_me": "ordinary value",
        "password": "hunter2",
        "nested": {"AWS_ACCESS_KEY_ID": AWS_ACCESS_KEY_ID},
    }
    out = redact(payload)

    assert OPENAI_KEY not in str(out)
    assert out["keep_me"] == "ordinary value"
    assert out["outer"]["inner"][0] == f"prefix {REDACTED} suffix"
    assert out["password"] == REDACTED
    assert out["nested"]["AWS_ACCESS_KEY_ID"] == REDACTED
    assert set(out.keys()) == {"outer", "keep_me", "password", "nested"}


def test_redact_sensitive_key_value_never_leaks():
    out = redact({"api_key": "anything-at-all", "Passwd": "x", "refresh_token": "y"})
    assert out["api_key"] == REDACTED
    assert out["Passwd"] == REDACTED
    assert out["refresh_token"] == REDACTED


def test_redact_preserves_scalars():
    assert redact(1) == 1
    assert redact(1.5) == 1.5
    assert redact(True) is True
    assert redact(None) is None
    assert redact_text(12345) == 12345


def test_contains_secret_true_and_false():
    assert contains_secret(f"key is {OPENAI_KEY}") is True
    assert contains_secret(f"Bearer {BEARER_TOKEN}") is True
    assert contains_secret("ordinary prose with no creds") is False
    assert contains_secret(REDACTED) is False


def test_idempotence_on_representative_structure():
    payload = {
        "message": f"use {OPENAI_KEY} and {GITHUB_TOKEN}",
        "password": "hunter2",
        "items": [f"jwt {JWT}", {"access_token": "rawvalue"}],
    }
    once = redact(payload)
    twice = redact(once)
    assert once == twice
    assert redact_text(redact_text("password=hunter2")) == redact_text("password=hunter2")


def test_no_false_positive_on_token_budget_prose():
    sentence = "the token budget is 100 tokens"
    assert redact_text(sentence) == sentence
    assert contains_secret(sentence) is False


def test_safe_metadata_non_dict_returns_empty():
    assert safe_metadata("not a dict") == {}
    assert safe_metadata([1, 2, 3]) == {}
    assert safe_metadata(None) == {}


def test_safe_metadata_redacts_dict():
    out = safe_metadata({"password": "hunter2", "note": f"see {OPENAI_KEY}"})
    assert out["password"] == REDACTED
    assert OPENAI_KEY not in out["note"]


def test_secret_in_nested_list_inside_dict_removed():
    payload = {"level1": {"level2": ["clean", f"token {GITHUB_TOKEN}", "clean2"]}}
    out = redact(payload)
    assert GITHUB_TOKEN not in str(out)
    assert out["level1"]["level2"] == ["clean", f"token {REDACTED}", "clean2"]


def test_scrub_error_deep_redacts():
    err = {"detail": f"aws_secret_access_key={AWS_SECRET}", "token": "raw"}
    out = scrub_error(err)
    assert AWS_SECRET not in str(out)
    assert out["token"] == REDACTED


def test_redact_never_raises_on_weird_input():
    class Weird:
        def __str__(self):
            return f"obj {OPENAI_KEY}"

        def __repr__(self):
            return "Weird()"

    class BadStr:
        def __str__(self):
            raise RuntimeError("boom")

    assert isinstance(redact_text("ok"), str)
    redact({"k": Weird()})
    redact({"k": BadStr()})
    contains_secret(BadStr())
    redact(("tuple", [OPENAI_KEY]))
    redact({"s": {"x", "y"}})


# --------------------------------------------------------------------------
# Regression (V5 integration): the [REDACTED] marker is never itself a secret.
# `contains_secret` used to return True for already-redacted assignments because
# the generic-credential pattern captured the marker as the matched "secret".
# --------------------------------------------------------------------------


def test_contains_secret_marker_alone_is_false():
    assert contains_secret(REDACTED) is False
    assert contains_secret("[REDACTED]") is False


def test_contains_secret_sensitive_key_with_marker_value_is_false():
    assert contains_secret('{"password": "[REDACTED]"}') is False


def test_contains_secret_bare_marker_assignment_is_false():
    assert contains_secret("api_key=[REDACTED]") is False


def test_contains_secret_after_full_redaction_is_false():
    secret = "sk-proj-ZZZZZZZZZZZZZZZZZZZZZZZZ"
    out = redact({"password": "hunter2", "api_key": secret})

    text = str(out)
    assert "hunter2" not in text
    assert secret not in text
    assert contains_secret(out) is False


def test_contains_secret_real_secret_next_to_marker_is_true():
    mixed = "password=[REDACTED] api_key=sk-proj-AMBIGUOUS1234567890"
    assert contains_secret(mixed) is True


def test_redact_mixed_marker_and_real_secret_removes_secret():
    mixed = "password=[REDACTED] api_key=sk-proj-AMBIGUOUS1234567890"
    out = redact_text(mixed)

    assert "sk-proj-AMBIGUOUS1234567890" not in out
    assert REDACTED in out
    assert contains_secret(out) is False
