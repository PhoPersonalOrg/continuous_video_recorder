---
name: Web server instant exit
overview: "`uv run src/web_stream_server.py` exits immediately with code 0 because the file never starts the server or blocks the main thread—it only defines classes. Documentation in `debut_example.py` incorrectly suggests this file is runnable as-is."
todos:
  - id: add-main-cli
    content: "Add `if __name__ == \"__main__\"` in web_stream_server.py: load config, WebStreamServer, start_server(), block main (Event wait + SIGINT) or run Flask on main thread"
    status: completed
  - id: fix-debut-doc
    content: Align debut_example.py web viewer instructions with real runnable command/path
    status: completed
isProject: false
---

# Why `web_stream_server.py` exits instantly

## Root cause

`[src/web_stream_server.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\src\web_stream_server.py)` contains **no** `if __name__ == "__main__":` block and no other top-level code that constructs `WebStreamServer`, calls `start_server()`, or runs Flask. Executing the file only runs imports and class definitions, then the interpreter reaches end-of-file and **exits normally**—hence exit code 0 and no traceback.

This matches the terminal you shared: the prompt returns right after `uv run .\src\web_stream_server.py` with `last_exit_code: 0`.

## Secondary trap (if you add a `main` without blocking)

`start_server()` starts Flask in a **daemon** thread:

```501:512:c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\src\web_stream_server.py
                self.server_thread = threading.Thread(
                    target=self.flask_app.run,
                    kwargs={
                        'host': host,
                        'port': try_port,
                        'threaded': True,
                        'use_reloader': False,
                        'debug': False
                    },
                    daemon=True
                )
```

If `__main__` only called `start_server()` and then returned, the process would **still** exit immediately: the main thread would finish, and CPython does not wait for daemon threads.

## Intended usage today

Integration is via importing `WebStreamServer` from another process (e.g. the recorder) or tests—see `[test_manual_web_server.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\test_manual_web_server.py)` and `[examples/debut_example.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\examples\debut_example.py)`. The example’s comment “`python src/web_stream_server.py`” is **misleading** until a real CLI entry exists.

## Recommended fix (when you implement)

1. Add a `__main__` block that loads config (reuse `[ConfigLoader](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\src\config_loader.py)` / same YAML pattern as the recorder), builds `WebStreamServer` with appropriate `camera_ids`, calls `start_server()`, then **keeps the main thread alive** until Ctrl+C, e.g. `threading.Event().wait()` in a loop with `KeyboardInterrupt`, or call `flask_app.run()` on the main thread instead of a daemon thread for the standalone script path only.
2. Update `[examples/debut_example.py](c:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\continuous_video_recorder\examples\debut_example.py)` lines 16–17 to match the actual command once added.

No code changes are made in this planning step; the above is the full explanation and a minimal implementation outline.