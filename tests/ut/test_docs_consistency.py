# -*- coding: utf-8 -*-
"""Doc-code consistency guards (roadmap P4).

Automated checks that READMEs and detailed user guides do not drift away
from the code.  Added after the Self-Harness feature was deleted
from the code base but remained documented in the README for
months: each check below targets that class of incident.

The extraction/validation logic lives in plain helper functions so
the guards themselves can be unit-tested; the ``test_guard_*``
methods prove each check actually fires on simulated drift.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

import pytest

from dashscope_agenticx.cli.constants import _TOP_LEVEL_COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_AGENTICX = REPO_ROOT / "src" / "dashscope_agenticx"
README_EN = REPO_ROOT / "README.md"
README_ZH = REPO_ROOT / "README_zh.md"
GUIDE_EN = REPO_ROOT / "user-guide.md"
GUIDE_ZH = REPO_ROOT / "user-guide_zh.md"
MIGRATION = REPO_ROOT / "migration-to-1.0.md"
PIPELINE_PY = SRC_AGENTICX / "prompt_pipeline.py"

# src/dashscope_agenticx subdirectories that mark a ``.py`` token as a reference
# into the agenticx package (as opposed to example or user code).
_AGENTICX_SUBDIRS = frozenset(
    (
        "agents",
        "cli",
        "memory",
        "platforms",
        "providers",
        "skills",
        "tools",
        "ui",
    ),
)

_PY_TOKEN_RE = re.compile(r"[A-Za-z0-9_/.]+\.py")
_SLASH_CELL_RE = re.compile(r"`(/[^`\n]+)`")
_SECTION_CLASS_RE = re.compile(r"^class (\w*Section)\b(.*)$")

# Top-level commands intentionally left undocumented in the README.
# Keep this set empty; only add entries (with a comment) for
# commands that are deliberately internal (e.g. /exit variants).
EXEMPT_COMMANDS: frozenset[str] = frozenset()

# Command-table entries that are documented aliases rather than
# members of _TOP_LEVEL_COMMANDS.  "/v" is the voice-input shortcut
# handled in cli/dispatch.py (_handle_slash_command).
_KNOWN_ALIASES = frozenset({"/v"})


# ---------------------------------------------------------------------------
# Check 1: referenced agenticx source files exist
# ---------------------------------------------------------------------------


def extract_py_tokens(text: str) -> list[str]:
    """Return every ``*.py``-looking token found in *text*."""
    return _PY_TOKEN_RE.findall(text)


def is_agenticx_reference(token: str) -> bool:
    """True when *token* looks like a path into the agenticx package."""
    rel = token
    if rel.startswith("src/"):
        rel = rel[len("src/") :]
    if rel.startswith("dashscope_agenticx/"):
        return True
    parts = rel.split("/")
    return len(parts) > 1 and parts[0] in _AGENTICX_SUBDIRS


def resolve_agenticx_path(token: str) -> Path | None:
    """Resolve *token* to a real file under ``src/dashscope_agenticx``.

    Tries the literal relative path first, then falls back to a
    basename search so moved/renamed modules are not false positives.
    """
    rel = token
    if rel.startswith("src/dashscope_agenticx/"):
        rel = rel[len("src/dashscope_agenticx/") :]
    elif rel.startswith("src/"):
        rel = rel[len("src/") :]
    if rel.startswith("dashscope_agenticx/"):
        rel = rel[len("dashscope_agenticx/") :]
    literal = SRC_AGENTICX / rel
    if literal.is_file():
        return literal
    basename = Path(rel).name
    for match in sorted(SRC_AGENTICX.rglob(basename)):
        if match.is_file():
            return match
    return None


def find_missing_agenticx_refs(text: str) -> list[str]:
    """Return agenticx ``.py`` references in *text* that do not exist."""
    return sorted(
        {
            token
            for token in extract_py_tokens(text)
            if is_agenticx_reference(token)
            and resolve_agenticx_path(token) is None
        },
    )


class TestOnePointZeroReadmes:
    @pytest.mark.parametrize("readme", [README_EN, README_ZH])
    def test_readme_is_a_current_release_entrypoint(self, readme: Path):
        text = readme.read_text(encoding="utf-8")
        assert text.startswith("# DashScope AgenticX\n")
        assert "![DashScope AgenticX](logo.svg)" in text
        assert "AgenticX 1.0" in text
        assert 'python -m pip install "dashscope-agenticx[tui]"' in text
        assert "1.0.0rc3" not in text
        assert "release has not been published" not in text
        assert "尚未发布最终正式版" not in text
        assert len(text.splitlines()) < 350
        assert "CHANGELOG.md" in text
        assert "RuntimeBuilder" in text
        assert "dashscope-agenticx" in text
        assert "### v0." not in text
        assert "## Changelog" not in text
        assert "## 变更" not in text

    @pytest.mark.parametrize(
        "document",
        [README_EN, README_ZH, GUIDE_EN, GUIDE_ZH, MIGRATION],
    )
    def test_local_document_links_exist(self, document: Path):
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^\]\n]*\]\(([^\s)]+)", text):
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            relative = target.split("#", 1)[0]
            assert (
                document.parent / relative
            ).exists(), f"{document.name} contains a broken link: {target}"
            resolved = (document.parent / relative).resolve()
            assert not resolved.is_relative_to(
                REPO_ROOT / "md",
            ), f"{document.name} links to internal documentation: {target}"
            assert resolved.is_relative_to(
                REPO_ROOT,
            ), f"{document.name} links outside the public repository: {target}"

    def test_public_changelog_excludes_pre_one_point_zero_history(self):
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "## [Released]" in text
        assert "## [1.0.0] - 2026-11-09" in text
        assert "## [1.0.0rc3]" in text
        assert "## [0.7.2]" not in text
        assert "Chinese archive" not in text

    @pytest.mark.skipif(
        not (REPO_ROOT / "md").is_dir(),
        reason="The internal changelog archive is not shipped publicly",
    )
    def test_pre_one_point_zero_internal_archive_is_preserved(self):
        archive = REPO_ROOT / "md" / "CHANGELOG-pre-1.0-archive.md"
        archived_text = archive.read_text(encoding="utf-8")
        assert "## [0.7.2]" in archived_text
        assert "#### v0.7.1:" in archived_text
        assert "#### v0.1.0:" in archived_text
        assert "Chinese archive" in archived_text

    @pytest.mark.parametrize(
        "document",
        [
            GUIDE_EN,
            GUIDE_ZH,
            MIGRATION,
            REPO_ROOT / "RELEASE.md",
            REPO_ROOT / "SECURITY.md",
        ],
    )
    def test_public_release_documents_match_package_version(
        self,
        document: Path,
    ):
        from dashscope_agenticx import __version__

        text = document.read_text(encoding="utf-8")
        assert __version__ in text
        assert "1.0.0rc3" not in text

    def test_logo_is_a_local_accessible_svg(self):
        import xml.etree.ElementTree as element_tree

        logo = element_tree.parse(REPO_ROOT / "logo.svg").getroot()
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        assert logo.tag == "{http://www.w3.org/2000/svg}svg"
        assert logo.findtext("svg:title", namespaces=namespace) == (
            "DashScope AgenticX"
        )
        assert logo.get("aria-labelledby")


class TestReferencedSourceFiles:
    @pytest.mark.parametrize(
        "readme",
        [README_ZH, README_EN, GUIDE_ZH, GUIDE_EN],
        ids=["README_zh.md", "README.md", "user-guide_zh.md", "user-guide.md"],
    )
    def test_referenced_agenticx_modules_exist(self, readme: Path) -> None:
        missing = find_missing_agenticx_refs(
            readme.read_text(encoding="utf-8"),
        )
        assert not missing, (
            f"{readme.name} references agenticx modules that no longer "
            f"exist under src/dashscope_agenticx: {', '.join(missing)}"
        )

    def test_guard_flags_deleted_module(self, tmp_path: Path) -> None:
        """Self-test: the Self-Harness incident must fail check 1."""
        fake = tmp_path / "README.md"
        fake.write_text(
            "See memory/self_harness.py and tools/harness.py, "
            "but cli/constants.py is real.\n",
            encoding="utf-8",
        )
        missing = find_missing_agenticx_refs(
            fake.read_text(encoding="utf-8"),
        )
        assert "memory/self_harness.py" in missing
        assert "tools/harness.py" in missing
        assert "cli/constants.py" not in missing


# ---------------------------------------------------------------------------
# Check 2: slash commands in sync with cli/constants.py
# ---------------------------------------------------------------------------


def find_undocumented_commands(
    text: str,
    commands: Sequence[str],
    exempt: frozenset[str] = EXEMPT_COMMANDS,
) -> list[str]:
    """Commands from *commands* never referenced in README *text*."""
    missing = []
    for cmd in commands:
        if cmd in exempt:
            continue
        if not re.search(re.escape(cmd) + r"(?![a-z0-9-])", text):
            missing.append(cmd)
    return missing


def extract_commands_chapter(text: str) -> str:
    """Return the ``## 命令`` chapter of a README."""
    lines = text.splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        if start is None:
            if line.strip() == "## 命令":
                start = idx + 1
        elif line.startswith("## "):
            return "\n".join(lines[start:idx])
    if start is None:
        return ""
    return "\n".join(lines[start:])


