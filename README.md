# mwm

Small macOS window manager.

## Use

```sh
make install
```

Grant Accessibility permission if macOS asks for it.

```sh
mwm status --verbose
make uninstall
```

## Shortcuts

```text
alt-h/j/k/l, cmd-arrows                  focus left/down/up/right
shift-alt-h/j/k/l, shift-cmd-arrows      move left/down/up/right
alt-1/2/3/.../0                          switch to Desktop 1/2/3/.../10
shift-alt-q                              close focused window
alt-f                                    fullscreen
alt-r                                    retile
ctrl-alt-1/2/3/4/5                       columns 1/2/3/2.5/1.7
ctrl-alt-s                               status
```

## Commands

```sh
mwm daemon
mwm daemon --poll-seconds 30
mwm focus left
mwm move right
mwm goto-desktop 2
mwm close
mwm fullscreen
mwm columns 3
mwm retile
mwm status --verbose
mwm stop
```
