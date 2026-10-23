---
name: Debut open-folder hotkey
overview: Add an [o] console hotkey to examples/debut_example.py that opens the recording output directory in the system file manager with the currently recording file selected.
todos:
  - id: poll
    content: Refactor _poll_manual_split_key into _poll_console_keys returning a set of pressed keys
    status: completed
  - id: reveal
    content: Add _reveal_in_file_manager (explorer /select on Windows, open -R on macOS, xdg-open dir on Linux) with safe error handling
    status: completed
  - id: loop
    content: Wire 'o' and 's' handling into the main loop
    status: completed
  - id: hints
    content: Update docstring, startup print, and log hints to mention 'o'
    status: completed
isProject: false
---

# Add [o] hotkey to reveal the active recording

All changes are in [examples/debut_example.py](examples/debut_example.py).

## 1. Generalize key polling
Right now `_poll_manual_split_key()` drains `msvcrt` and returns `True` only for `s`. Change it to `_poll_console_keys() -> set[str]`, which returns the set of lowercased keys pressed during the drain. It will still skip the `\x00`/`\xe0` extended-key prefixes, and returns an empty set when `msvcrt` is `None`.

## 2. Add a reveal helper
Add `_reveal_in_file_manager(target: Optional[Path], fallback_dir: Path) -> None`:
- If `target` exists, select that file. Otherwise open `fallback_dir`. This covers the brief gap between segments during a split.
- Windows: `subprocess.Popen(f'explorer /select,"{target.resolve()}"')`. The command is passed as a single string so the `/select,"..."` quoting holds up for paths with spaces. Explorer's exit code is unreliable (it often returns 1), so don't check it.
- macOS: `["open", "-R", str(target)]`. Linux: `["xdg-open", str(dir)]`. Linux file managers have no standard way to select a file, so only the folder opens.
- Wrap everything in try/except, log failures, and never let this interrupt recording. Log or print the path being revealed.
- Add `import subprocess`.

## 3. Wire it into the main loop
Replace the current `s` check (around line 343) with:

```python
keys = _poll_console_keys()
if "o" in keys:
    _reveal_in_file_manager(recorder.get_current_file(), Path(output_dir))
if manual_split_enabled and "s" in keys and recorder.is_recording():
    _split_or_fail("manual_split")
```

The `o` key works whether or not `manual_split_enabled` is set. Keys are still drained when splitting is disabled, which is harmless.

## 4. Update the hints
Mention `o` in the module docstring (the Features and Usage lines), in the "Recording in progress..." print, and in the startup `logger.info` hint.
