# -*- coding: utf-8 -*-
import json
from pathlib import Path

import pytest

from dashscope_agenticx.skills import store


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    return tmp_path / "skills"


def _make_pkg(path: Path, name: str = "demo", version: str = "1.0.0") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "skill.toml").write_text(
        f'name = "{name}"\nversion = "{version}"\ndescription = "a demo skill"\n',
        encoding="utf-8",
    )
    (path / "prompt.md").write_text("Hello {name}", encoding="utf-8")
    return path


class TestInstall:
    def test_install_from_local_path(self, skills_dir: Path):
        src = _make_pkg(skills_dir / "src" / "demo", name="demo")
        name = store.install(str(src), skills_dir=skills_dir)
        assert name == "demo"
        assert (skills_dir / "demo" / "skill.toml").exists()
        manifest = store.load_manifest(skills_dir)
        assert manifest["demo"]["source_type"] == "local"

    def test_install_renames_to_declared_name(self, skills_dir: Path):
        src = _make_pkg(
            skills_dir / "src" / "foo",
            name="bar",
            version="0.2.0",
        )
        name = store.install(str(src), skills_dir=skills_dir)
        assert name == "bar"
        assert (skills_dir / "bar" / "skill.toml").exists()
        assert not (skills_dir / "foo").exists()

    def test_install_invalid_source(self, skills_dir: Path):
        with pytest.raises(RuntimeError):
            store.install("not-a-real-thing", skills_dir=skills_dir)


class TestLink:
    def test_link_local_directory(self, skills_dir: Path):
        src = _make_pkg(skills_dir / "src" / "linkable", name="linkable")
        name = store.link(str(src), skills_dir=skills_dir)
        assert name == "linkable"
        assert (skills_dir / "linkable").is_symlink()
        manifest = store.load_manifest(skills_dir)
        assert manifest["linkable"]["source_type"] == "link"


class TestUninstall:
    def test_uninstall_removes_package(self, skills_dir: Path):
        _make_pkg(skills_dir / "demo", name="demo")
        store.save_manifest(skills_dir, {"demo": {"source_type": "local"}})
        assert store.uninstall("demo", skills_dir=skills_dir) is True
        assert not (skills_dir / "demo").exists()
        assert "demo" not in store.load_manifest(skills_dir)

    def test_uninstall_missing_package(self, skills_dir: Path):
        assert store.uninstall("missing", skills_dir=skills_dir) is False


class TestUpdate:
    def test_update_local_copies_source(self, skills_dir: Path):
        src = skills_dir / "src" / "demo"
        _make_pkg(src, name="demo", version="1.0.0")
        store.install(str(src), skills_dir=skills_dir)

        # Change source after install.
        _make_pkg(src, name="demo", version="2.0.0")
        store.update("demo", skills_dir=skills_dir)

        installed_toml = (skills_dir / "demo" / "skill.toml").read_text(
            encoding="utf-8",
        )
        assert "2.0.0" in installed_toml
        manifest = store.load_manifest(skills_dir)
        assert manifest["demo"]["version"] == "2.0.0"

    def test_update_link_validates(self, skills_dir: Path):
        src = _make_pkg(skills_dir / "src" / "linked", name="linked")
        store.link(str(src), skills_dir=skills_dir)
        store.update("linked", skills_dir=skills_dir)
        assert (skills_dir / "linked").is_symlink()


class TestSearch:
    def test_search_installed_packages(self, skills_dir: Path):
        _make_pkg(skills_dir / "py-lint", name="py-lint", version="1.0.0")
        _make_pkg(skills_dir / "js-lint", name="js-lint", version="1.0.0")
        results = store.search("py", skills_dir=skills_dir)
        names = {r["name"] for r in results}
        assert "py-lint" in names
        assert "js-lint" not in names

    def test_search_registry(self, skills_dir: Path, tmp_path: Path):
        index = tmp_path / "index.toml"
        index.write_text(
            "[[skills]]\n"
            'name = "remote-skill"\n'
            'version = "1.0.0"\n'
            'description = "a remote skill"\n'
            'source = "https://example.com/remote-skill.git"\n'
            'tags = ["python"]\n',
            encoding="utf-8",
        )
        results = store.search(
            "remote",
            skills_dir=skills_dir,
            registry_url=str(index),
        )
        assert len(results) == 1
        assert results[0]["name"] == "remote-skill"
        assert results[0]["installed"] is False


