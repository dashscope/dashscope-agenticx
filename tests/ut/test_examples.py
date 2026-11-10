# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.cli.examples — _KNOWN_EXAMPLES, _get_examples_dir,
_list_examples, _print_example_list, _handle_example_command."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize(
    "relative,content",
    [
        (
            "hooks.toml",
            '[hooks]\n[[hooks.on_message]]\naction = "run"\ncommand = "echo forbidden"\n',
        ),
        (
            "custom-extensions.toml",
            '[[shell_tools]]\nname = "danger"\ncommand_template = "echo forbidden"\n',
        ),
        ("skills/unsafe/tools.py", 'raise RuntimeError("must not import")\n'),
    ],
)
@pytest.mark.parametrize("force", [False, True])
def test_expert_templates_cannot_install_executable_extensions(
    tmp_path,
    relative,
    content,
    force,
):
    from dashscope_agenticx.cli.examples import _copy_example_flat

    source = tmp_path / "source" / ".agenticx"
    path = source / relative
    path.parent.mkdir(parents=True)
    path.write_text(content)
    destination = tmp_path / "workspace"
    with pytest.raises(ValueError, match="templates cannot"):
        _copy_example_flat(source.parent, destination, force=force)
    assert not destination.exists()


def test_setup_template_preserves_config_and_merges_declarations(
    tmp_path,
    monkeypatch,
):
    from dashscope_agenticx.extensions import CustomExtensions, CustomProvider
    from dashscope_agenticx.utils.toml import load_toml

    monkeypatch.setattr(
        "dashscope_agenticx.extensions._current",
        CustomExtensions(
            providers=[
                CustomProvider(
                    name="global-provider",
                    base_url="https://global.example/v1",
                    auth=False,
                ),
            ],
        ),
    )
    source = tmp_path / "template"
    source.mkdir()
    (source / "config.toml").write_text('provider = "wrong"\n')
    (source / "system-prompt.md").write_text("Template role")
    (source / "custom-extensions.toml").write_text(
        '[[ providers ]]\nname = "existing"\nbase_url = "https://wrong.example/v1"\nauth = false\n'
        '[[providers]]\nname = "global-provider"\nbase_url = "https://wrong.example/v1"\nauth = false\n'
        '[[providers]]\nname = "added"\nbase_url = "https://added.example/v1"\nauth = false\n'
        '[[capabilities]]\nkey = "template.http"\nauth = "none"\n'
        '[[capabilities.tools]]\nname = "request"\nmethod = "GET"\nendpoint = "https://api.example/v1"\n'
        '[[skills]]\nname = "template-skill"\nprompt_template = "Skill instructions"\n',
    )
    destination = tmp_path / "workspace"
    workspace_dir = destination / ".agenticx"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "config.toml").write_text(
        'provider = "existing"\nuser_name = "keep-user"\n',
    )
    existing = (
        "# Keep this comment and credential reference\n[[providers]]\n"
        'name = "existing"\napi_key = "KEYRING:v1:test"\n'
        '[[shell_tools]]\nname = "trusted-local-shell"\n'
        'command_template = "echo local"\n'
    )
    (workspace_dir / "custom-extensions.toml").write_text(existing)

    assert _copy_example_flat(
        source,
        destination,
        force=True,
        show_usage=False,
        preserve_configuration=True,
    )

    assert (
        workspace_dir / "config.toml"
    ).read_text() == 'provider = "existing"\nuser_name = "keep-user"\n'
    assert (
        (workspace_dir / "custom-extensions.toml")
        .read_text()
        .startswith(existing)
    )
    data = load_toml(workspace_dir / "custom-extensions.toml")
    assert [provider["name"] for provider in data["providers"]] == [
        "existing",
        "added",
    ]
    assert data["providers"][0]["api_key"] == "KEYRING:v1:test"
    assert (
        data["capabilities"][0]["tools"][0]["endpoint"]
        == "https://api.example/v1"
    )
    assert data["skills"][0]["name"] == "template-skill"
    assert [tool["name"] for tool in data["shell_tools"]] == [
        "trusted-local-shell",
    ]
    assert (
        workspace_dir / "backup" / "custom-extensions.toml"
    ).read_text() == existing
    assert not (workspace_dir / "backup" / "config.toml").exists()


@pytest.mark.parametrize(
    "invalid",
    ["[[providers]\n", 'providers = "not a list"\n'],
)
@pytest.mark.parametrize("invalid_target", [False, True])
def test_setup_template_invalid_extensions_do_not_modify_workspace(
    tmp_path,
    invalid,
    invalid_target,
):
    source = tmp_path / "template"
    source.mkdir()
    (source / "system-prompt.md").write_text("New role")
    valid = '[[providers]]\nname = "existing"\nauth = false\n'
    (source / "custom-extensions.toml").write_text(
        valid if invalid_target else invalid,
    )
    destination = tmp_path / "workspace"
    workspace_dir = destination / ".agenticx"
    workspace_dir.mkdir(parents=True)
    original = invalid if invalid_target else valid
    (workspace_dir / "custom-extensions.toml").write_text(original)
    (workspace_dir / "system-prompt.md").write_text("Old role")

    with pytest.raises(ValueError):
        _copy_example_flat(
            source,
            destination,
            force=True,
            preserve_configuration=True,
        )

    assert (workspace_dir / "custom-extensions.toml").read_text() == original
    assert (workspace_dir / "system-prompt.md").read_text() == "Old role"
    assert not (workspace_dir / "backup").exists()


