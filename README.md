# mwm

Small macOS window manager.

## Use

```sh
make install
```

Grant Accessibility permission if macOS asks for it.

```sh
make uninstall
```

## Shortcuts

```text
alt-h/j/k/l          focus left/down/up/right
shift-alt-h/j/k/l    move left/down/up/right
shift-alt-q          close focused window
alt-f                fullscreen
alt-r                retile
ctrl-alt-1/2/3/4/5   columns 1/2/3/2.5/1.7
ctrl-alt-s           status
```

## Commands

```sh
./mwm.py daemon
./mwm.py focus left
./mwm.py move right
./mwm.py close
./mwm.py fullscreen
./mwm.py columns 3
./mwm.py retile
./mwm.py status --verbose
./mwm.py stop
```