def extract_table_commands(chapter: str) -> list[str]:
    """First backticked ``/...`` entry of every markdown table row."""
    entries = []
    for line in chapter.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        match = _SLASH_CELL_RE.search(line)
        if match:
            entries.append(match.group(1).strip())
    return entries


def find_unknown_table_commands(
    chapter: str,
    commands: Sequence[str],
    aliases: frozenset[str] = _KNOWN_ALIASES,
) -> list[str]:
    """Table entries that are neither real commands, known aliases,
    nor documented subcommand forms (kept lenient on purpose)."""
    known = set(commands) | set(aliases)
    unknown = []
    for entry in extract_table_commands(chapter):
        base = entry.split()[0]
        if base in known:
            continue
        if " " in entry or "|" in entry:  # documented subcommand form
            continue
        unknown.append(entry)
    return unknown


class TestSlashCommandsInSync:
    def test_every_top_level_command_is_documented(self) -> None:
        text = GUIDE_ZH.read_text(encoding="utf-8")
        missing = find_undocumented_commands(text, _TOP_LEVEL_COMMANDS)
        assert not missing, (
            "top-level commands present in "
            "dashscope_agenticx.cli.constants._TOP_LEVEL_COMMANDS but missing "
            f"from user-guide_zh.md: {', '.join(missing)}"
        )

    def test_command_table_only_lists_real_commands(self) -> None:
        chapter = extract_commands_chapter(
            GUIDE_ZH.read_text(encoding="utf-8"),
        )
        assert chapter, "user-guide_zh.md lost its '## 命令' chapter"
        unknown = find_unknown_table_commands(
            chapter,
            _TOP_LEVEL_COMMANDS,
        )
        assert not unknown, (
            "user-guide_zh.md command table lists commands unknown to "
            f"dashscope_agenticx.cli.constants._TOP_LEVEL_COMMANDS: {unknown}"
        )

    def test_guard_flags_undocumented_command(self) -> None:
        missing = find_undocumented_commands(
            "Only `/help` is documented here.",
            ["/help", "/undo"],
        )
        assert missing == ["/undo"]

    def test_guard_flags_unknown_table_entry(self) -> None:
        chapter = (
            "| 命令 | 说明 |\n"
            "|------|------|\n"
            "| `/fake` | not a real command |\n"
            "| `/help` | real command |\n"
            "| `/fake sub` | lenient subcommand form |\n"
        )
        unknown = find_unknown_table_commands(
            chapter,
            _TOP_LEVEL_COMMANDS,
        )
        assert unknown == ["/fake"]


