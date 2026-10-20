# Repository Guidelines

## Project Structure

The Python desktop application lives in `e_pod_line_simulator/`:

```text
e_pod_line_simulator/
├── src/             # Application, simulation, analysis, and GUI modules
├── tests/           # pytest test suite
├── configs/         # Default configuration and Excel import template
├── assets/          # Runtime images and other bundled assets
├── packaging/       # PyInstaller spec
├── requirements.txt
├── requirements-dev.txt
└── run.py           # Source entry point
```

Repository-level user documentation is in `README.md`, `docs/user-guide.md`,
`CHANGELOG.md`, and `CONTRIBUTING.md`. GitHub Actions are defined under
`.github/workflows/`.

## Setup and Commands

Run development commands from `e_pod_line_simulator/` unless noted otherwise:

```bash
python -m pip install -r requirements-dev.txt
python run.py
python -m pytest tests -q
```

CI uses Python 3.12 on macOS and runs the complete pytest suite. The declared
runtime minimum is Python 3.8+.

For a local PyInstaller build:

```bash
pyinstaller --noconfirm packaging/e_pod_line_simulator.spec
```

Windows and macOS release packages are also built by
`.github/workflows/packaging.yml` on version tags or manual dispatch.

## Coding Conventions

- Follow PEP 8 with 4-space indentation and a maximum line length of 99.
- Use `PascalCase` for classes, `snake_case` for functions and variables, and
  `UPPER_SNAKE_CASE` for constants.
- Add docstrings and useful type annotations to public interfaces.
- Keep identifiers in English; comments and user-facing UI text may be Chinese.
- Preserve the local-only design: do not add credentials, telemetry, or external
  data transfer without an explicit product decision.

## Tests and Verification

- Test files belong in `e_pod_line_simulator/tests/` and use `test_*.py` names.
- Add focused tests for new behavior and regression tests for bug fixes.
- Run `python -m pytest tests -q` before claiming completion.
- For changes to models, simulation, scenarios, reports, or utilities, inspect
  coverage with `pytest-cov`; the target for core modules is at least 80%.
- UI changes require both relevant automated checks and a manual launch check
  when the environment supports Tkinter.

## Documentation Discipline

- `README.md` is the public product overview and primary marketing entry point.
- `docs/user-guide.md` is the authoritative installation, usage, configuration,
  and FAQ guide.
- `CONTRIBUTING.md` is the public contribution process; do not expose internal
  agent instructions there.
- Update `CHANGELOG.md` for user-visible release changes.
- Keep version numbers, download names, screenshots, links, and UI labels aligned
  with current source and release artifacts.
- Do not create duplicate project overviews inside application subdirectories.

## Commit and Pull Request Guidelines

- Prefer Conventional Commits, for example `fix: 修复仿真重启问题` or
  `docs: 更新用户指南`.
- Keep each commit and pull request focused on one logical change.
- Pull requests should explain what changed, why it changed, and how it was
  verified; link relevant issues.
- Include before/after screenshots for UI changes and example inputs/outputs for
  calculation changes.
- Never publish, tag, release, or push without explicit owner authorization.

## Security and Data Handling

- Never commit credentials, personal information, customer data, or unredacted
  production configurations.
- Sanitize Excel imports and validate station parameters at input boundaries.
- Do not commit generated caches, build output, logs, or OS metadata.
- Preserve user changes in a dirty worktree and avoid destructive Git commands.

## Agent Instructions

- Read `README.md`, `docs/user-guide.md`, and the relevant source/tests before
  editing behavior.
- Prefer evidence from current source, tests, workflows, and release artifacts
  over historical documentation.
- Keep user-facing responses in Chinese unless the user asks otherwise.
- Verify the requested scope before editing; do not modify unrelated files.

## Active Development Roadmap

- v3.5.0 scope, implementation status, validation and handoff: `docs/roadmap-v3.5.0.md`.
- Read that roadmap before continuing this version. Update its checklist and dated
  process log after each implementation or verification stage; keep incomplete
  validation and deferred capabilities explicit.
