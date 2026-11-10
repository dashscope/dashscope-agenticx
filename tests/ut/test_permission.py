# -*- coding: utf-8 -*-
"""Tests for the permission policy system."""

from __future__ import annotations

import pytest

from dashscope_agenticx.permission import (
    PermissionPolicy,
    get_permission_policy,
    set_permission_policy,
)

# ── PermissionPolicy.check_tool ─────────────────────────────────────────


class TestCheckTool:
    def test_allow_rule(self):
        policy = PermissionPolicy()
        policy.add_tool_rule("read_file", "allow")
        assert policy.check_tool("read_file") == "allow"

    def test_deny_rule(self):
        policy = PermissionPolicy()
        policy.add_tool_rule("delete_file", "deny")
        assert policy.check_tool("delete_file") == "deny"

    def test_confirm_rule(self):
        policy = PermissionPolicy()
        policy.add_tool_rule("write_file", "confirm")
        assert policy.check_tool("write_file") == "confirm"

    def test_no_rule_returns_none(self):
        policy = PermissionPolicy()
        assert policy.check_tool("unknown_tool") is None

    def test_multiple_rules(self):
        policy = PermissionPolicy()
        policy.add_tool_rule("read_file", "allow")
        policy.add_tool_rule("write_file", "confirm")
        policy.add_tool_rule("delete_file", "deny")
        assert policy.check_tool("read_file") == "allow"
        assert policy.check_tool("write_file") == "confirm"
        assert policy.check_tool("delete_file") == "deny"

    def test_rule_overwrite(self):
        policy = PermissionPolicy()
        policy.add_tool_rule("bash", "allow")
        policy.add_tool_rule("bash", "deny")
        assert policy.check_tool("bash") == "deny"


# ── PermissionPolicy.check_command ──────────────────────────────────────


class TestCheckCommand:
    def test_allow_prefix(self):
        policy = PermissionPolicy()
        policy.add_command_rule("git status", "allow")
        assert policy.check_command("git status") == "allow"

    def test_deny_prefix(self):
        policy = PermissionPolicy()
        policy.add_command_rule("rm -rf", "deny")
        assert policy.check_command("rm -rf /tmp") == "deny"

    def test_confirm_prefix(self):
        policy = PermissionPolicy()
        policy.add_command_rule("docker", "confirm")
        assert policy.check_command("docker run -it ubuntu") == "confirm"

    def test_no_rule_returns_none(self):
        policy = PermissionPolicy()
        assert policy.check_command("ls -la") is None

    def test_prefix_matching_with_leading_whitespace(self):
        policy = PermissionPolicy()
        policy.add_command_rule("git", "allow")
        # The check_command strips the command before matching
        assert policy.check_command("  git status") == "allow"

    def test_multiple_prefixes(self):
        policy = PermissionPolicy()
        policy.add_command_rule("git status", "allow")
        policy.add_command_rule("git push", "deny")
        assert policy.check_command("git status") == "allow"
        assert policy.check_command("git push origin main") == "deny"

    def test_partial_prefix_does_not_match(self):
        policy = PermissionPolicy()
        policy.add_command_rule("git push", "deny")
        # "git status" does not start with "git push"
        assert policy.check_command("git status") is None


# ── PermissionPolicy.add_tool_rule / add_command_rule ───────────────────


class TestAddRules:
    def test_add_tool_rule_valid(self):
        policy = PermissionPolicy()
        for decision in ("allow", "deny", "confirm"):
            policy.add_tool_rule(f"tool_{decision}", decision)
            assert policy.check_tool(f"tool_{decision}") == decision

    def test_add_tool_rule_invalid_decision(self):
        policy = PermissionPolicy()
        with pytest.raises(ValueError, match="allow/deny/confirm"):
            policy.add_tool_rule("bash", "maybe")

    def test_add_tool_rule_empty_decision(self):
        policy = PermissionPolicy()
        with pytest.raises(ValueError):
            policy.add_tool_rule("bash", "")

    def test_add_command_rule_valid(self):
        policy = PermissionPolicy()
        for decision in ("allow", "deny", "confirm"):
            policy.add_command_rule(f"cmd_{decision}", decision)
            assert policy.check_command(f"cmd_{decision}") == decision

    def test_add_command_rule_invalid_decision(self):
        policy = PermissionPolicy()
        with pytest.raises(ValueError, match="allow/deny/confirm"):
            policy.add_command_rule("ls", "always")

    def test_add_command_rule_invalid_decision_empty(self):
        policy = PermissionPolicy()
        with pytest.raises(ValueError):
            policy.add_command_rule("ls", "")


# ── PermissionPolicy defaults ───────────────────────────────────────────


class TestPermissionPolicyDefaults:
    def test_default_tool_rules_empty(self):
        policy = PermissionPolicy()
        assert policy.tool_rules == {}

    def test_default_command_rules_empty(self):
        policy = PermissionPolicy()
        assert policy.command_rules == {}

    def test_independent_instances(self):
        p1 = PermissionPolicy()
        p2 = PermissionPolicy()
        p1.add_tool_rule("bash", "deny")
        assert p2.check_tool("bash") is None  # p2 unaffected


# ── Global policy ───────────────────────────────────────────────────────


class TestGlobalPolicy:
    def test_get_permission_policy_returns_instance(self):
        policy = get_permission_policy()
        assert isinstance(policy, PermissionPolicy)

    def test_set_permission_policy_replaces_global(self):
        original = get_permission_policy()
        try:
            custom = PermissionPolicy()
            custom.add_tool_rule("custom_tool", "deny")
            set_permission_policy(custom)
            assert get_permission_policy() is custom
            assert get_permission_policy().check_tool("custom_tool") == "deny"
        finally:
            set_permission_policy(original)

    def test_get_permission_policy_returns_same_instance(self):
        assert get_permission_policy() is get_permission_policy()