def test_setup_template_conflict_defaults_to_cancel(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = tmp_path / "template"
    source.mkdir()
    (source / "system-prompt.md").write_text("New role")
    destination = tmp_path / "workspace"
    workspace_dir = destination / ".agenticx"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "system-prompt.md").write_text("Old role")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: prompts.append(prompt) or "",
    )

    assert not _copy_example_flat(
        source,
        destination,
        force=False,
        show_usage=False,
        preserve_configuration=True,
    )

    assert prompts == ["Continue? [y/N] "]
    assert "merge/update" in capsys.readouterr().out
    assert (workspace_dir / "system-prompt.md").read_text() == "Old role"


from dashscope_agenticx.cli.examples import (
    _DEFAULT_EXAMPLES_BRANCH,
    _DEFAULT_EXAMPLES_REPO,
    _KNOWN_EXAMPLES,
    _get_examples_dir,
    _get_configured_repo,
    _handle_example_command,
    _clone_example_to_temp,
    _copy_example_flat,
    _list_examples,
    _list_remote_examples,
    _print_example_list,
    _restore_example_backup,
    _selected_expert,
)


@pytest.fixture(autouse=True)
def disable_remote_catalog_queries(monkeypatch):
    monkeypatch.setattr(
        "dashscope_agenticx.cli.examples._list_remote_examples",
        lambda repo, branch: [],
    )


class TestExpertIdentity:
    @pytest.mark.parametrize("name", _KNOWN_EXAMPLES)
    def test_explicit_name_overrides_bundled_identity(self, tmp_path, name):
        source = tmp_path / "template" / ".agenticx"
        source.mkdir(parents=True)
        (source / "expert.toml").write_text('name = "other-expert"\n')
        (source / "system-prompt.md").write_text("Template role")
        destination = tmp_path / "workspace"

        assert _copy_example_flat(
            source.parent,
            destination,
            force=True,
            expert_name=name,
        )
        assert _selected_expert(destination / ".agenticx") == name
        assert (
            destination / ".agenticx/system-prompt.md"
        ).read_text() == "Template role"

    @pytest.mark.parametrize(
        "contents",
        [
            None,
            "",
            "broken = [",
            'name = ""',
            "name = 42",
            "name = []",
            'name = "../outside"',
            'name = "[red]spoof[/red]"',
            'name = "bad\\nname"',
        ],
    )
    def test_unrecorded_or_invalid_identity_is_not_displayed(
        self,
        tmp_path,
        contents,
    ):
        workspace = tmp_path / ".agenticx"
        workspace.mkdir()
        if contents is not None:
            (workspace / "expert.toml").write_text(contents)

        assert _selected_expert(workspace) == ""

    @pytest.mark.parametrize("symlink_workspace", [False, True])
    def test_identity_does_not_follow_symlinks(
        self,
        tmp_path,
        symlink_workspace,
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "expert.toml").write_text('name = "outside-expert"\n')
        workspace = tmp_path / ".agenticx"
        if symlink_workspace:
            workspace.symlink_to(outside, target_is_directory=True)
        else:
            workspace.mkdir()
            (workspace / "expert.toml").symlink_to(outside / "expert.toml")

        assert _selected_expert(workspace) == ""

    def test_cancelled_merge_preserves_identity(self, tmp_path, monkeypatch):
        source = tmp_path / "template"
        source.mkdir()
        (source / "system-prompt.md").write_text("New role")
        destination = tmp_path / "workspace"
        workspace = destination / ".agenticx"
        workspace.mkdir(parents=True)
        (workspace / "expert.toml").write_text('name = "old-expert"\n')
        (workspace / "system-prompt.md").write_text("Old role")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt: "n")

        assert not _copy_example_flat(
            source,
            destination,
            force=False,
            expert_name="new-expert",
        )
        assert _selected_expert(workspace) == "old-expert"
        assert (workspace / "system-prompt.md").read_text() == "Old role"
        assert not (workspace / "backup").exists()

    @pytest.mark.parametrize("relative", ["expert.toml", "backup/expert.toml"])
    def test_download_rejects_symlinked_identity_paths(
        self,
        tmp_path,
        relative,
    ):
        source = tmp_path / "template"
        source.mkdir()
        (source / "system-prompt.md").write_text("New role")
        destination = tmp_path / "workspace"
        workspace = destination / ".agenticx"
        workspace.mkdir(parents=True)
        (workspace / "system-prompt.md").write_text("Old role")
        outside = tmp_path / "outside.toml"
        outside.write_text('name = "outside-expert"\n')
        link = workspace / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)

        with pytest.raises(ValueError, match="symbolic links"):
            _copy_example_flat(
                source,
                destination,
                force=True,
                expert_name="new-expert",
            )

        assert outside.read_text() == 'name = "outside-expert"\n'
        assert (workspace / "system-prompt.md").read_text() == "Old role"
        assert link.is_symlink()

    @pytest.mark.parametrize("recorded", [False, True])
    def test_restore_restores_or_removes_identity(self, tmp_path, recorded):
        source = tmp_path / "template"
        source.mkdir()
        (source / "system-prompt.md").write_text("New role")
        (source / "new-skill.md").write_text("New skill")
        destination = tmp_path / "workspace"
        workspace = destination / ".agenticx"
        workspace.mkdir(parents=True)
        (workspace / "system-prompt.md").write_text("Old role")
        if recorded:
            (workspace / "expert.toml").write_text('name = "old-expert"\n')

        assert _copy_example_flat(
            source,
            destination,
            force=True,
            expert_name="new-expert",
        )
        assert _selected_expert(workspace) == "new-expert"
        assert _restore_example_backup(destination)
        assert _selected_expert(workspace) == (
            "old-expert" if recorded else ""
        )
        assert (workspace / "expert.toml").exists() == recorded
        assert (workspace / "system-prompt.md").read_text() == "Old role"
        assert not (workspace / "new-skill.md").exists()

    def test_invalid_profile_preserves_identity(self, tmp_path):
        source = tmp_path / "template"
        source.mkdir()
        (source / "custom-extensions.toml").write_text("broken = [")
        destination = tmp_path / "workspace"
        workspace = destination / ".agenticx"
        workspace.mkdir(parents=True)
        (workspace / "expert.toml").write_text('name = "old-expert"\n')

        with pytest.raises(ValueError):
            _copy_example_flat(
                source,
                destination,
                force=True,
                preserve_configuration=True,
                expert_name="new-expert",
            )
        assert _selected_expert(workspace) == "old-expert"
        assert not (workspace / "backup").exists()


