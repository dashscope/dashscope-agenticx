# -*- coding: utf-8 -*-
"""Unit tests for the sensitive data sanitizer module."""

import pytest

from dashscope_agenticx.utils import sanitize, sanitize_text


TEST_SK_KEY = "sk-" + "abc123def456ghi789jkl012"
TEST_SK_ANT_KEY = "sk-ant-api03-" + "x" * 25
TEST_SK_PROJ_KEY = "sk-proj-" + "x" * 32
TEST_SK_LIVE_KEY = "sk-live-" + "x" * 28
TEST_AWS_ACCESS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
TEST_AWS_TEMP_ACCESS_KEY = "ASIA" + "IOSFODNN7EXAMPLE"
TEST_BEARER_TOKEN = "example-bearer-token-1234567890"


class TestSanitizeText:
    """Tests for sanitize_text() function."""

    def test_sk_generic_key(self):
        """Generic sk-* keys should be redacted."""
        assert sanitize_text(TEST_SK_KEY) == "[REDACTED]"
        assert (
            sanitize_text("sk-" + "abcdefghijklmnopqrstuvwxyz") == "[REDACTED]"
        )

    def test_sk_ant_key(self):
        """Anthropic sk-ant-* keys should be redacted."""
        assert sanitize_text(TEST_SK_ANT_KEY) == "[REDACTED]"

    def test_sk_proj_key(self):
        """OpenAI sk-proj-* keys should be redacted."""
        assert sanitize_text(TEST_SK_PROJ_KEY) == "[REDACTED]"

    def test_sk_with_hyphen(self):
        """sk-* keys with hyphens should be redacted."""
        assert sanitize_text(TEST_SK_LIVE_KEY) == "[REDACTED]"

    def test_sk_too_short(self):
        """Very short sk-* strings (< 6 chars after prefix) should NOT be redacted."""
        assert sanitize_text("sk-short") == "sk-short"
        assert sanitize_text("sk-abc") == "sk-abc"

    def test_sk_short_key(self):
        """sk-* keys with 6+ chars after prefix should be redacted."""
        assert sanitize_text("sk-1232543") == "[REDACTED]"
        assert sanitize_text("sk-abcdef") == "[REDACTED]"
        assert sanitize_text("sk-123456") == "[REDACTED]"

    def test_ltai_key(self):
        """Alibaba Cloud LTAI* keys should be redacted."""
        assert sanitize_text(f"LTAI{'x' * 17}") == "[REDACTED]"
        assert sanitize_text(f"LTAI5t{'x' * 18}") == "[REDACTED]"

    def test_aws_key(self):
        """AWS AKIA/ASIA keys should be redacted."""
        assert sanitize_text(TEST_AWS_ACCESS_KEY) == "[REDACTED]"
        assert sanitize_text(TEST_AWS_TEMP_ACCESS_KEY) == "[REDACTED]"

    def test_github_token(self):
        """GitHub tokens should be redacted."""
        for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
            assert sanitize_text(f"{prefix}{'x' * 42}") == "[REDACTED]"

    def test_bearer_token(self):
        """Bearer tokens should be redacted, keeping 'Bearer ' prefix."""
        result = sanitize_text(f"Bearer {TEST_BEARER_TOKEN}")
        assert result == "Bearer [REDACTED]"

    def test_key_value_api_key(self):
        """api_key=... patterns should be redacted."""
        assert sanitize_text(f"api_key={TEST_SK_KEY}") == "api_key=[REDACTED]"
        assert (
            sanitize_text(f"api_key: {TEST_SK_KEY}") == "api_key: [REDACTED]"
        )
        assert (
            sanitize_text(f'api_key="{TEST_SK_KEY}"') == 'api_key="[REDACTED]"'
        )
        assert sanitize_text(f"API_KEY={TEST_SK_KEY}") == "API_KEY=[REDACTED]"

    def test_key_value_password(self):
        """password=... patterns should be redacted."""
        assert (
            sanitize_text("password: supersecretpassword123")
            == "password: [REDACTED]"
        )
        assert (
            sanitize_text('password="mysecretpassword123"')
            == 'password="[REDACTED]"'
        )
        assert (
            sanitize_text("password=verylongpassword12345")
            == "password=[REDACTED]"
        )

    def test_key_value_token(self):
        """token=... patterns should be redacted."""
        assert sanitize_text("token=abcdefghijklmnop") == "token=[REDACTED]"
        assert (
            sanitize_text("access_token=abcdefghijklmnop")
            == "access_token=[REDACTED]"
        )

    def test_key_value_secret(self):
        """secret=... patterns should be redacted."""
        assert sanitize_text("secret=abcdefghijklmnop") == "secret=[REDACTED]"
        assert (
            sanitize_text("client_secret=abcdefghijklmnop")
            == "client_secret=[REDACTED]"
        )

    def test_short_credential_assignments_are_redacted(self):
        assert sanitize_text("api_key=short") == "api_key=[REDACTED]"
        assert sanitize_text("password=1234567") == "password=[REDACTED]"

    @pytest.mark.parametrize(
        "key_type",
        [
            "PRIVATE KEY",
            "RSA PRIVATE KEY",
            "EC PRIVATE KEY",
            "OPENSSH PRIVATE KEY",
        ],
    )
    def test_multiline_private_keys_are_redacted_in_full(self, key_type):
        private_key = (
            "-----BEGIN "
            + key_type
            + "-----\nexample-body\n-----END "
            + key_type
            + "-----"
        )
        assert (
            sanitize_text("before\n" + private_key + "\nafter")
            == "before\n[REDACTED]\nafter"
        )

    def test_truncated_private_key_is_redacted(self):
        private_key = "-----BEGIN " + "RSA PRIVATE KEY-----\nexample-body"
        assert sanitize_text(private_key) == "[REDACTED]"

    def test_normal_text_unchanged(self):
        """Normal text without secrets should not be changed."""
        assert sanitize_text("正常文本不受影响") == "正常文本不受影响"
        assert (
            sanitize_text("This is a normal message")
            == "This is a normal message"
        )
        assert sanitize_text("Hello, world!") == "Hello, world!"

    def test_multiple_secrets_in_text(self):
        """Multiple secrets in one text should all be redacted."""
        text = f"key1: {TEST_SK_KEY}, key2: {TEST_SK_PROJ_KEY}"
        result = sanitize_text(text)
        assert "sk-abc" not in result
        assert "sk-proj" not in result
        assert result.count("[REDACTED]") == 2

    def test_secret_in_sentence(self):
        """Secrets embedded in sentences should be redacted."""
        text = f"My API key is {TEST_SK_KEY}, please use it carefully."
        result = sanitize_text(text)
        assert "sk-abc" not in result
        assert "My API key is [REDACTED], please use it carefully." == result

    def test_non_string_passthrough(self):
        """Non-string inputs should be returned as-is."""
        assert sanitize_text(123) == 123
        assert sanitize_text(None) is None
        assert sanitize_text(True) is True