class TestPublish:
    def test_publish_creates_tarball(self, tmp_path: Path):
        src = _make_pkg(tmp_path / "demo", name="demo", version="1.2.3")
        archive = store.publish(str(src), output_dir=tmp_path / "dist")
        assert archive.exists()
        assert archive.name == "demo-1.2.3.tar.gz"

    def test_publish_invalid_package(self, tmp_path: Path):
        bad = tmp_path / "bad"
        bad.mkdir()
        with pytest.raises(RuntimeError):
            store.publish(str(bad))


class TestManifest:
    def test_load_save_roundtrip(self, skills_dir: Path):
        data = {"foo": {"source_type": "local", "version": "1.0.0"}}
        store.save_manifest(skills_dir, data)
        assert store.load_manifest(skills_dir) == data


class TestSkillNameValidation:
    def test_validate_accepts_normal_names(self, skills_dir: Path):
        store._validate_skill_name("demo", skills_dir)
        store._validate_skill_name("my-skill.v2", skills_dir)

    @pytest.mark.parametrize(
        "bad_name",
        ["../evil", "..", "a/b", "a\\b", "/abs", ""],
    )
    def test_validate_rejects_bad_names(self, skills_dir: Path, bad_name: str):
        with pytest.raises(RuntimeError):
            store._validate_skill_name(bad_name, skills_dir)

    def test_install_rejects_traversal_name_from_registry(
        self,
        skills_dir: Path,
        tmp_path: Path,
    ):
        index = tmp_path / "index.toml"
        index.write_text(
            "[[skills]]\n"
            'name = "../evil-nonexistent-agenticx-test"\n'
            'version = "1.0.0"\n'
            'source = "/nonexistent/source"\n',
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError):
            store.install(
                "../evil-nonexistent-agenticx-test",
                skills_dir=skills_dir,
                registry_url=str(index),
            )

    def test_install_rejects_malicious_declared_name(self, skills_dir: Path):
        src = _make_pkg(skills_dir / "src" / "evildir", name="../evil")
        with pytest.raises(RuntimeError):
            store.install(str(src), skills_dir=skills_dir)
        # The copied directory must be cleaned up, and nothing escapes.
        assert not (skills_dir / "evildir").exists()
        assert not (skills_dir.parent / "evil").exists()

    def test_uninstall_rejects_traversal_name(self, skills_dir: Path):
        skills_dir.mkdir(parents=True, exist_ok=True)
        outside = skills_dir.parent / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        with pytest.raises(RuntimeError):
            store.uninstall("../outside", skills_dir=skills_dir)
        assert (outside / "keep.txt").exists()

    @pytest.mark.parametrize("bad_name", ["..", "a/b", "a\\b", "/abs", ""])
    def test_uninstall_rejects_bad_names(
        self,
        skills_dir: Path,
        bad_name: str,
    ):
        with pytest.raises(RuntimeError):
            store.uninstall(bad_name, skills_dir=skills_dir)

    def test_link_rejects_malicious_declared_name(
        self,
        skills_dir: Path,
        tmp_path: Path,
    ):
        src = _make_pkg(tmp_path / "linkable", name="../evil")
        with pytest.raises(RuntimeError):
            store.link(str(src), skills_dir=skills_dir)
        assert not (tmp_path / "evil").exists()

    def test_publish_rejects_malicious_declared_name(self, tmp_path: Path):
        src = _make_pkg(tmp_path / "demo", name="../evil")
        with pytest.raises(RuntimeError):
            store.publish(str(src), output_dir=tmp_path / "dist")
        assert not (tmp_path / "evil-1.0.0.tar.gz").exists()
