# -*- coding: utf-8 -*-
"""Expert management functions."""
# pylint: disable=too-many-branches,too-many-return-statements
# pylint: disable=too-many-statements

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console

console = Console()

_DEFAULT_EXPERTS_REPO = (
    "https://github.com/dashscope/dashscope-agenticx-experts.git"
)
_DEFAULT_EXPERT_BRANCH = "main"

_KNOWN_EXPERTS = [
    "basic-chat",
    "dashscope-sdk-expert",
    "bailian-expert",
]


def _selected_expert(workspace: Path | None = None) -> str:
    from dashscope_agenticx.config import WORKSPACE_DIR
    from dashscope_agenticx.utils.toml import load_toml

    workspace = Path(workspace) if workspace is not None else WORKSPACE_DIR
    try:
        marker = _safe_expert_path(
            workspace.parent,
            Path(workspace.name) / "expert.toml",
        )
        data = load_toml(marker) or {}
        name = data.get("name", "")
        if not isinstance(name, str) or not name:
            return ""
        _validate_expert_name(name)
        return name
    except (OSError, ValueError):
        return ""


def _get_experts_dir() -> Path:
    """Return the canonical local App Catalog directory."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return project_root / "apps"


def _get_configured_repo() -> tuple[str, str]:
    try:
        from dashscope_agenticx.config import Config

        cfg = Config.load()
        return (
            cfg.expert_repo or _DEFAULT_EXPERTS_REPO,
            cfg.expert_branch or _DEFAULT_EXPERT_BRANCH,
        )
    except Exception:
        return _DEFAULT_EXPERTS_REPO, _DEFAULT_EXPERT_BRANCH


def _list_experts() -> list[str]:
    experts_dir = _get_experts_dir()
    if not experts_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in experts_dir.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
    )


def _clone_expert_to_temp(
    name: str,
    repo: str,
    branch: str,
    tmp: Path,
) -> Path:
    """Sparse-clone apps/<name> from the App Catalog into tmp."""
    _validate_expert_name(name)
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                "--no-checkout",
                "-b",
                branch,
                "--",
                repo,
                str(tmp),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp),
                "sparse-checkout",
                "set",
                "--",
                f"apps/{name}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp), "checkout"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()
        raise RuntimeError(f"git operation failed: {err or e}") from e
    except FileNotFoundError:
        raise RuntimeError("git not found; please install git") from None

    src = tmp / "apps" / name
    if not src.is_dir():
        raise FileNotFoundError(f"expert {name} not found in the repo")
    return src


def _list_remote_experts(repo: str, branch: str) -> list[str]:
    """List apps/<name> directories via a blob-less clone."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--no-checkout",
                "-b",
                branch,
                "--",
                repo,
                tmp,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        out = subprocess.run(
            ["git", "-C", tmp, "ls-tree", "-d", "--name-only", "HEAD:apps"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return sorted(d for d in out.splitlines() if not d.startswith((".", "_")))


def _save_expert_repo(repo: str) -> None:
    """Write expert_repo into the global AgenticX config (create or
    replace)."""
    from dashscope_agenticx.config import CONFIG_DIR, CONFIG_FILE
    from dashscope_agenticx.utils.paths import atomic_write_text
    from dashscope_agenticx.utils.toml import toml_str

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    text = (
        CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else ""
    )
    line = f"expert_repo = {toml_str(repo)}"
    if re.search(r"(?m)^expert_repo\s*=", text):
        text = re.sub(r"(?m)^expert_repo\s*=.*$", line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    atomic_write_text(CONFIG_FILE, text)


def _validate_expert_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("expert name must be a single directory name")


def _safe_expert_path(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("expert path escapes the workspace")
    target = root
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("expert paths must not contain symbolic links")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("expert path escapes the workspace")
    return target


def _expert_files(src: Path) -> list[Path]:
    """All copyable files under an expert dir (relative-safe, no junk)."""
    entries = sorted(src.rglob("*"))
    if src.is_symlink() or any(path.is_symlink() for path in entries):
        raise ValueError("experts must not contain symbolic links")
    return [
        p
        for p in entries
        if p.is_file()
        and "__pycache__" not in p.parts
        and ".git" not in p.parts
        and p.suffix != ".pyc"
    ]


def _validate_expert_profile(source: Path, files: list[Path]) -> None:
    from dashscope_agenticx.utils.toml import load_toml

    for path in files:
        relative = path.relative_to(source)
        if relative.parts[0] == ".agenticx":
            relative = relative.relative_to(".agenticx")
        if not relative.parts:
            raise ValueError("the App profile must be a directory")
        if relative.parts[0] == "skills" and relative.name == "tools.py":
            raise ValueError(
                "Expert templates cannot auto-load Python tools; "
                "install a trusted App Bundle instead",
            )
        if relative not in {
            Path("hooks.toml"),
            Path("custom-extensions.toml"),
        }:
            continue
        data = load_toml(path)
        if data is None:
            raise ValueError(f"Invalid Expert configuration: {relative}")
        if relative.name == "custom-extensions.toml" and data.get(
            "shell_tools",
        ):
            raise ValueError(
                "Expert templates cannot install Shell tools; "
                "configure trusted tools explicitly",
            )
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("Expert hooks must be a table")
        for entries in hooks.values():
            if isinstance(entries, list) and any(
                isinstance(spec, dict) and spec.get("action") == "run"
                for spec in entries
            ):
                raise ValueError(
                    "Expert templates cannot install run hooks; "
                    "install a trusted App Bundle instead",
                )


def _merge_extension_profile(source: Path, target: Path) -> str:
    """Append template declarations without replacing existing identities."""
    from dashscope_agenticx.extensions import current
    from dashscope_agenticx.utils.toml import loads_toml

    source_text = source.read_text(encoding="utf-8")
    target_text = target.read_text(encoding="utf-8") if target.exists() else ""
    source_data = loads_toml(source_text)
    target_data = loads_toml(target_text)
    if source_data is None or target_data is None:
        raise ValueError(
            "Invalid custom-extensions.toml; template was not applied",
        )
    identities = {
        "providers": "name",
        "capabilities": "key",
        "skills": "name",
        "shell_tools": "name",
    }
    for data in (source_data, target_data):
        for group in identities:
            entries = data.get(group, [])
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) for entry in entries
            ):
                raise ValueError(
                    "Invalid extension declarations; template was not applied",
                )
    existing = {
        group: {entry.get(identity) for entry in target_data.get(group, [])}
        for group, identity in identities.items()
    }
    loaded = current()
    for group, identity in identities.items():
        existing[group].update(
            getattr(entry, identity) for entry in getattr(loaded, group)
        )
    headers = list(
        re.finditer(
            r"(?m)^[ \t]*\[\[[ \t]*"
            r"(providers|capabilities|skills|shell_tools)"
            r"[ \t]*\]\][ \t]*(?:#[^\n]*)?$",
            source_text,
        ),
    )
    if len(headers) != sum(
        len(source_data.get(group, [])) for group in identities
    ):
        raise ValueError(
            "Unsupported template extension layout; template was not applied",
        )
    additions = []
    positions = dict.fromkeys(identities, 0)
    for index, header in enumerate(headers):
        end = (
            headers[index + 1].start()
            if index + 1 < len(headers)
            else len(source_text)
        )
        block = source_text[header.start() : end].strip()
        parsed = loads_toml(block)
        group = header.group(1)
        if parsed is None or not parsed.get(group):
            raise ValueError("Invalid template extension declaration")
        if parsed[group][0] != source_data[group][positions[group]]:
            raise ValueError("Invalid template extension layout")
        positions[group] += 1
        name = parsed[group][0].get(identities[group])
        if not name:
            raise ValueError("Template extension is missing its identity")
        if name not in existing[group]:
            additions.append(block)
            existing[group].add(name)
    merged = "\n\n".join([target_text.rstrip(), *additions]).strip() + "\n"
    if loads_toml(merged) is None:
        raise ValueError("Invalid merged extensions; template was not applied")
    return merged


def _copy_expert_flat(
    src: Path,
    dst: Path,
    *,
    force: bool,
    show_usage: bool = True,
    preserve_configuration: bool = False,
    expert_name: str = "",
) -> bool:
    """Merge the expert into dst/.agenticx/, backing up conflicting files.

    The expert's own .agenticx/ tree merges as-is; root-level files (README,
    build scripts) land in .agenticx/ too; the standalone-repo root
    .gitignore is
    skipped. Without --force: TTY lists conflicts and asks (default Yes);
    non-TTY aborts. Conflicting files are copied to .agenticx/backup/ first
    (single rolling backup, replaced on each download) so the last merge
    can be rolled back.
    """
    agenticx_dir = _safe_expert_path(dst, Path(".agenticx"))
    source_files = _expert_files(src)
    _validate_expert_profile(src, source_files)
    _safe_expert_path(dst, Path(".agenticx/backup"))
    copies: list[
        tuple[Path, Path]
    ] = []  # (source file, path relative to dst/.agenticx)
    merged_extensions: dict[Path, str] = {}
    if expert_name:
        _validate_expert_name(expert_name)
        _safe_expert_path(dst, Path(".agenticx/expert.toml"))
        _safe_expert_path(dst, Path(".agenticx/backup/expert.toml"))
    for p in source_files:
        rel = p.relative_to(src)
        if rel.parts[0] == ".agenticx":
            rel = rel.relative_to(".agenticx")
        elif rel.name == ".gitignore" and len(rel.parts) == 1:
            continue
        if not rel.parts:
            raise ValueError("the App profile must be a directory")
        if rel.parts[0] == "backup":
            raise ValueError(
                "experts must not overwrite the backup directory",
            )
        _safe_expert_path(dst, Path(".agenticx") / rel)
        _safe_expert_path(dst, Path(".agenticx/backup") / rel)
        if preserve_configuration and rel == Path("config.toml"):
            continue
        if expert_name and rel == Path("expert.toml"):
            continue
        if preserve_configuration and rel == Path("custom-extensions.toml"):
            merged_extensions[rel] = _merge_extension_profile(
                p,
                agenticx_dir / rel,
            )
        copies.append((p, rel))

    destinations = [rel for _, rel in copies]
    if expert_name:
        destinations.append(Path("expert.toml"))
    conflicts = [rel for rel in destinations if (agenticx_dir / rel).exists()]

    if conflicts and not force:
        listing = "\n".join(f"  .agenticx/{rel}" for rel in conflicts)
        action = "merge/update" if preserve_configuration else "overwrite"
        console.print(
            f"[yellow]These files already exist; continuing will "
            f"{action} them (originals are auto-backed up):[/yellow]\n"
            f"{listing}",
        )
        if not sys.stdin.isatty():
            console.print(
                "[yellow]Conflicts found in non-interactive mode; "
                "cancelled.[/yellow]",
            )
            if show_usage:
                console.print(
                    "[dim]Pass --force to overwrite (conflict files are "
                    "auto-backed up).[/dim]",
                )
            return False
        try:
            answer = (
                input(
                    "Continue? [y/N] "
                    if preserve_configuration
                    else "Continue? [Y/n] ",
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            console.print()
            return False
        if (
            preserve_configuration and answer not in ("y", "yes")
        ) or answer in ("n", "no"):
            console.print("[dim]Cancelled.[/dim]")
            return False

    backup_dir: Path | None = None
    if conflicts:
        backup_dir = agenticx_dir / "backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)  # single rolling backup: latest only
        for rel in conflicts:
            old = agenticx_dir / rel
            bdst = backup_dir / rel
            bdst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, bdst)

    agenticx_dir.mkdir(parents=True, exist_ok=True)
    for p, rel in copies:
        target = agenticx_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel in merged_extensions:
            from dashscope_agenticx.utils.paths import atomic_write_text

            atomic_write_text(target, merged_extensions[rel])
        else:
            shutil.copy2(p, target)

    if expert_name:
        from dashscope_agenticx.utils.paths import atomic_write_text
        from dashscope_agenticx.utils.toml import toml_str

        atomic_write_text(
            agenticx_dir / "expert.toml",
            f"name = {toml_str(expert_name)}\n",
        )

    if backup_dir is not None:
        manifest = "\n".join(
            str(Path(".agenticx") / rel) for rel in destinations
        )
        (backup_dir / ".manifest").write_text(
            manifest + "\n",
            encoding="utf-8",
        )

    if not show_usage:
        console.print("[green]✓ Expert downloaded.[/green]")
        return True
    console.print(f"[green]✓ Expert copied to: {agenticx_dir}[/green]")
    if (expert_name or src.name) == "dashscope-sdk-expert":
        from dashscope_agenticx.cli.handlers_key import _GUIDE_DOC, _doc_locale

        console.print(
            f"[dim]Guide: {_GUIDE_DOC.format(_doc_locale())}[/dim]",
        )
    if backup_dir is not None:
        restore_hint = " (undo with /expert restore)" if show_usage else ""
        console.print(
            f"[dim]Overwritten files backed up to: {backup_dir}"
            f"{restore_hint}[/dim]",
        )
    readmes = sorted(
        (
            agenticx_dir / rel
            for _, rel in copies
            if rel.name.lower().startswith("readme")
        ),
        key=lambda p: (len(p.parts), str(p)),
    )
    if readmes:
        console.print(f"[dim]See README for usage: {readmes[0]}[/dim]")
    console.print(
        "[dim]Edit config and skills under .agenticx/ "
        "to customize your Agent[/dim]",
    )
    if show_usage:
        console.print(
            "[dim]Run /setup to personalize, /help for all commands[/dim]",
        )
    else:
        console.print(
            "[dim]Restart AgenticX to apply the downloaded profile.[/dim]",
        )
    return True


def _prune_empty_dirs(path: Path, stop: Path) -> None:
    while path != stop and path.is_dir():
        try:
            path.rmdir()
        except OSError:
            break
        path = path.parent


def _restore_expert_backup(dst: Path) -> bool:
    """Restore the single .agenticx/backup: originals copied back,
    added files
    removed."""
    backup = _safe_expert_path(dst, Path(".agenticx/backup"))
    if not backup.is_dir():
        console.print(
            "[yellow]No restorable backup found "
            "(.agenticx/backup/).[/yellow]",
        )
        return False

    removals = []
    manifest = _safe_expert_path(dst, Path(".agenticx/backup/.manifest"))
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            rel = line.strip()
            if not rel:
                continue
            backup_rel = Path(rel).relative_to(".agenticx")
            if not backup_rel.parts or backup_rel.parts[0] == "backup":
                raise ValueError("invalid expert backup manifest")
            target = _safe_expert_path(dst, Path(rel))
            original = _safe_expert_path(
                dst,
                Path(".agenticx/backup") / backup_rel,
            )
            if original.exists():
                continue  # overwritten files are restored below
            removals.append(target)

    restores = []
    for source in _expert_files(backup):
        if source.name == ".manifest":
            continue
        relative = source.relative_to(backup)
        target = _safe_expert_path(dst, Path(".agenticx") / relative)
        if relative.parts[0] == "backup":
            raise ValueError("invalid expert backup contents")
        restores.append((source, target))

    removed = 0
    for target in removals:
        if target.is_file():
            target.unlink()
            removed += 1
            _prune_empty_dirs(target.parent, dst)

    restored = 0
    for source, target in restores:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        restored += 1

    shutil.rmtree(backup)
    _prune_empty_dirs(backup.parent, dst)
    console.print(
        f"[green]✓ Restored ({restored} original files restored, "
        f"{removed} expert-added files removed)[/green]",
    )
    return True


def _print_expert_list():
    experts_dir = _get_experts_dir()
    available = _list_experts()
    if not available:
        repo, branch = _get_configured_repo()
        if repo:
            try:
                available = _list_remote_experts(repo, branch)
            except Exception:
                available = []
    if not available:
        available = _KNOWN_EXPERTS
    console.print(
        "[dim]Experts are role templates. Use `agenticx app` for "
        "versioned installable applications.[/dim]",
    )
    console.print("[bold]Available experts:[/bold]")
    for name in available:
        readme = experts_dir / name / "README.md"
        desc = ""
        if readme.exists():
            first_line = readme.read_text(encoding="utf-8").split("\n", 1)[0]
            desc = first_line.lstrip("# ").strip()
        console.print(f"  [cyan]{name}[/cyan]  — {desc}")
    console.print()
    console.print("[bold]Usage:[/bold]")
    console.print(
        "  agenticx expert download <name>                 "
        "[dim]# merge into ./.agenticx/ (conflicts backed up)[/dim]",
    )
    console.print(
        "  agenticx expert download <name> --target <dir>  "
        "[dim]# merge into the given directory[/dim]",
    )
    console.print(
        "  agenticx expert download <name> --force         "
        "[dim]# overwrite without asking (still backed up)[/dim]",
    )
    console.print(
        "  agenticx expert download <name> --repo <url>    "
        "[dim]# use this repo and save it to config[/dim]",
    )
    console.print(
        "  agenticx expert restore                         "
        "[dim]# restore the latest backup (undo merge)[/dim]",
    )


def _handle_expert_command(
    args,
    *,
    show_usage: bool = True,
    preserve_configuration: bool = False,
):
    """Handle `agenticx expert [list | download <name>]`."""
    experts_dir = _get_experts_dir()

    if not args:
        _print_expert_list()
        return None

    sub = args[0]

    if sub in ("list", "--list", "-l"):
        _print_expert_list()
        return None

    # restore [--target DIR]
    if sub == "restore":
        target_dir = Path.cwd()
        if "--target" in args or "-t" in args:
            idx = (
                args.index("--target")
                if "--target" in args
                else args.index("-t")
            )
            if idx + 1 < len(args):
                target_dir = Path(args[idx + 1])
        try:
            _restore_expert_backup(target_dir)
        except (ValueError, OSError) as error:
            console.print(f"[yellow]Error: {error}[/yellow]")
        return None

    # download <name> [--target DIR] [--force]
    if sub == "download":
        if len(args) < 2:
            console.print(
                "[yellow]Error: download requires an expert name[/yellow]",
            )
            available = _list_experts() or _KNOWN_EXPERTS
            console.print(f"[dim]Available: {', '.join(available)}[/dim]")
            return None
        name = args[1]
        try:
            _validate_expert_name(name)
        except ValueError as error:
            console.print(f"[yellow]Error: {error}[/yellow]")
            return None
        target_dir = Path.cwd()
        if "--target" in args or "-t" in args:
            idx = (
                args.index("--target")
                if "--target" in args
                else args.index("-t")
            )
            if idx + 1 < len(args):
                target_dir = Path(args[idx + 1])
        force = "--force" in args or "-f" in args
        provided = None
        if "--repo" in args:
            idx = args.index("--repo")
            if idx + 1 < len(args):
                provided = args[idx + 1]

        src = experts_dir / name
        if src.is_dir():
            try:
                return _copy_expert_flat(
                    src,
                    target_dir,
                    force=force,
                    show_usage=show_usage,
                    preserve_configuration=preserve_configuration,
                    expert_name=name,
                )
            except (ValueError, OSError) as error:
                console.print(f"[yellow]Error: {error}[/yellow]")
                return False

        repo, branch = _get_configured_repo()
        repo = provided or repo
        if provided:
            _save_expert_repo(provided)
            console.print(
                "[dim]Saved expert_repo to AgenticX global config[/dim]",
            )
        console.print(
            f"[dim]Downloading expert '{name}' from {repo}...[/dim]",
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fetched = _clone_expert_to_temp(name, repo, branch, Path(tmp))
                return _copy_expert_flat(
                    fetched,
                    target_dir,
                    force=force,
                    show_usage=show_usage,
                    preserve_configuration=preserve_configuration,
                    expert_name=name,
                )
        except (RuntimeError, ValueError, OSError) as e:
            console.print(f"[yellow]Error: {e}[/yellow]")
            console.print(f"[dim]Repo: {repo} (branch: {branch})[/dim]")
            console.print(
                "[dim]You can set expert_repo in "
                "~/.agenticx/config.toml[/dim]",
            )
        return False

    # Unknown subcommand — show hint
    console.print(f"[yellow]Unknown subcommand: '{sub}'[/yellow]")
    console.print("[dim]Usage:[/dim]")
    console.print(
        "  agenticx expert               [dim]# list experts[/dim]",
    )
    console.print(
        "  agenticx expert download <name>         "
        "[dim]# download an expert[/dim]",
    )
    console.print(
        "  agenticx expert restore                 "
        "[dim]# restore the latest backup[/dim]",
    )
    return None