class TestSanitizeRecursive:
    """Tests for sanitize() recursive function."""

    def test_sanitize_string(self):
        """Strings should be sanitized."""
        assert sanitize(TEST_SK_KEY) == "[REDACTED]"

    def test_sanitize_list(self):
        """Lists should be recursively sanitized."""
        result = sanitize([TEST_SK_KEY, "normal text"])
        assert result == ["[REDACTED]", "normal text"]

    def test_sanitize_dict(self):
        """Dicts should be recursively sanitized."""
        result = sanitize(
            {"key": TEST_SK_KEY, "message": "normal text"},
        )
        assert result == {"key": "[REDACTED]", "message": "normal text"}

    def test_sanitize_nested_structure(self):
        """Complex nested structures should be recursively sanitized."""
        data = [
            {
                "role": "user",
                "content": f"My key is {TEST_SK_KEY}",
            },
            {
                "role": "assistant",
                "content": "Got it",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "run_command",
                            "arguments": '{"command": "echo $API_KEY"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "content": TEST_SK_LIVE_KEY,
                "name": "run_command",
            },
        ]
        result = sanitize(data)

        # Check user message
        assert "sk-abc" not in result[0]["content"]
        assert "[REDACTED]" in result[0]["content"]

        # Check assistant message (should not have secrets in this example)
        assert result[1]["content"] == "Got it"

        # Check tool result
        assert result[2]["content"] == "[REDACTED]"

    def test_sanitize_non_string_values(self):
        """Non-string values in structures should be preserved."""
        result = sanitize(
            {
                "count": 42,
                "enabled": True,
                "items": [1, 2, 3],
                "nested": {"value": None},
            },
        )
        assert result == {
            "count": 42,
            "enabled": True,
            "items": [1, 2, 3],
            "nested": {"value": None},
        }

    def test_sanitize_empty_structures(self):
        """Empty structures should be handled correctly."""
        assert sanitize([]) == []
        assert sanitize({}) == {}
        assert sanitize("") == ""

    @pytest.mark.parametrize(
        "field",
        [
            "api_key",
            "X-API-Key",
            "password",
            "authorization",
            "access_token",
            "client_secret",
        ],
    )
    def test_sanitize_credential_fields_without_known_token_patterns(
        self,
        field,
    ):
        original = {"arguments": {field: "short"}}
        assert sanitize(original) == {"arguments": {field: "[REDACTED]"}}
        assert original["arguments"][field] == "short"

    def test_sanitize_preserves_noncredential_field_names(self):
        original = {
            "key": "record-id",
            "keyboard": "native",
            "token_usage": 42,
            "api_key": None,
        }
        assert sanitize(original) == original

    def test_sanitize_deeply_nested(self):
        """Deeply nested structures should be sanitized."""
        data = {
            "level1": {
                "level2": {"level3": [{"key": TEST_SK_KEY}]},
            },
        }
        result = sanitize(data)
        assert result["level1"]["level2"]["level3"][0]["key"] == "[REDACTED]"


