# Status

Implemented in `m3.py`.

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

- `./m3.py daemon` starts the macOS tiling daemon with built-in keybindings.
- `./m3.py daemon --no-keybindings` starts without keybindings.
- `./m3.py daemon --keybindings PATH` loads a simple JSON map from key chord to
  command.
- Keybindings use physical key codes where possible, so Option-modified
  characters still match bindings such as `alt-h`.
- `shift-alt-q` closes the currently focused window by pressing its AX close
  button.

Runtime macOS Accessibility paths still need live validation after each
behavioral change, because doctests use an in-memory API.