# ---------------------------------------------------------------------------
# Check 3: PromptPipeline section count matches the READMEs
# ---------------------------------------------------------------------------


def count_pipeline_sections(source: str) -> int:
    """Count concrete ``*Section`` classes in prompt_pipeline.py.

    ``PromptSection`` is the shared Protocol (the interface every
    section implements), not one of the built-in sections, so it is
    excluded from the count.
    """
    count = 0
    for line in source.splitlines():
        match = _SECTION_CLASS_RE.match(line)
        if match and "Protocol" not in match.group(2):
            count += 1
    return count


def _paragraph_with(text: str, anchor: str) -> str:
    """Return the paragraph (up to the next blank line) containing
    *anchor*."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if anchor in line:
            chunk = [line]
            for following in lines[idx + 1 :]:
                if not following.strip():
                    break
                chunk.append(following)
            return "\n".join(chunk)
    return ""


class TestPromptPipelineSections:
    def test_section_counts_match_user_guides(self) -> None:
        count = count_pipeline_sections(
            PIPELINE_PY.read_text(encoding="utf-8"),
        )
        assert count > 0, "no *Section classes found in prompt_pipeline"

        zh_para = _paragraph_with(
            GUIDE_ZH.read_text(encoding="utf-8"),
            "Prompt 装配管线",
        )
        assert zh_para, "user-guide_zh.md lost the 'Prompt 装配管线' paragraph"
        zh_match = re.search(r"(\d+) 个内置 section", zh_para)
        assert zh_match, (
            "user-guide_zh.md 'Prompt 装配管线' paragraph no longer states "
            "the section count ('N 个内置 section')"
        )
        assert int(zh_match.group(1)) == count, (
            f"user-guide_zh.md claims {zh_match.group(1)} built-in sections "
            f"but prompt_pipeline.py defines {count}"
        )

        en_para = _paragraph_with(
            GUIDE_EN.read_text(encoding="utf-8"),
            "Prompt assembly pipeline",
        )
        assert (
            en_para
        ), "user-guide.md lost the 'Prompt assembly pipeline' paragraph"
        en_match = re.search(r"(\d+) built-in sections", en_para)
        assert en_match, (
            "user-guide.md 'Prompt assembly pipeline' paragraph no "
            "longer states the section count ('N built-in sections')"
        )
        assert int(en_match.group(1)) == count, (
            f"user-guide.md claims {en_match.group(1)} built-in "
            f"sections but prompt_pipeline.py defines {count}"
        )

    def test_guard_counts_only_concrete_sections(self) -> None:
        source = (
            "class PromptSection(Protocol):\n"
            "    ...\n"
            "class FooSection:\n"
            "    ...\n"
            "class BarSection:\n"
            "    ...\n"
        )
        assert count_pipeline_sections(source) == 2


# ---------------------------------------------------------------------------
# Check 4: base prompts never name a tool the model cannot see
# ---------------------------------------------------------------------------

_WORKSPACE_PROMPT = REPO_ROOT / ".agenticx" / "system-prompt.md"

_SNAKE_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# Snake-case tokens in the prompts that are prose, not tool names. Keep
# this set empty: any new match is a dead instruction until justified here.
_PROSE_TOKENS: frozenset[str] = frozenset()


def find_invisible_tool_mentions(
    text: str,
    visible: frozenset[str],
    prose: frozenset[str] = _PROSE_TOKENS,
) -> list[str]:
    """Snake-case tokens in *text* that no to_schema_list call can offer.

    A base prompt that names an unoffered tool is a dead instruction: the
    model is told to call something absent from every schema it can receive
    (the create_plan/complete_step rule this guard was added for).
    """
    return sorted(
        {
            token
            for token in _SNAKE_TOKEN_RE.findall(text)
            if token not in visible and token not in prose
        },
    )


class TestBasePromptToolMentions:
    def test_system_prompt_constant_names_only_visible_tools(self) -> None:
        from dashscope_agenticx.agent import SYSTEM_PROMPT
        from dashscope_agenticx.tools.registry import ever_visible_tool_names

        invisible = find_invisible_tool_mentions(
            SYSTEM_PROMPT,
            ever_visible_tool_names(),
        )
        assert not invisible, (
            "SYSTEM_PROMPT instructs the model to call tools that "
            f"to_schema_list never offers: {', '.join(invisible)}"
        )

    @pytest.mark.skipif(
        not _WORKSPACE_PROMPT.is_file(),
        reason="Local workspace profiles are not part of the public source",
    )
    def test_workspace_prompt_file_names_only_visible_tools(self) -> None:
        from dashscope_agenticx.tools.registry import ever_visible_tool_names

        assert (
            _WORKSPACE_PROMPT.is_file()
        ), f"tracked workspace prompt missing: {_WORKSPACE_PROMPT}"
        invisible = find_invisible_tool_mentions(
            _WORKSPACE_PROMPT.read_text(encoding="utf-8"),
            ever_visible_tool_names(),
        )
        assert not invisible, (
            ".agenticx/system-prompt.md instructs the model to call tools that "
            f"to_schema_list never offers: {', '.join(invisible)}"
        )

    def test_guard_flags_dead_instruction(self) -> None:
        """Self-test: the create_plan incident must fail check 4."""
        text = "call create_plan with the goal, then read_file the target"
        invisible = find_invisible_tool_mentions(
            text,
            frozenset({"read_file"}),
        )
        assert invisible == ["create_plan"]

    def test_guard_respects_prose_allowlist(self) -> None:
        invisible = find_invisible_tool_mentions(
            "set json_object format and call read_file",
            frozenset({"read_file"}),
            prose=frozenset({"json_object"}),
        )
        assert invisible == []