class TestRealWorldScenarios:
    """Tests for real-world usage scenarios."""

    def test_session_messages(self):
        """Simulate sanitizing a conversation session."""
        messages = [
            {
                "role": "user",
                "content": f"Help me configure my API key {TEST_SK_KEY}",
            },
            {"role": "assistant", "content": "I'll help you set it up."},
            {
                "role": "tool",
                "content": f"Config saved: api_key={TEST_SK_KEY}",
            },
            {"role": "assistant", "content": "Your key has been configured."},
        ]
        result = sanitize(messages)

        # Verify no secrets remain
        result_str = str(result)
        assert "sk-abc123" not in result_str
        assert result_str.count("[REDACTED]") >= 2

    def test_tool_output_with_secrets(self):
        """Simulate tool output containing secrets."""
        tool_output = f"""
Environment variables:
DASHSCOPE_API_KEY={TEST_SK_LIVE_KEY}
PATH=/usr/bin:/bin
HOME=/home/user
"""
        result = sanitize_text(tool_output)
        assert "sk-live-" not in result
        assert "DASHSCOPE_API_KEY=[REDACTED]" in result
        assert "PATH=/usr/bin:/bin" in result  # Normal vars unchanged

    def test_config_file_content(self):
        """Simulate config file content with secrets."""
        config = f"""
[api]
endpoint = https://api.example.com
api_key = {TEST_SK_PROJ_KEY}
timeout = 30

[auth]
bearer_token = {TEST_BEARER_TOKEN}
"""
        result = sanitize_text(config)
        assert "sk-proj-" not in result
        assert TEST_BEARER_TOKEN not in result
        assert "api_key = [REDACTED]" in result
        assert "endpoint = https://api.example.com" in result

    def test_error_message_with_secrets(self):
        """Simulate error messages containing secrets."""
        error = (
            f"Authentication failed for key {TEST_SK_KEY}: Invalid credentials"
        )
        result = sanitize_text(error)
        assert "sk-abc123" not in result
        assert "[REDACTED]" in result
        assert "Authentication failed for key" in result
        assert "Invalid credentials" in result


class TestEdgeCases:
    """Tests for edge cases and potential issues."""

    def test_whitespace_handling(self):
        """Whitespace around secrets should be preserved."""
        text = f"  {TEST_SK_KEY}  "
        result = sanitize_text(text)
        assert result == "  [REDACTED]  "

    def test_newlines_preserved(self):
        """Newlines should be preserved."""
        text = f"Line 1\n{TEST_SK_KEY}\nLine 3"
        result = sanitize_text(text)
        assert "sk-abc" not in result
        assert result.count("\n") == 2

    def test_unicode_text(self):
        """Unicode text should be handled correctly."""
        text = f"我的 API key 是 {TEST_SK_KEY}，请注意保管"
        result = sanitize_text(text)
        assert "sk-abc" not in result
        assert "我的 API key 是 [REDACTED]，请注意保管" == result

    def test_partial_match_no_redact(self):
        """Partial matches should not be redacted."""
        # Too short
        assert sanitize_text("sk-abc") == "sk-abc"
        # Not a recognized pattern
        assert sanitize_text("not-a-secret-key") == "not-a-secret-key"

    def test_case_insensitivity(self):
        """Key-value patterns should be case-insensitive."""
        assert (
            sanitize_text("API_KEY=abcdefghijklmnop") == "API_KEY=[REDACTED]"
        )
        assert (
            sanitize_text("Api_Key=abcdefghijklmnop") == "Api_Key=[REDACTED]"
        )
        assert (
            sanitize_text("api_key=abcdefghijklmnop") == "api_key=[REDACTED]"
        )

    def test_special_chars_in_context(self):
        """Secrets with special chars in context should be handled."""
        text = f'export API_KEY="{TEST_SK_KEY}"'
        result = sanitize_text(text)
        assert "sk-abc" not in result
        assert "[REDACTED]" in result
