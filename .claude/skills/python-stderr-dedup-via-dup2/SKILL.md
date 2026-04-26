---
name: python-stderr-dedup-via-dup2
description: |
  Contain runaway stderr spam in a Python daemon where a transitive library
  (e.g. firebase_messaging, asyncio, a C extension) writes the same traceback
  or error line thousands of times per second without exposing a knob to
  disable it. Use when: (1) a long-running Python service fills its logfile
  with gigabytes of duplicate tracebacks, (2) wrapping `sys.stderr` at the
  Python level doesn't catch the spam because the library writes via a
  logger/C-ext that goes directly to FD 2, (3) you need rate-limiting AND
  dedup AND a force-exit escape hatch so launchd/systemd can restart the
  process fresh. Pattern: pipe() + os.dup2(write_end, 2) + pump thread +
  dedup filter + size-based self-exit.
author: Claude Code
version: 1.0.0
date: 2026-04-18
---

# Python stderr dedup/rate-limit via FD-level `os.dup2`

## Problem

A Python daemon inherits a broken transitive dependency that enters a tight
exception loop and writes the same traceback to stderr thousands of times per
second. You can't patch the library (maybe it's a transitive dep of a
transitive dep), you can't disable its logging, and replacing
`sys.stderr = MyFilter(sys.stderr)` at the Python level only catches writes
that go through the Python `sys.stderr` object — it misses:

- `logging.StreamHandler` that captured a reference to the real stderr at module load
- C extensions that call `write(2, buf, len)` directly
- Any subprocess or threading lib that dup'd FD 2 before your override

Result: a 703GB logfile in 4 days, a full disk, and cascading failures across
everything that needs to write.

## Context / Trigger Conditions