# ---------------------------------------------------------------------------
# _KNOWN_EXAMPLES
# ---------------------------------------------------------------------------


class TestKnownExamples:
    def test_contains_all_apps(self):
        expected = {
            "basic-chat",
            "dashscope-sdk-expert",
            "bailian-expert",
        }
        assert set(_KNOWN_EXAMPLES) == expected

    def test_has_three_entries(self):
        assert len(_KNOWN_EXAMPLES) == 3

    @pytest.mark.parametrize(
        "name",
        [
            "basic-chat",
            "dashscope-sdk-expert",
            "bailian-expert",
        ],
    )
    def test_each_example_present(self, name):
        assert name in _KNOWN_EXAMPLES

    def test_is_list_of_strings(self):
        assert isinstance(_KNOWN_EXAMPLES, list)
        assert all(isinstance(e, str) for e in _KNOWN_EXAMPLES)

    def test_no_duplicates(self):
        assert len(_KNOWN_EXAMPLES) == len(set(_KNOWN_EXAMPLES))


# ---------------------------------------------------------------------------
# _get_examples_dir
# ---------------------------------------------------------------------------


class TestGetExamplesDir:
    def test_returns_path(self):
        result = _get_examples_dir()
        assert isinstance(result, Path)

    def test_returns_path_even_if_not_exists(self):
        """The function should always return a Path, even if the directory
        does not exist on disk."""
        with patch.object(Path, "is_dir", return_value=False):
            result = _get_examples_dir()
        assert isinstance(result, Path)

    def test_dev_mode_path_checked_first(self):
        """Local downloads use the canonical apps directory."""
        fake_root = Path("/fake/project/root")
        dev_examples = fake_root / "apps"

        # Simulate: __file__ is at .../src/dashscope_agenticx/cli/examples.py
        # project_root = parent.parent.parent.parent
        with patch.object(Path, "resolve") as mock_resolve:
            mock_resolve.return_value = (
                fake_root
                / "src"
                / "dashscope_agenticx"
                / "cli"
                / "examples.py"
            )
            with patch.object(Path, "is_dir", return_value=True):
                result = _get_examples_dir()
            assert result == dev_examples


# ---------------------------------------------------------------------------
# _list_examples
# ---------------------------------------------------------------------------


