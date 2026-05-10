# Status

Implemented in `m3.py`.

Verification:

```sh
make
```

Last result: passes locally on Linux without PyObjC installed, including 87
doctests for layout geometry, state movement, IPC parsing, and daemon dispatch
with an in-memory API. Runtime macOS API paths are lazy-loaded and still need to
be exercised on macOS with Accessibility permission granted.