- Your service's logfile grows at MB/sec and tail shows the same traceback repeating
- `sys.stderr = SomeWrapper(...)` in `main()` had no effect — spam still hits the file
- You suspect library code is writing via `os.write(2, ...)` or a logger with a captured FD
- The process keeps running (it doesn't crash) because the loop catches the exception internally
- You run under launchd/systemd with KeepAlive/Restart enabled — safe to force-exit

## Solution

Install the guard at **FD 2 level**, not the `sys.stderr` level. Create a pipe,
`dup2` the write end onto FD 2 (so *every* write to stderr from *anywhere* in
the process — Python, C, threads, subprocs — lands in the pipe), and run a
pump thread that reads the pipe and feeds a dedup/rate-limit filter, which
then writes to the *real* stderr (saved earlier via `os.dup(2)`).

Add a kill switch: if stderr bytes exceed a threshold in a rolling window,
`os._exit(1)` so the supervisor restarts the process clean.

```python
import os, sys, threading, time

_MAX_BPS = 64 * 1024                   # 64 KB/s sustained after burst
_WINDOW_SEC = 60
_EXIT_BYTES = 16 * 1024 * 1024         # 16 MB in 60s → self-exit
_FLUSH_SEC = 5                          # flush dup-count summary every 5s


class _DedupStderr:
    def __init__(self, real):
        self._real = real
        self._lock = threading.Lock()
        self._last = ""
        self._dups = 0
        self._last_flush = time.monotonic()
        self._win_start = time.monotonic()
        self._win_bytes = 0

    def _emit(self, s):
        try:
            self._real.write(s); self._real.flush()
        except Exception:
            pass

    def write(self, data):
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        with self._lock:
            now = time.monotonic()
            if now - self._win_start > _WINDOW_SEC:
                self._win_start = now; self._win_bytes = 0
            self._win_bytes += len(data)
            if self._win_bytes > _EXIT_BYTES:
                self._emit(f"[stderr-dedup] FATAL: {self._win_bytes}B in "
                           f"{_WINDOW_SEC}s, exiting for supervisor restart\n")
                os._exit(1)
            if self._win_bytes > _MAX_BPS * _WINDOW_SEC:
                return len(data)  # drop
            for line in data.splitlines(keepends=True):
                if line == self._last:
                    self._dups += 1
                    if now - self._last_flush > _FLUSH_SEC:
                        if self._dups > 1:
                            self._emit(f"[stderr-dedup] x{self._dups - 1} "
                                       f"suppressed duplicates\n")
                        self._emit(line)
                        self._dups = 1
                        self._last_flush = now
                else:
                    if self._dups > 1:
                        self._emit(f"[stderr-dedup] x{self._dups - 1} "
                                   f"suppressed duplicates\n")
                    self._emit(line)
                    self._last = line
                    self._dups = 1


def install_stderr_guard():
    # Save a reference to the real stderr BEFORE we clobber FD 2.
    real_fd = os.dup(2)
    real = os.fdopen(real_fd, "w", buffering=1, encoding="utf-8",
                     errors="replace")
    dedup = _DedupStderr(real)

    r_fd, w_fd = os.pipe()
    os.dup2(w_fd, 2)     # FD 2 now points to pipe write end
    os.close(w_fd)       # original pipe-write handle no longer needed
    sys.stderr = os.fdopen(2, "w", buffering=1, encoding="utf-8",
                           errors="replace", closefd=False)

    def pump():
        with os.fdopen(r_fd, "rb", buffering=0) as r:
            buf = b""
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    dedup.write(line.decode("utf-8", "replace") + "\n")
                if len(buf) > 65536:
                    dedup.write(buf.decode("utf-8", "replace")); buf = b""

    threading.Thread(target=pump, name="stderr-dedup", daemon=True).start()


install_stderr_guard()
# ...rest of imports, including the misbehaving library...
```

**Install BEFORE importing the misbehaving library**, so its module-level
loggers capture your piped FD 2 and not the original.

## Verification

- Simulate spam: have a thread loop `print("same line", file=sys.stderr)` 100k/sec.
  Observe logfile contains one line plus `[stderr-dedup] xN suppressed` lines.
- Try `os.write(2, b"raw write\n")` from within a test — it should also be
  captured (proves FD-level, not sys.stderr-level).
- Let the spam run past the threshold — process should exit with code 1 and
  your supervisor should restart it.

## Example: 2026-04-18 dog-walk-listener

`firebase_messaging.fcmpushclient._listen` caught an asyncio
`StreamReader._exception` but didn't reconnect, printing the same traceback
at 10k+/sec. A Python-level `sys.stderr` wrapper would miss it because the
traceback was being printed through asyncio's default exception handler
(which had captured the real stderr at logging-module import time). The
FD-level pipe caught it, dedupe collapsed 703GB/4days of duplicates, and the
16MB/min ceiling tripped `os._exit(1)` so launchd KeepAlive restarted
cleanly.

## Notes

- **Don't use `PIPE_BUF`-dependent atomic writes from multiple threads** —
  the dedup thread serializes via its own lock, but writes to the pipe from
  many threads are only atomic up to PIPE_BUF (usually 512 bytes on macOS).
  This is fine for tracebacks (short lines) but know the limit.
- **Test the kill switch carefully.** If `os._exit(1)` fires during startup
  before the supervisor is ready to restart, you'll crash-loop. Give the
  process ~30s of grace before the window starts counting.
- **Don't install in short-lived scripts.** The pump thread is a daemon and
  disappears at interpreter exit, but the pipe FD leak is ~0 cost for daemons
  and real cost for tiny tools.
- If you're on systemd instead of launchd, set `Restart=always` +
  `RestartSec=10` and this pattern works identically.
- The `sys.stderr = os.fdopen(2, ..., closefd=False)` dance is important —
  without `closefd=False` Python will close FD 2 at shutdown, which is fine
  at shutdown but scary if anything else is still writing.

## References

- `os.dup2` and `os.pipe` — `python3 -c "help(os.dup2)"` and `help(os.pipe)`
- `pipe(7)` — `man 7 pipe` on macOS/Linux for PIPE_BUF atomic-write guarantees