class TestListExamples:
    def test_returns_list(self):
        result = _list_examples()
        assert isinstance(result, list)

    def test_returns_sorted_list(self):
        result = _list_examples()
        if len(result) > 1:
            assert result == sorted(result)

    def test_all_entries_are_strings(self):
        result = _list_examples()
        assert all(isinstance(e, str) for e in result)

    def test_excludes_hidden_directories(self, tmp_path):
        """Directories starting with . or _ should be excluded."""
        (tmp_path / "valid-example").mkdir()
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "_private").mkdir()

        with patch(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            return_value=tmp_path,
        ):
            result = _list_examples()
        assert "valid-example" in result
        assert ".hidden" not in result
        assert "_private" not in result

    def test_empty_when_dir_missing(self):
        """When the examples directory does not exist, return empty list."""
        nonexistent = Path("/nonexistent/path/that/should/not/exist")
        with patch(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            return_value=nonexistent,
        ):
            result = _list_examples()
        assert result == []

    def test_only_directories_listed(self, tmp_path):
        """Files (non-directories) should not appear in the listing."""
        (tmp_path / "real-dir").mkdir()
        (tmp_path / "some-file.txt").write_text("not a dir")

        with patch(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            return_value=tmp_path,
        ):
            result = _list_examples()
        assert "real-dir" in result
        assert "some-file.txt" not in result


# ---------------------------------------------------------------------------
# _print_example_list
# ---------------------------------------------------------------------------


class TestPrintExampleList:
    def test_does_not_raise(self):
        """_print_example_list should run without raising."""
        _print_example_list()  # prints to console; just verify no exception

    def test_uses_known_examples_when_dir_empty(self, tmp_path):
        """When _list_examples returns [], the fallback is _KNOWN_EXAMPLES."""
        with patch(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            return_value=tmp_path,
        ):
            _print_example_list()  # should not raise even with empty dir


# ---------------------------------------------------------------------------
# _handle_example_command
# ---------------------------------------------------------------------------


