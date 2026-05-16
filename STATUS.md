# Status

Implemented in `mwm.py`.

Verification:

```sh
make
```

Last result: passes locally with:

```sh
make lint
make test
```

Coverage is primarily doctests for geometry, layout state, focus selection, IPC
parsing, keybinding parsing, and daemon dispatch with an in-memory API.

Current runtime behavior:

- `./mwm.py daemon` starts the macOS tiling daemon with built-in keybindings.
- `./mwm.py daemon --no-keybindings` starts without keybindings.
- `./mwm.py daemon --keybindings PATH` loads a simple JSON map from key chord to
  command.
- The periodic repair poll is disabled by default; use
  `./mwm.py daemon --poll-seconds 30` to enable a fallback repair poll.
- Keybindings use physical key codes where possible, so Option-modified
  characters still match bindings such as `alt-h`.
- Directional keybindings support both `h/j/k/l` and Command arrow keys, e.g.
  `cmd-left` focuses left and `shift-cmd-left` moves left.
- `shift-alt-q` closes the currently focused window by pressing its AX close
  button.
- `alt-1` through `alt-0` switches to Desktop 1 through 10.
- `goto-desktop` posts macOS's `Control`+number Desktop shortcut.
- Client commands are silent by default. Use `--verbose` to print daemon
  responses.
- `make install` installs `mwm` into `~/.local/bin` and generates a LaunchAgent
  that starts in `$HOME`.

Runtime macOS Accessibility paths still need live validation after each
behavioral change, because doctests use an in-memory API.
