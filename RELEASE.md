# Release Process

The release version is `1.0.0`. Updating the version and pushing or merging
Git branches does not publish packages or create release tags. External
publication requires the checks and explicit approval below.

1. Update `src/dashscope_agenticx/_version.py`; all metadata consumes this
   value.
2. Run the full Python/OS CI matrix and App Catalog validation.
3. Run `scripts/release_check.py` for version, wheel, `dashscope_agenticx`
   imports, the `agenticx` CLI, bundled Apps, and the `dashscope-agenticx`
   protocol/package identity.
4. Generate the SBOM and run dependency/security scans.
5. Confirm the source commit, vendored commit metadata, proposed tag, wheel
   metadata, and `agenticx --version` agree.
6. Only when external publication is explicitly approved, tag `v<version>` and
   publish the `dashscope-agenticx` wheel/sdist plus checksums and SBOM.
7. Archive the migration guide and final benchmark/eval reports for `1.0.0`.

Main-branch pushes and pull requests run unit tests and pre-commit checks.
Before pushing a release tag, confirm the tagged source passed those checks.
The tag workflow builds Python distributions and platform binaries, smoke
tests the binaries, and uploads assets plus SHA-256 checksums to a draft
GitHub Release. Use the version's CHANGELOG entry as its release notes and
publish the draft only after reviewing the build results and assets.
Publishing a GitHub Release does not upload the Python package to PyPI;
PyPI publication is a separate, explicitly authorized step.