class TestHandleExampleCommand:
    def test_no_args_shows_list(self):
        """No arguments should trigger the list display (no exception)."""
        _handle_example_command([])

    def test_list_subcommand(self):
        """`list` subcommand should not raise."""
        _handle_example_command(["list"])

    def test_list_alias_dash_l(self):
        _handle_example_command(["-l"])

    def test_list_alias_dash_dash_list(self):
        _handle_example_command(["--list"])

    def test_download_without_name_does_not_raise(self):
        """`download` without a name should print an error, not crash."""
        _handle_example_command(["download"])

    def test_unknown_subcommand_does_not_raise(self):
        """Unknown subcommand should print a hint, not crash."""
        _handle_example_command(["bogus-subcommand"])

    def _make_example(self, tmp_path, name, files: dict[str, str]):
        examples_dir = tmp_path / "examples"
        ex_dir = examples_dir / name
        for rel, content in files.items():
            f = ex_dir / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        return examples_dir

    def test_download_copies_local_example_flat(self, tmp_path, monkeypatch):
        """Example files are merged into the target's .agenticx/ directory."""
        examples_dir = self._make_example(
            tmp_path,
            "test-ex",
            {"README.md": "# Test Example"},
        )
        target_dir = tmp_path / "output"

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )

        _handle_example_command(
            ["download", "test-ex", "--target", str(target_dir)],
        )

        assert (
            target_dir / ".agenticx" / "README.md"
        ).read_text() == "# Test Example"
        assert not (target_dir / "test-ex").exists()
        assert _selected_expert(target_dir / ".agenticx") == "test-ex"
        assert not (tmp_path / ".agenticx/expert.toml").exists()

    @pytest.mark.parametrize("show_usage", [False, True])
    def test_download_usage_hints(
        self,
        tmp_path,
        monkeypatch,
        capsys,
        show_usage,
    ):
        examples_dir = self._make_example(
            tmp_path,
            "basic-chat",
            {"README.md": "# New profile"},
        )
        target_dir = tmp_path / "workspace"
        profile = target_dir / ".agenticx" / "README.md"
        profile.parent.mkdir(parents=True)
        profile.write_text("# Original profile", encoding="utf-8")
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples.sys.stdin.isatty",
            lambda: True,
        )
        monkeypatch.setattr("builtins.input", lambda prompt: "")

        _handle_example_command(
            ["download", "basic-chat", "--target", str(target_dir)],
            show_usage=show_usage,
        )

        assert profile.read_text(encoding="utf-8") == "# New profile"
        assert (profile.parent / "backup" / "README.md").read_text(
            encoding="utf-8",
        ) == "# Original profile"
        output = capsys.readouterr().out
        assert ("Expert copied to:" in output) is show_usage
        assert ("Overwritten files backed up to:" in output) is show_usage
        assert ("Expert downloaded." in output) is not show_usage
        assert ("/setup" in output) is show_usage
        assert ("/help" in output) is show_usage
        assert ("/expert restore" in output) is show_usage
        assert "Restart AgenticX" not in output

    def test_setup_download_conflict_has_no_command_hint(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        examples_dir = self._make_example(
            tmp_path,
            "basic-chat",
            {"README.md": "# New profile"},
        )
        target_dir = tmp_path / "workspace"
        profile = target_dir / ".agenticx" / "README.md"
        profile.parent.mkdir(parents=True)
        profile.write_text("# Original profile", encoding="utf-8")
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples.sys.stdin.isatty",
            lambda: False,
        )

        _handle_example_command(
            ["download", "basic-chat", "--target", str(target_dir)],
            show_usage=False,
        )

        assert profile.read_text(encoding="utf-8") == "# Original profile"
        output = capsys.readouterr().out
        assert "cancelled" in output
        assert "--force" not in output

    def test_download_target_alias_short_flag(self, tmp_path, monkeypatch):
        """The -t short flag should work as an alias for --target."""
        examples_dir = self._make_example(
            tmp_path,
            "short-ex",
            {"file.txt": "data"},
        )
        target_dir = tmp_path / "out2"

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )

        _handle_example_command(
            ["download", "short-ex", "-t", str(target_dir)],
        )

        assert (target_dir / ".agenticx" / "file.txt").exists()

    def test_download_skips_root_gitignore(self, tmp_path, monkeypatch):
        """The standalone-repo root .gitignore must not be merged."""
        examples_dir = self._make_example(
            tmp_path,
            "gi-ex",
            {
                ".gitignore": ".agenticx/*",
                "README.md": "doc",
                ".agenticx/skills/s.md": "s",
            },
        )
        target_dir = tmp_path / "out-gi"

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )

        _handle_example_command(
            ["download", "gi-ex", "--target", str(target_dir)],
        )

        assert not (target_dir / ".gitignore").exists()
        assert not (target_dir / ".agenticx" / ".gitignore").exists()
        assert (target_dir / ".agenticx" / "README.md").exists()
        assert (target_dir / ".agenticx" / "skills" / "s.md").exists()

    def test_download_conflict_non_tty_aborts(self, tmp_path, monkeypatch):
        """Conflicts + non-interactive stdin → abort, nothing changes."""
        examples_dir = self._make_example(
            tmp_path,
            "dup-ex",
            {"file.txt": "new", "other.txt": "other"},
        )
        target_dir = tmp_path / "out3"
        (target_dir / ".agenticx").mkdir(parents=True)
        (target_dir / ".agenticx" / "file.txt").write_text(
            "pre-existing",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"isatty": lambda self: False})(),
        )

        _handle_example_command(
            ["download", "dup-ex", "--target", str(target_dir)],
        )

        assert (
            target_dir / ".agenticx" / "file.txt"
        ).read_text() == "pre-existing"
        assert not (target_dir / ".agenticx" / "other.txt").exists()
        assert not (target_dir / ".agenticx" / "backup").exists()

    def test_download_conflict_force_backs_up(self, tmp_path, monkeypatch):
        """--force overwrites conflicts but backs up the originals first."""
        examples_dir = self._make_example(
            tmp_path,
            "force-ex",
            {"file.txt": "new", "sub/nested.txt": "nested"},
        )
        target_dir = tmp_path / "out4"
        (target_dir / ".agenticx" / "sub").mkdir(parents=True)
        (target_dir / ".agenticx" / "file.txt").write_text(
            "old-file",
            encoding="utf-8",
        )
        (target_dir / ".agenticx" / "sub" / "nested.txt").write_text(
            "old-nested",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )

        _handle_example_command(
            ["download", "force-ex", "--target", str(target_dir), "--force"],
        )

        assert (target_dir / ".agenticx" / "file.txt").read_text() == "new"
        assert (
            target_dir / ".agenticx" / "sub" / "nested.txt"
        ).read_text() == "nested"
        backup = target_dir / ".agenticx" / "backup"
        assert (backup / "file.txt").read_text() == "old-file"
        assert (backup / "sub" / "nested.txt").read_text() == "old-nested"

    def test_download_conflict_tty_confirm(self, tmp_path, monkeypatch):
        """TTY + user answers y → proceed with backup."""
        examples_dir = self._make_example(
            tmp_path,
            "tty-ex",
            {"file.txt": "new"},
        )
        target_dir = tmp_path / "out5"
        (target_dir / ".agenticx").mkdir(parents=True)
        (target_dir / ".agenticx" / "file.txt").write_text(
            "old",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"isatty": lambda self: True})(),
        )
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")

        _handle_example_command(
            ["download", "tty-ex", "--target", str(target_dir)],
        )

        assert (target_dir / ".agenticx" / "file.txt").read_text() == "new"
        assert (
            target_dir / ".agenticx" / "backup" / "file.txt"
        ).read_text() == "old"

    def test_download_conflict_tty_enter_defaults_yes(
        self,
        tmp_path,
        monkeypatch,
    ):
        """TTY + empty answer (Enter) → default Yes, proceed with backup."""
        examples_dir = self._make_example(
            tmp_path,
            "enter-ex",
            {"file.txt": "new"},
        )
        target_dir = tmp_path / "out6b"
        (target_dir / ".agenticx").mkdir(parents=True)
        (target_dir / ".agenticx" / "file.txt").write_text(
            "old",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"isatty": lambda self: True})(),
        )
        monkeypatch.setattr("builtins.input", lambda prompt="": "")

        _handle_example_command(
            ["download", "enter-ex", "--target", str(target_dir)],
        )

        assert (target_dir / ".agenticx" / "file.txt").read_text() == "new"
        assert (
            target_dir / ".agenticx" / "backup" / "file.txt"
        ).read_text() == "old"

    def test_download_conflict_tty_decline(self, tmp_path, monkeypatch):
        """TTY + user answers n → cancel, nothing changes."""
        examples_dir = self._make_example(
            tmp_path,
            "no-ex",
            {"file.txt": "new"},
        )
        target_dir = tmp_path / "out6"
        (target_dir / ".agenticx").mkdir(parents=True)
        (target_dir / ".agenticx" / "file.txt").write_text(
            "old",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"isatty": lambda self: True})(),
        )
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")

        _handle_example_command(
            ["download", "no-ex", "--target", str(target_dir)],
        )

        assert (target_dir / ".agenticx" / "file.txt").read_text() == "old"
        assert not (target_dir / ".agenticx" / "backup").exists()

    def test_download_skips_junk_files(self, tmp_path, monkeypatch):
        """__pycache__/.git/.pyc under the example must not be copied."""
        examples_dir = self._make_example(
            tmp_path,
            "junk-ex",
            {
                "README.md": "doc",
                "__pycache__/x.pyc": "junk",
                ".git/config": "junk",
            },
        )
        (examples_dir / "junk-ex" / "mod.pyc").write_text(
            "junk",
            encoding="utf-8",
        )
        target_dir = tmp_path / "out7"

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )

        _handle_example_command(
            ["download", "junk-ex", "--target", str(target_dir)],
        )

        assert (target_dir / ".agenticx" / "README.md").exists()
        assert not (target_dir / ".agenticx" / "__pycache__").exists()
        assert not (target_dir / ".agenticx" / ".git").exists()
        assert not (target_dir / ".agenticx" / "mod.pyc").exists()


