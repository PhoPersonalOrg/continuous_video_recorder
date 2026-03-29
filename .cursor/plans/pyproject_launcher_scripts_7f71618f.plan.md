---
name: pyproject launcher scripts
overview: Add `[project.scripts]` for the two example programs so `uv run &lt;name&gt;` and uv-launcher-generated wrappers work. This requires treating `examples` as an installable package because console_scripts must reference importable `module:callable` targets.
todos:
  - id: package-examples
    content: Add examples/__init__.py and set setuptools include = ["src", "examples"]
    status: completed
  - id: project-scripts
    content: Add [project.scripts] with debut-example and epocx-eyecam-usb entry points
    status: completed
  - id: verify-uv-run
    content: Run uv sync and uv run for both script keys
    status: completed
isProject: false
---

# Add launcher scripts for example programs

## Context

- [uv-launcher](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\uv-launcher\README.md) discovers `**[project.scripts]**` and generates `scripts/*.bat` / `.ps1` / `.sh` that run `**uv run <script-key>**` (the TOML key, not the `module:function` value). So the keys you choose are the CLI names users type.
- [continuous_video_recorder/pyproject.toml](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\pyproject.toml) has no `[project.scripts]` yet, and setuptools only packages `[src](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\pyproject.toml)` via `[tool.setuptools.packages.find]`, so nothing under `[examples/](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\examples)` is importable after install — a blocker for console_scripts.

Both targets already expose `**main()**` and use `if __name__ == "__main__":` (`[debut_example.py` ~102, ~194](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\examples\debut_example.py); `[epocXEyeCamUSB_example.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\examples\epocXEyeCamUSB_example.py)`).

## Changes

### 1. Make `examples` a package (minimal)

- Add `[examples/__init__.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\examples\__init__.py)` (empty file is enough).
- In `[pyproject.toml](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\pyproject.toml)`, extend `**[tool.setuptools.packages.find]**` → `include = ["src", "examples"]`.

This matches how the examples import `src.*` (project root on path for editable installs).

### 2. Register console scripts

Add a `**[project.scripts]**` table to `[pyproject.toml](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\pyproject.toml)`, for example:


| Key (what you run) | Entry point                            |
| ------------------ | -------------------------------------- |
| `debut-example`    | `examples.debut_example:main`          |
| `epocx-eyecam-usb` | `examples.epocXEyeCamUSB_example:main` |


(Exact key names can be adjusted; avoid spaces. The second module name must match the filename’s stem: `epocXEyeCamUSB_example`.)

### 3. Optional behavior note (no change required unless you care)

Setuptools will invoke `**main()**` directly. The extra `**try` / `except**` in each file’s `if __name__ == "__main__":` block will not run via the installed console entry. If you want identical user-facing error handling, a small follow-up would be to move that wrapper into a function (e.g. `cli()`) and point the script at `cli` instead of `main`.

## Verify after implementation

From the project root:

- `uv sync` (refreshes entry points)
- `uv run debut-example` and `uv run epocx-eyecam-usb`
- Optionally: `uv-launcher generate --path <project>` if you rely on generated `scripts/` launchers

