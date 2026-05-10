# m3

`m3.py` is a small macOS tiling daemon controlled through a Unix socket. It uses
PyObjC for Accessibility, CoreFoundation, AppKit, and Quartz access, while key
bindings are expected to be handled elsewhere, for example by `skhd`.

Start the daemon:

```sh
./m3.py daemon --columns 2.5
```

Useful client commands:

```sh
./m3.py focus left
./m3.py focus right
./m3.py move left
./m3.py move right
./m3.py fullscreen
./m3.py columns 3
./m3.py retile
./m3.py status
./m3.py stop
```

The layout is horizontal columns with vertical splits inside each column. A
fractional column count makes the final column fractional: `--columns 2.5`
produces two normal-width columns and a half-width final column when there are
enough windows.

The fullscreen command is deliberately local to the tiler: it fills the current
screen with the focused window and leaves macOS native fullscreen spaces alone.
