# m3

`m3.py` is a small macOS tiling daemon. It uses PyObjC for Accessibility,
CoreFoundation, AppKit, and Quartz access, and includes simple built-in
keybindings through `pynput`. The daemon is also controlled through a Unix socket,
so commands can still be sent from scripts or another hotkey daemon.

Start the daemon:

```sh
./m3.py daemon --columns 2.5
```

The daemon starts with built-in keybindings. To run without keybindings:

```sh
./m3.py daemon --no-keybindings
```

To use a simple JSON keybinding file:

```sh
./m3.py daemon --keybindings ~/.config/m3/keybindings.json
```

On macOS, keybindings are matched by physical key code where possible, so
`alt-h` still means the `h` key even if Option would normally type a different
character.

Default keybindings:

```text
alt-h          focus left
alt-j          focus down
alt-k          focus up
alt-l          focus right

shift-alt-h    move left
shift-alt-j    move down
shift-alt-k    move up
shift-alt-l    move right
shift-alt-q    close focused window

alt-f          fullscreen
alt-r          retile

ctrl-alt-1     columns 1
ctrl-alt-2     columns 2
ctrl-alt-3     columns 3
ctrl-alt-4     columns 2.5
ctrl-alt-5     columns 1.7

ctrl-alt-s     status
```

Keybinding JSON is a simple object mapping a key chord to an `m3` command. This
example matches the built-in defaults:

```json
{
  "alt-h": "focus left",
  "alt-j": "focus down",
  "alt-k": "focus up",
  "alt-l": "focus right",
  "shift-alt-h": "move left",
  "shift-alt-j": "move down",
  "shift-alt-k": "move up",
  "shift-alt-l": "move right",
  "shift-alt-q": "close",
  "alt-f": "fullscreen",
  "alt-r": "retile",
  "ctrl-alt-1": "columns 1",
  "ctrl-alt-2": "columns 2",
  "ctrl-alt-3": "columns 3",
  "ctrl-alt-4": "columns 2.5",
  "ctrl-alt-5": "columns 1.7",
  "ctrl-alt-s": "status"
}
```

Useful client commands:

```sh
./m3.py focus left
./m3.py focus right
./m3.py move left
./m3.py move right
./m3.py close
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
