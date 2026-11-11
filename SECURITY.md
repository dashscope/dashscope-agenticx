# Security Policy

## Supported versions

Security fixes target the active `1.0.1` release line.

## Reporting

Do not open a public issue for a suspected vulnerability. Report it privately
to the maintainers with reproduction steps, affected version, impact, and any
suggested mitigation. Avoid including real API keys, customer data, or private
App Bundles.

## Security boundaries

- Credentials are stored through the operating-system keyring under the
  `dashscope-agenticx` service. TOML files hold references only; reads fall
  back to the legacy `agenticcli` keyring service, and legacy `ENC:` values
  are read for migration. New secrets are not dual-written.
- Community Apps cannot execute `tools.py` or shell hooks without verified
  trust and explicit permissions.
- Bundled OFFICIAL trust applies only to direct shipped catalog entries, not
  traversal paths or symlink targets. Manifest paths stay inside the bundle;
  permission flags must be booleans. Installation and update preview new
  permissions before copying files. A checksum detects changed bytes; it is
  not a publisher signature or an OS/filesystem/network isolation guarantee.
- Expert templates cannot auto-install Python tools, Shell declarations, or
  executable run hooks, including when `--force` is used for overwriting.
- Write/confirm tools require approval by default (`confirm_mode = "all"`).
  Explicit policy denies take precedence over AUTO and `auto_approve`.
  Read-only parallel delegation does not inherit parent authorization grants.
- Run-hook template values are passed as process arguments, not shell syntax.
  Custom Shell tool parameters follow the same rule, including quoted
  `{{parameter}}` placeholders. Parameterized commands cannot contain shell
  pipelines or invoke shell interpreters; use a trusted executable script
  with literal arguments for those workflows.
  Templated commands reject shell operators, shell interpreters, and variable
  executable/Python code/module names;
  Windows batch-file targets are not allowed for templated hooks. Constant
  shell scripts and scripts that evaluate their arguments still require trust.
- Release audits install the built Runtime in an isolated environment and
  cover both base and optional dependencies, not just the build tooling.
- Local Pillow decoding requires Pillow 13.0.0+; unsupported versions fall
  back to whole-image provider reads rather than local crop/slice decoding.
- App archives reject traversal, links, special files, excessive members, and
  excessive expanded size.
- The local Server binds only to localhost, requires a bearer token, and never
  accepts remote permission decisions. Remote runs cannot inherit local
  automatic approval; invalid/oversized HTTP body lengths are rejected.
- Runtime contexts isolate MCP clients, sessions, extensions, and permission
  snapshots across tasks. Owning clients are closed on normal completion,
  early stream closure, and frontend exceptions. SDK consumers that stop
  iteration early should explicitly close the async iterator.
- Redaction protects recognized credential fields/patterns, not arbitrary
  confidential data. In particular, independently streamed fragments can
  split a secret across events; do not treat event redaction as a DLP boundary.
- Sandbox support is defense in depth, not a complete isolation boundary.