class TestDownloadRepoGuidance:
    def _no_local_example(self, tmp_path, monkeypatch):
        """Patch away local examples and configured repo; return config paths."""
        empty = tmp_path / "examples"
        empty.mkdir()
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: empty,
        )

        from dashscope_agenticx.config import Config

        monkeypatch.setattr(
            Config,
            "load",
            classmethod(lambda cls, **kw: cls()),
        )
        cfg_dir = tmp_path / "home-agenticx"
        cfg_file = cfg_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_DIR", cfg_dir)
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_FILE", cfg_file)
        return cfg_dir, cfg_file

    def _fake_clone(self, monkeypatch):
        def fake(name, repo, branch, tmp):
            src = tmp / "apps" / name
            src.mkdir(parents=True)
            (src / "README.md").write_text(f"from {repo}", encoding="utf-8")
            return src

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._clone_example_to_temp",
            fake,
        )

    def test_default_public_repo_is_used_without_prompting(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        self._no_local_example(tmp_path, monkeypatch)
        self._fake_clone(monkeypatch)
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"isatty": lambda self: False})(),
        )

        _handle_example_command(
            ["download", "ghost", "--target", str(tmp_path / "out")],
        )

        assert (tmp_path / "out" / ".agenticx" / "README.md").read_text() == (
            "from " + _DEFAULT_EXAMPLES_REPO
        )
        assert _selected_expert(tmp_path / "out" / ".agenticx") == "ghost"
        assert "Enter the examples" not in capsys.readouterr().out

    def test_repo_flag_saved_and_used(self, tmp_path, monkeypatch):
        _, cfg_file = self._no_local_example(tmp_path, monkeypatch)
        self._fake_clone(monkeypatch)
        target_dir = tmp_path / "out"

        _handle_example_command(
            [
                "download",
                "remote-ex",
                "--target",
                str(target_dir),
                "--repo",
                "http://git.example.com/a/b.git",
            ],
        )

        assert (target_dir / ".agenticx" / "README.md").read_text() == (
            "from http://git.example.com/a/b.git"
        )
        content = cfg_file.read_text(encoding="utf-8")
        assert 'examples_repo = "http://git.example.com/a/b.git"' in content

    def test_repo_flag_replaces_existing_config_value(
        self,
        tmp_path,
        monkeypatch,
    ):
        cfg_dir, cfg_file = self._no_local_example(tmp_path, monkeypatch)
        cfg_dir.mkdir(parents=True)
        cfg_file.write_text(
            'examples_repo = "http://old.git"\nexamples_branch = "dev"\n',
            encoding="utf-8",
        )
        self._fake_clone(monkeypatch)

        _handle_example_command(
            [
                "download",
                "remote-ex",
                "--target",
                str(tmp_path / "out"),
                "--repo",
                "http://new.git",
            ],
        )

        content = cfg_file.read_text(encoding="utf-8")
        assert 'examples_repo = "http://new.git"' in content
        assert 'examples_branch = "dev"' in content  # other keys preserved

    def test_tty_uses_default_without_saving_an_override(
        self,
        tmp_path,
        monkeypatch,
    ):
        _, cfg_file = self._no_local_example(tmp_path, monkeypatch)
        self._fake_clone(monkeypatch)
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"isatty": lambda self: True})(),
        )
        monkeypatch.setattr(
            "builtins.input",
            lambda prompt="": pytest.fail(
                "default downloads must not prompt for a repository",
            ),
        )

        target_dir = tmp_path / "out"
        _handle_example_command(
            ["download", "remote-ex", "--target", str(target_dir)],
        )

        assert (
            target_dir / ".agenticx" / "README.md"
        ).read_text() == "from " + _DEFAULT_EXAMPLES_REPO
        assert not cfg_file.exists()

    def test_configured_repo_overrides_the_public_default(
        self,
        tmp_path,
        monkeypatch,
    ):
        self._no_local_example(tmp_path, monkeypatch)
        from dashscope_agenticx.config import Config

        monkeypatch.setattr(
            Config,
            "load",
            classmethod(
                lambda cls, **kwargs: cls(
                    examples_repo="https://git.example.com/catalog.git",
                    examples_branch="custom",
                ),
            ),
        )
        assert _get_configured_repo() == (
            "https://git.example.com/catalog.git",
            "custom",
        )


# ---------------------------------------------------------------------------
# example restore
# ---------------------------------------------------------------------------


class TestExampleRestore:
    def test_restore_roundtrip_undoes_download(self, tmp_path, monkeypatch):
        examples_dir = self._make_example(
            tmp_path,
            "ex",
            {"file.txt": "new", ".agenticx/skills/s.md": "skill"},
        )
        target_dir = tmp_path / "proj"
        (target_dir / ".agenticx").mkdir(parents=True)
        (target_dir / ".agenticx" / "file.txt").write_text(
            "old",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )
        _handle_example_command(
            ["download", "ex", "--target", str(target_dir), "--force"],
        )
        assert (target_dir / ".agenticx" / "file.txt").read_text() == "new"

        _handle_example_command(["restore", "--target", str(target_dir)])

        assert (target_dir / ".agenticx" / "file.txt").read_text() == "old"
        assert not (
            target_dir / ".agenticx" / "skills"
        ).exists()  # added file pruned
        assert not (
            target_dir / ".agenticx" / "backup"
        ).exists()  # backup consumed

    def test_restore_without_backup_is_graceful(self, tmp_path, capsys):
        _handle_example_command(["restore", "--target", str(tmp_path)])

        assert "No restorable backup found" in capsys.readouterr().out

    def test_new_backup_replaces_previous(self, tmp_path, monkeypatch):
        """Single rolling backup: only the latest download can be rolled back."""
        examples_dir = self._make_example(tmp_path, "ex", {"f.txt": "v2"})
        target_dir = tmp_path / "proj"
        (target_dir / ".agenticx").mkdir(parents=True)
        (target_dir / ".agenticx" / "f.txt").write_text(
            "v0",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: examples_dir,
        )

        _handle_example_command(
            ["download", "ex", "--target", str(target_dir), "--force"],
        )
        (target_dir / ".agenticx" / "f.txt").write_text(
            "v1",
            encoding="utf-8",
        )
        _handle_example_command(
            ["download", "ex", "--target", str(target_dir), "--force"],
        )

        assert (
            target_dir / ".agenticx" / "backup" / "f.txt"
        ).read_text() == "v1"

        _handle_example_command(["restore", "--target", str(target_dir)])

        assert (target_dir / ".agenticx" / "f.txt").read_text() == "v1"
        assert not (target_dir / ".agenticx" / "backup").exists()

    def _make_example(self, tmp_path, name, files):
        examples_dir = tmp_path / "examples"
        ex_dir = examples_dir / name
        for rel, content in files.items():
            f = ex_dir / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")
        return examples_dir


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestCatalogPaths:
    def test_remote_clone_uses_only_apps_directory(
        self,
        tmp_path,
        monkeypatch,
    ):
        calls = []
        app = tmp_path / "apps" / "basic-chat"
        app.mkdir(parents=True)

        def run(arguments, **kwargs):
            calls.append(arguments)
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout="",
                stderr="",
            )

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples.subprocess.run",
            run,
        )
        assert (
            _clone_example_to_temp(
                "basic-chat",
                _DEFAULT_EXAMPLES_REPO,
                "main",
                tmp_path,
            )
            == app
        )
        assert calls[0][-3:] == ["--", _DEFAULT_EXAMPLES_REPO, str(tmp_path)]
        assert calls[1][-2:] == ["--", "apps/basic-chat"]

    def test_remote_clone_does_not_fall_back_to_top_level_example(
        self,
        tmp_path,
        monkeypatch,
    ):
        (tmp_path / "basic-chat").mkdir()
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples.subprocess.run",
            lambda arguments, **kwargs: subprocess.CompletedProcess(
                arguments,
                0,
            ),
        )
        with pytest.raises(FileNotFoundError):
            _clone_example_to_temp(
                "basic-chat",
                _DEFAULT_EXAMPLES_REPO,
                "main",
                tmp_path,
            )

    def test_remote_listing_reads_apps_tree(self, monkeypatch):
        calls = []

        def run(arguments, **kwargs):
            calls.append(arguments)
            output = (
                "dashscope-sdk-expert\nbasic-chat\n.hidden\n"
                if "ls-tree" in arguments
                else ""
            )
            return subprocess.CompletedProcess(arguments, 0, stdout=output)

        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples.subprocess.run",
            run,
        )
        assert _list_remote_examples(_DEFAULT_EXAMPLES_REPO, "main") == [
            "basic-chat",
            "dashscope-sdk-expert",
        ]
        assert calls[1][-1] == "HEAD:apps"

    @pytest.mark.parametrize(
        "name",
        [
            "../outside",
            "foo/bar",
            "/tmp/outside",
            "--upload-pack=bad",
            ".",
            "..",
        ],
    )
    def test_download_rejects_invalid_directory_names(
        self,
        name,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._get_examples_dir",
            lambda: tmp_path / "apps",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._clone_example_to_temp",
            lambda *arguments: pytest.fail("invalid names must not clone"),
        )
        _handle_example_command(
            ["download", name, "--target", str(tmp_path / "workspace")],
        )
        assert "single directory name" in capsys.readouterr().out
        assert not (tmp_path / "workspace").exists()


class TestDownloadPathSecurity:
    @staticmethod
    def _link(target, source):
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
        except OSError:
            pytest.skip("symbolic links are unavailable")

    def test_source_symlink_is_rejected_without_copying(self, tmp_path):
        source = tmp_path / "app"
        source.mkdir()
        private = tmp_path / "private.txt"
        private.write_text("private-content", encoding="utf-8")
        self._link(source / "config.toml", private)
        workspace = tmp_path / "workspace"

        with pytest.raises(ValueError, match="symbolic links"):
            _copy_example_flat(source, workspace, force=True)

        assert not (workspace / ".agenticx").exists()
        assert private.read_text() == "private-content"

    def test_destination_symlink_is_rejected_without_writing(self, tmp_path):
        source = tmp_path / "app"
        source.mkdir()
        (source / "config.toml").write_text("new", encoding="utf-8")
        workspace = tmp_path / "workspace"
        profile = workspace / ".agenticx"
        profile.mkdir(parents=True)
        private = tmp_path / "private.txt"
        private.write_text("private-content", encoding="utf-8")
        self._link(profile / "config.toml", private)

        with pytest.raises(ValueError, match="symbolic links"):
            _copy_example_flat(source, workspace, force=True)

        assert private.read_text() == "private-content"
        assert not (profile / "backup").exists()

    def test_example_cannot_supply_managed_backup_contents(self, tmp_path):
        source = tmp_path / "app"
        backup = source / ".agenticx" / "backup"
        backup.mkdir(parents=True)
        (backup / ".manifest").write_text("bad", encoding="utf-8")

        with pytest.raises(ValueError, match="backup directory"):
            _copy_example_flat(source, tmp_path / "workspace", force=True)

        assert not (tmp_path / "workspace" / ".agenticx").exists()

    def test_restore_preflights_all_manifest_paths_before_deleting(
        self,
        tmp_path,
    ):
        workspace = tmp_path / "workspace"
        profile = workspace / ".agenticx"
        backup = profile / "backup"
        backup.mkdir(parents=True)
        valid = profile / "added.txt"
        valid.write_text("keep", encoding="utf-8")
        private = tmp_path / "private.txt"
        private.write_text("private-content", encoding="utf-8")
        (backup / ".manifest").write_text(
            ".agenticx/added.txt\n.agenticx/../../private.txt\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="escapes"):
            _restore_example_backup(workspace)

        assert valid.read_text() == "keep"
        assert private.read_text() == "private-content"
        assert backup.exists()

    def test_restore_rejects_symlinks_before_deleting_added_files(
        self,
        tmp_path,
    ):
        workspace = tmp_path / "workspace"
        profile = workspace / ".agenticx"
        backup = profile / "backup"
        backup.mkdir(parents=True)
        valid = profile / "added.txt"
        valid.write_text("keep", encoding="utf-8")
        private = tmp_path / "private.txt"
        private.write_text("private-content", encoding="utf-8")
        (backup / ".manifest").write_text(
            ".agenticx/added.txt\n",
            encoding="utf-8",
        )
        self._link(backup / "config.toml", private)

        with pytest.raises(ValueError, match="symbolic links"):
            _restore_example_backup(workspace)

        assert valid.read_text() == "keep"
        assert private.read_text() == "private-content"


class TestConstants:
    def test_default_repo_is_string(self):
        assert (
            _DEFAULT_EXAMPLES_REPO
            == "https://github.com/dashscope/dashscope-agenticx-experts.git"
        )

    def test_default_branch_is_string(self):
        assert isinstance(_DEFAULT_EXAMPLES_BRANCH, str)

    def test_default_branch_is_main(self):
        assert _DEFAULT_EXAMPLES_BRANCH == "main"
