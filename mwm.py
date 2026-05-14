#!/usr/bin/env python3
################################################################
# Copyright (c) 2026 Witalis Domitrz <witekdomitrz@gmail.com>
# AGPL License
################################################################
#
# /// script
# dependencies = [
#   "pynput",
#   "pyobjc-framework-ApplicationServices",
#   "pyobjc-framework-Cocoa",
#   "pyobjc-framework-Quartz",
# ]
# ///

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import queue
import shlex
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import ClassVar, Literal, Protocol, assert_never, cast

Direction = Literal["left", "right", "up", "down"]
ModifierName = Literal["cmd", "ctrl", "alt", "shift"]
CommandKind = Literal[
    "focus",
    "move",
    "goto-desktop",
    "fullscreen",
    "close",
    "columns",
    "retile",
    "status",
    "stop",
]
CliCommand = Literal[
    "daemon",
    "focus",
    "move",
    "goto-desktop",
    "fullscreen",
    "close",
    "columns",
    "retile",
    "status",
    "stop",
]
JsonMap = dict[str, object]
JsonObject = dict[str, object]
PAIR_LENGTH: int = 2
COMMAND_WITH_ARG_LENGTH: int = 2
COMMAND_WITHOUT_ARG_LENGTH: int = 1
DESKTOP_COUNT: int = 10
DESKTOP_KEY_CODES: dict[int, int] = {
    1: 0x12,
    2: 0x13,
    3: 0x14,
    4: 0x15,
    5: 0x17,
    6: 0x16,
    7: 0x1A,
    8: 0x1C,
    9: 0x19,
    10: 0x1D,
}
FOCUS_MOVE_COMMANDS: tuple[Literal["focus", "move"], ...] = ("focus", "move")
CLIENT_COMMANDS_WITHOUT_ARGS: tuple[
    Literal["fullscreen", "close", "retile", "status", "stop"], ...
] = ("fullscreen", "close", "retile", "status", "stop")
UTILITY_COMMANDS: tuple[Literal["retile", "status", "stop"], ...] = (
    "retile",
    "status",
    "stop",
)
COMMAND_KINDS: tuple[CommandKind, ...] = (
    "focus",
    "move",
    "goto-desktop",
    "fullscreen",
    "close",
    "columns",
    "retile",
    "status",
    "stop",
)
CLI_COMMANDS: tuple[CliCommand, ...] = ("daemon", *COMMAND_KINDS)


class DynamicObjC(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> DynamicObjC: ...
    def __getattr__(self, name: str) -> DynamicObjC: ...
    def __iter__(self) -> Iterator[object]: ...
    def __bool__(self) -> bool: ...
    def __int__(self) -> int: ...
    def __float__(self) -> float: ...
    def __or__(self, other: object) -> DynamicObjC: ...
    def __ror__(self, other: object) -> DynamicObjC: ...


class CocoaPoint(Protocol):
    x: float
    y: float


class CocoaSize(Protocol):
    width: float
    height: float


class CocoaRect(Protocol):
    origin: CocoaPoint
    size: CocoaSize


@dataclass(frozen=True, kw_only=True)
class AxPoint:
    x: float
    y: float


@dataclass(frozen=True, kw_only=True)
class AxSize:
    width: float
    height: float


@dataclass(frozen=True, kw_only=True)
class AxCopyValue:
    error: object
    value: object


@dataclass(frozen=True, kw_only=True)
class AxValue:
    ok: bool
    value: object


def to_float(value: object) -> float:
    return float(cast(str | bytes | int | float, value))


def to_int(value: object) -> int:
    return int(cast(str | bytes | int | float, value))


def parse_json_map(payload: bytes) -> JsonMap:
    raw = json.loads(payload.decode())
    assert isinstance(raw, dict)
    return cast(JsonMap, raw)


def parse_pair(raw: object) -> tuple[object, object]:
    pair = cast(tuple[object, ...], raw) if isinstance(raw, tuple) else None
    assert pair is not None
    assert len(pair) == PAIR_LENGTH
    first, second = pair
    return (first, second)


def parse_ax_copy_value(raw: object) -> AxCopyValue:
    error, value = parse_pair(raw)
    return AxCopyValue(error=error, value=value)


def parse_ax_value(raw: object) -> AxValue:
    ok, value = parse_pair(raw)
    return AxValue(ok=bool(ok), value=value)


def parse_ax_point(raw: object) -> AxPoint:
    if isinstance(raw, tuple):
        x, y = parse_pair(cast(tuple[object, ...], raw))
        return AxPoint(x=to_float(x), y=to_float(y))
    point = cast(CocoaPoint, raw)
    return AxPoint(x=float(point.x), y=float(point.y))


def parse_ax_size(raw: object) -> AxSize:
    if isinstance(raw, tuple):
        width, height = parse_pair(cast(tuple[object, ...], raw))
        return AxSize(width=to_float(width), height=to_float(height))
    size = cast(CocoaSize, raw)
    return AxSize(width=float(size.width), height=float(size.height))


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir())
    return base / f"mwm-{os.getuid()}.sock"


def import_keyboard() -> DynamicObjC:
    try:
        module = importlib.import_module("pynput.keyboard")
    except ImportError as error:
        msg = "pynput is required for built-in keybindings; use --no-keybindings to run without it."
        raise RuntimeError(msg) from error
    return cast(DynamicObjC, cast(ModuleType, module))


@dataclass(frozen=True, kw_only=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    MIN_WIDTH: ClassVar[int] = 40
    MIN_HEIGHT: ClassVar[int] = 40

    @classmethod
    def from_ax_values(cls, *, position: AxPoint, size: AxSize) -> Rect:
        return cls(
            x=round(position.x),
            y=round(position.y),
            width=round(size.width),
            height=round(size.height),
        )

    @classmethod
    def from_quartz_bounds(cls, raw: Mapping[object, object]) -> Rect | None:
        """Convert a CGWindow bounds dictionary into a rectangle.

        >>> Rect.from_quartz_bounds({"X": 1.2, "Y": 3.8, "Width": 10, "Height": 20})
        Rect(x=1, y=4, width=10, height=20)
        >>> Rect.from_quartz_bounds({"X": 1})
        """
        try:
            return cls(
                x=round(to_float(raw["X"])),
                y=round(to_float(raw["Y"])),
                width=round(to_float(raw["Width"])),
                height=round(to_float(raw["Height"])),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_cocoa_rect(
        cls,
        raw: CocoaRect,
        *,
        main_screen_height: float,
    ) -> Rect:
        x = round(float(raw.origin.x))
        height = round(float(raw.size.height))
        y = round(main_screen_height - float(raw.origin.y) - float(raw.size.height))
        width = round(float(raw.size.width))
        return cls(x=x, y=y, width=width, height=height)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    @property
    def valid_window_size(self) -> bool:
        return self.width >= self.MIN_WIDTH and self.height >= self.MIN_HEIGHT

    def contains_point(self, *, x: float, y: float) -> bool:
        return self.x <= x <= self.right and self.y <= y <= self.bottom

    def intersection_area(self, other: Rect) -> int:
        width = max(0, min(self.right, other.right) - max(self.x, other.x))
        height = max(0, min(self.bottom, other.bottom) - max(self.y, other.y))
        return width * height

    @staticmethod
    def _partition(total: int, weights: tuple[float, ...]) -> tuple[int, ...]:
        """Split integer pixels proportionally and preserve the full total.

        >>> Rect._partition(10, (1, 1, 1))
        (4, 3, 3)
        >>> Rect._partition(100, (0.4, 0.4, 0.2))
        (40, 40, 20)
        """
        if not weights:
            return ()
        weight_sum = sum(weights)
        raw_sizes = tuple(total * weight / weight_sum for weight in weights)
        sizes = [math.floor(size) for size in raw_sizes]
        remaining = total - sum(sizes)
        fractions = sorted(
            enumerate(raw_sizes),
            key=lambda item: (item[1] - math.floor(item[1]), -item[0]),
            reverse=True,
        )
        for index, _size in fractions[:remaining]:
            sizes[index] += 1
        return tuple(sizes)

    def split_columns(self, *, count: int, columns: float) -> tuple[Rect, ...]:
        """Return i3-like columns, including fractional final columns.

        >>> frame = Rect(x=0, y=0, width=1000, height=500)
        >>> [rect.width for rect in frame.split_columns(count=3, columns=2.5)]
        [400, 400, 200]
        >>> [rect.width for rect in frame.split_columns(count=2, columns=2.5)]
        [400, 600]
        """
        if count <= 0:
            return ()
        if count == 1:
            return (self,)

        slot = 1.0 / columns
        weights = tuple([slot] * (count - 1) + [max(0.001, 1 - slot * (count - 1))])
        widths = self._partition(self.width, weights)
        x = self.x
        rects: list[Rect] = []
        for width in widths:
            rects.append(Rect(x=x, y=self.y, width=width, height=self.height))
            x += width
        return tuple(rects)

    def split_rows(
        self, *, count: int, weights: tuple[float, ...] | None = None
    ) -> tuple[Rect, ...]:
        if count <= 0:
            return ()
        if count == 1:
            return (self,)

        row_weights = usable_weights(weights=weights, count=count)
        heights = self._partition(self.height, row_weights)
        y = self.y
        rects: list[Rect] = []
        for height in heights:
            rects.append(Rect(x=self.x, y=y, width=self.width, height=height))
            y += height
        return tuple(rects)

    def distance_to(self, other: Rect) -> float:
        return abs(self.center_x - other.center_x) + abs(self.center_y - other.center_y)

    def as_ax_position(self) -> tuple[int, int]:
        return (self.x, self.y)

    def as_ax_size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def as_key(self) -> str:
        return f"{self.x},{self.y},{self.width},{self.height}"


@dataclass(frozen=True, kw_only=True)
class WindowInfo:
    key: str
    pid: int
    ax: object
    title: str
    frame: Rect
    screen_key: str
    window_number: int | None
    order: int


@dataclass(frozen=True, kw_only=True)
class ScreenInfo:
    key: str
    frame: Rect


@dataclass(frozen=True, kw_only=True)
class VisibleWindowIndex:
    numbers_by_pid: dict[int, set[int]]
    order_by_pid_number: dict[tuple[int, int], int]
    frames_by_pid: dict[int, tuple[tuple[int, Rect], ...]] = field(default_factory=dict)

    def contains(
        self, *, pid: int, number: int | None, frame: Rect | None = None
    ) -> bool:
        if number is None:
            if frame is None:
                return pid in self.frames_by_pid or pid in self.numbers_by_pid
            return self._nearest_frame_order(pid=pid, frame=frame) is not None
        return number in self.numbers_by_pid.get(pid, set())

    def order(self, *, pid: int, number: int | None, frame: Rect | None = None) -> int:
        if number is None:
            if frame is None:
                return 1_000_000
            return self._nearest_frame_order(pid=pid, frame=frame) or 1_000_000
        return self.order_by_pid_number.get((pid, number), 1_000_000)

    def _nearest_frame_order(self, *, pid: int, frame: Rect) -> int | None:
        frames = self.frames_by_pid.get(pid)
        if not frames:
            return None
        order, _match = max(
            frames,
            key=lambda item: (
                item[1].intersection_area(frame),
                -item[1].distance_to(frame),
            ),
        )
        return order


@dataclass(frozen=True, kw_only=True)
class LayoutConfig:
    columns: float = 2.0
    poll_seconds: float | Literal["disabled"] = 30.0
    socket_path: Path = field(default_factory=default_socket_path)

    def __post_init__(self) -> None:
        """Validate user-controlled layout values.

        >>> LayoutConfig(columns=2.5, poll_seconds=0.25).max_column_count
        3
        >>> LayoutConfig(poll_seconds="disabled").poll_seconds
        'disabled'
        >>> LayoutConfig(columns=2.5).target_column_count(window_count=2)
        2
        >>> LayoutConfig(columns=float("nan"))
        Traceback (most recent call last):
        ...
        ValueError: columns must be a finite number at least 1
        >>> LayoutConfig(poll_seconds=0)
        Traceback (most recent call last):
        ...
        ValueError: poll_seconds must be a finite positive number
        """
        if not math.isfinite(self.columns) or self.columns < 1:
            msg = "columns must be a finite number at least 1"
            raise ValueError(msg)
        if self.poll_seconds != "disabled" and (
            not math.isfinite(self.poll_seconds) or self.poll_seconds <= 0
        ):
            msg = "poll_seconds must be a finite positive number"
            raise ValueError(msg)

    def with_columns(self, columns: float) -> LayoutConfig:
        return LayoutConfig(
            columns=columns,
            poll_seconds=self.poll_seconds,
            socket_path=self.socket_path,
        )

    @property
    def max_column_count(self) -> int:
        return max(1, math.ceil(self.columns))

    def target_column_count(self, *, window_count: int) -> int:
        return min(window_count, self.max_column_count)


@dataclass(frozen=True, kw_only=True)
class IpcRequest:
    kind: CommandKind
    direction: Direction | None = None
    desktop: int | None = None
    columns: float | None = None

    @classmethod
    def from_json(cls, payload: bytes) -> IpcRequest:
        raw = parse_json_map(payload)
        assert "kind" in raw
        kind = parse_command_kind(str(raw["kind"]))
        direction = (
            parse_direction(str(raw["direction"]))
            if raw.get("direction") is not None
            else None
        )
        desktop = (
            parse_desktop_number(raw["desktop"])
            if raw.get("desktop") is not None
            else None
        )
        columns = to_float(raw["columns"]) if raw.get("columns") is not None else None
        return cls(
            kind=kind,
            direction=direction,
            desktop=desktop,
            columns=columns,
        )

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "kind": self.kind,
                "direction": self.direction,
                "desktop": self.desktop,
                "columns": self.columns,
            },
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True, kw_only=True)
class IpcResponse:
    ok: bool
    message: str

    @classmethod
    def from_json(cls, payload: bytes) -> IpcResponse:
        raw = parse_json_map(payload)
        assert "ok" in raw
        assert "message" in raw
        return cls(ok=bool(raw["ok"]), message=str(raw["message"]))

    def to_json(self) -> bytes:
        return json.dumps(
            {"ok": self.ok, "message": self.message}, separators=(",", ":")
        ).encode()


@dataclass(frozen=True, kw_only=True)
class AppObserver:
    pid: int
    app: object
    observer: object


@dataclass(kw_only=True)
class PendingIpcCall:
    payload: bytes
    source: str = "ipc"
    event: threading.Event = field(default_factory=threading.Event)
    response: IpcResponse | None = None

    def respond(self, response: IpcResponse) -> None:
        self.response = response
        self.event.set()

    def wait(self, *, timeout: float) -> IpcResponse:
        if not self.event.wait(timeout=timeout):
            return IpcResponse(ok=False, message="daemon did not answer in time")
        if self.response is None:
            return IpcResponse(ok=False, message="daemon did not produce a response")
        return self.response


@dataclass(frozen=True, kw_only=True)
class KeyChord:
    modifiers: tuple[ModifierName, ...]
    key: str

    MODIFIER_ORDER: ClassVar[dict[ModifierName, int]] = {
        "cmd": 0,
        "ctrl": 1,
        "alt": 2,
        "shift": 3,
    }
    MODIFIER_ALIASES: ClassVar[dict[str, ModifierName]] = {
        "cmd": "cmd",
        "cmd_l": "cmd",
        "cmd_r": "cmd",
        "command": "cmd",
        "ctrl": "ctrl",
        "ctrl_l": "ctrl",
        "ctrl_r": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "alt_l": "alt",
        "alt_r": "alt",
        "option": "alt",
        "shift": "shift",
        "shift_l": "shift",
        "shift_r": "shift",
    }
    KEY_ALIASES: ClassVar[dict[str, str]] = {
        "return": "enter",
        "escape": "esc",
        "spacebar": "space",
        "left": "left",
        "right": "right",
        "up": "up",
        "down": "down",
    }
    VK_ALIASES: ClassVar[dict[int, str]] = {
        0x00: "a",
        0x01: "s",
        0x02: "d",
        0x03: "f",
        0x04: "h",
        0x05: "g",
        0x06: "z",
        0x07: "x",
        0x08: "c",
        0x09: "v",
        0x0B: "b",
        0x0C: "q",
        0x0D: "w",
        0x0E: "e",
        0x0F: "r",
        0x10: "y",
        0x11: "t",
        0x12: "1",
        0x13: "2",
        0x14: "3",
        0x15: "4",
        0x16: "6",
        0x17: "5",
        0x20: "u",
        0x22: "i",
        0x23: "p",
        0x25: "l",
        0x26: "j",
        0x28: "k",
        0x2D: "n",
        0x2E: "m",
        0x2F: ".",
    }

    @classmethod
    def parse(cls, value: str) -> KeyChord:
        raw_tokens = value.replace("+", "-").split("-")
        tokens = [token.strip().casefold() for token in raw_tokens if token.strip()]
        if not tokens:
            msg = "key chord must not be empty"
            raise ValueError(msg)
        key = cls.normalise_key(tokens[-1])
        modifier_set: set[ModifierName] = {
            cls.normalise_modifier(token) for token in tokens[:-1]
        }
        modifiers = tuple(
            sorted(
                modifier_set,
                key=lambda item: cls.MODIFIER_ORDER[item],
            )
        )
        return cls(modifiers=modifiers, key=key)

    @classmethod
    def from_event_key(cls, key: object) -> str | None:
        name = getattr(key, "name", None)
        if isinstance(name, str):
            return cls.KEY_ALIASES.get(name, cls.MODIFIER_ALIASES.get(name, name))
        vk = getattr(key, "vk", None)
        if isinstance(vk, int):
            return cls.VK_ALIASES.get(vk, f"vk:{vk}")
        char = getattr(key, "char", None)
        if isinstance(char, str) and char:
            return cls.normalise_key(char)
        return None

    @classmethod
    def normalise_modifier(cls, value: str) -> ModifierName:
        modifier = cls.MODIFIER_ALIASES.get(value.strip().casefold())
        if modifier is None:
            msg = f"unknown modifier: {value}"
            raise ValueError(msg)
        return modifier

    @classmethod
    def modifier_from_event_key(cls, value: str) -> ModifierName | None:
        return cls.MODIFIER_ALIASES.get(value)

    @classmethod
    def normalise_key(cls, value: str) -> str:
        token = value.strip().casefold()
        if token.startswith("0x"):
            return f"vk:{int(token, 0)}"
        return cls.KEY_ALIASES.get(token, token)

    def matches(self, *, key: str, modifiers: set[ModifierName]) -> bool:
        return self.key == key and set(self.modifiers) == modifiers


@dataclass(frozen=True, kw_only=True)
class KeyBinding:
    chord: KeyChord
    request: IpcRequest


def default_keybindings() -> tuple[KeyBinding, ...]:
    return parse_keybinding_map(
        {
            "alt-h": "focus left",
            "alt-j": "focus down",
            "alt-k": "focus up",
            "alt-l": "focus right",
            "shift-alt-h": "move left",
            "shift-alt-j": "move down",
            "shift-alt-k": "move up",
            "shift-alt-l": "move right",
            "alt-1": "goto-desktop 1",
            "alt-2": "goto-desktop 2",
            "alt-3": "goto-desktop 3",
            "alt-4": "goto-desktop 4",
            "alt-5": "goto-desktop 5",
            "alt-6": "goto-desktop 6",
            "alt-7": "goto-desktop 7",
            "alt-8": "goto-desktop 8",
            "alt-9": "goto-desktop 9",
            "alt-0": "goto-desktop 10",
            "shift-alt-q": "close",
            "alt-f": "fullscreen",
            "alt-r": "retile",
            "ctrl-alt-1": "columns 1",
            "ctrl-alt-2": "columns 2",
            "ctrl-alt-3": "columns 3",
            "ctrl-alt-4": "columns 2.5",
            "ctrl-alt-5": "columns 1.7",
            "ctrl-alt-s": "status",
        }
    )


def load_keybindings(path: Path | None) -> tuple[KeyBinding, ...]:
    if path is None:
        return default_keybindings()
    raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    config = cast(JsonObject, raw)
    bindings = config.get("bindings", config)
    assert isinstance(bindings, dict)
    return parse_keybinding_map(cast(JsonObject, bindings))


def parse_keybinding_map(raw: Mapping[str, object]) -> tuple[KeyBinding, ...]:
    """Parse a simple chord-to-command JSON object.

    >>> [binding.request for binding in parse_keybinding_map({"alt-h": "focus left", "alt-f": "fullscreen"})]
    [IpcRequest(kind='focus', direction='left', desktop=None, columns=None), IpcRequest(kind='fullscreen', direction=None, desktop=None, columns=None)]
    """
    return tuple(
        KeyBinding(
            chord=KeyChord.parse(chord),
            request=parse_binding_command(str(command)),
        )
        for chord, command in raw.items()
    )


def parse_binding_command(command: str) -> IpcRequest:
    """Parse the command side of a keybinding.

    >>> parse_binding_command("move left")
    IpcRequest(kind='move', direction='left', desktop=None, columns=None)
    >>> parse_binding_command("goto-desktop 2")
    IpcRequest(kind='goto-desktop', direction=None, desktop=2, columns=None)
    >>> parse_binding_command("columns 1.7")
    IpcRequest(kind='columns', direction=None, desktop=None, columns=1.7)
    """
    parts = shlex.split(command)
    assert parts
    kind = parse_command_kind(parts[0])
    match kind:
        case "focus" | "move":
            assert len(parts) == COMMAND_WITH_ARG_LENGTH
            return IpcRequest(kind=kind, direction=parse_direction(parts[1]))
        case "goto-desktop":
            assert len(parts) == COMMAND_WITH_ARG_LENGTH
            return IpcRequest(kind=kind, desktop=parse_desktop_number(parts[1]))
        case "columns":
            assert len(parts) == COMMAND_WITH_ARG_LENGTH
            return IpcRequest(kind=kind, columns=float(parts[1]))
        case "fullscreen" | "close" | "retile" | "status" | "stop":
            assert len(parts) == COMMAND_WITHOUT_ARG_LENGTH
            return IpcRequest(kind=kind)
        case _ as unreachable:
            assert_never(unreachable)


class KeyBindingManager:
    def __init__(
        self, *, bindings: tuple[KeyBinding, ...], submit: Callable[[IpcRequest], None]
    ) -> None:
        self.bindings = bindings
        self.submit = submit
        self.lock = threading.RLock()
        self.pressed_modifiers: set[ModifierName] = set()
        self.pressed_keys: set[str] = set()
        self.suppressed_releases: set[str] = set()
        self.consume_current_event = False
        self.listener: object | None = None

    def start(self) -> None:
        keyboard = import_keyboard()
        listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=False,
            darwin_intercept=self._intercept,
        )
        listener.start()
        listener.wait()
        self.listener = listener

    def stop(self) -> None:
        if self.listener is None:
            return
        listener = cast(DynamicObjC, self.listener)
        listener.stop()
        listener.join(timeout=2)
        self.listener = None

    def _on_press(self, key: object, injected: object = None) -> None:
        with self.lock:
            self.consume_current_event = False
            if injected is True:
                return
            key_name = KeyChord.from_event_key(key)
            if key_name is None:
                return
            if modifier := KeyChord.modifier_from_event_key(key_name):
                self.pressed_modifiers.add(modifier)
                return
            repeated = key_name in self.pressed_keys
            self.pressed_keys.add(key_name)
            consume = self._handle_key_press(key_name, repeated=repeated)
            self.consume_current_event = consume
            if consume:
                self.suppressed_releases.add(key_name)

    def _on_release(self, key: object, injected: object = None) -> None:
        with self.lock:
            self.consume_current_event = False
            if injected is True:
                return
            key_name = KeyChord.from_event_key(key)
            if key_name is None:
                return
            if modifier := KeyChord.modifier_from_event_key(key_name):
                self.pressed_modifiers.discard(modifier)
                return
            self.pressed_keys.discard(key_name)
            if key_name in self.suppressed_releases:
                self.suppressed_releases.remove(key_name)
                self.consume_current_event = True

    def _intercept(self, _event_type: object, event: object) -> object | None:
        with self.lock:
            consume = self.consume_current_event
            self.consume_current_event = False
        return None if consume else event

    def _handle_key_press(self, key_name: str, *, repeated: bool) -> bool:
        if repeated:
            return False
        for binding in self.bindings:
            if binding.chord.matches(key=key_name, modifiers=self.pressed_modifiers):
                self.submit(binding.request)
                return True
        return False


@dataclass(frozen=True, kw_only=True)
class MacApi:
    appkit: DynamicObjC
    core: DynamicObjC
    hiservices: DynamicObjC
    objc: DynamicObjC
    quartz: DynamicObjC

    AX_WINDOW_NUMBER_ATTRIBUTE: ClassVar[str] = "AXWindowNumber"

    @classmethod
    def load(cls) -> MacApi:
        try:
            import AppKit  # pyright: ignore[reportMissingImports]
            import CoreFoundation  # pyright: ignore[reportMissingImports]
            import HIServices  # pyright: ignore[reportMissingImports]
            import objc  # pyright: ignore[reportMissingImports]
            import Quartz  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            msg = (
                "mwm requires PyObjC on macOS. Install the script dependencies with uv, "
                "pipx, or pip on the machine that will run the daemon."
            )
            raise RuntimeError(msg) from error
        return cls(
            appkit=cast(DynamicObjC, AppKit),
            core=cast(DynamicObjC, CoreFoundation),
            hiservices=cast(DynamicObjC, HIServices),
            objc=cast(DynamicObjC, objc),
            quartz=cast(DynamicObjC, Quartz),
        )

    def ensure_accessibility(self) -> None:
        if cast(bool, self.hiservices.AXIsProcessTrusted()):
            return
        prompt_key = self.hiservices.kAXTrustedCheckOptionPrompt
        _trusted = self.hiservices.AXIsProcessTrustedWithOptions({prompt_key: True})
        msg = "Accessibility permission is required; grant it in System Settings and start the daemon again."
        raise RuntimeError(msg)

    def ax_get(self, element: object, attribute: object) -> object | None:
        result = parse_ax_copy_value(
            self.hiservices.AXUIElementCopyAttributeValue(element, attribute, None),
        )
        if result.error != self.hiservices.kAXErrorSuccess:
            return None
        return result.value

    def ax_set(self, element: object, attribute: object, value: object) -> bool:
        error = self.hiservices.AXUIElementSetAttributeValue(element, attribute, value)
        return cast(bool, error == self.hiservices.kAXErrorSuccess)

    def ax_action(self, element: object, action: object) -> bool:
        error = self.hiservices.AXUIElementPerformAction(element, action)
        return cast(bool, error == self.hiservices.kAXErrorSuccess)

    def ax_pid(self, element: object) -> int | None:
        result = parse_ax_copy_value(self.hiservices.AXUIElementGetPid(element, None))
        if result.error != self.hiservices.kAXErrorSuccess:
            return None
        return to_int(result.value)

    def ax_bool(
        self, element: object, attribute: object, *, default: bool = False
    ) -> bool:
        value = self.ax_get(element, attribute)
        if value is None:
            return default
        return bool(value)

    def ax_point(self, value: object) -> AxPoint | None:
        result = parse_ax_value(
            self.hiservices.AXValueGetValue(
                value, self.hiservices.kAXValueTypeCGPoint, None
            ),
        )
        if not result.ok:
            return None
        return parse_ax_point(result.value)

    def ax_size(self, value: object) -> AxSize | None:
        result = parse_ax_value(
            self.hiservices.AXValueGetValue(
                value, self.hiservices.kAXValueTypeCGSize, None
            ),
        )
        if not result.ok:
            return None
        return parse_ax_size(result.value)

    def ax_frame(self, window: object) -> Rect | None:
        position_value = self.ax_get(window, self.hiservices.kAXPositionAttribute)
        size_value = self.ax_get(window, self.hiservices.kAXSizeAttribute)
        if position_value is None or size_value is None:
            return None
        position = self.ax_point(position_value)
        size = self.ax_size(size_value)
        if position is None or size is None:
            return None
        return Rect.from_ax_values(position=position, size=size)

    def set_frame(self, window: WindowInfo, frame: Rect) -> None:
        point = self.hiservices.AXValueCreate(
            self.hiservices.kAXValueTypeCGPoint, frame.as_ax_position()
        )
        size = self.hiservices.AXValueCreate(
            self.hiservices.kAXValueTypeCGSize, frame.as_ax_size()
        )
        _ = self.ax_set(window.ax, self.hiservices.kAXPositionAttribute, point)
        _ = self.ax_set(window.ax, self.hiservices.kAXSizeAttribute, size)

    def focus_window(self, window: WindowInfo) -> None:
        app = self.hiservices.AXUIElementCreateApplication(window.pid)
        _ = self.ax_set(app, self.hiservices.kAXFrontmostAttribute, value=True)
        _ = self.ax_set(window.ax, self.hiservices.kAXMainAttribute, value=True)
        _ = self.ax_set(window.ax, self.hiservices.kAXFocusedAttribute, value=True)
        _ = self.ax_action(window.ax, self.hiservices.kAXRaiseAction)

    def close_window(self, window: WindowInfo) -> bool:
        button = self.ax_get(window.ax, self.hiservices.kAXCloseButtonAttribute)
        if button is None:
            return False
        return self.ax_action(button, self.hiservices.kAXPressAction)

    def switch_desktop(self, number: int) -> bool:
        key_code = DESKTOP_KEY_CODES.get(number)
        if key_code is None:
            return False
        for is_down in (True, False):
            event = self.quartz.CGEventCreateKeyboardEvent(None, key_code, is_down)
            if event is None:
                return False
            self.quartz.CGEventSetFlags(event, self.quartz.kCGEventFlagMaskControl)
            self.quartz.CGEventPost(self.quartz.kCGHIDEventTap, event)
        return True

    def screens(self) -> tuple[ScreenInfo, ...]:
        screens = tuple(cast(Iterable[DynamicObjC], self.appkit.NSScreen.screens()))
        if not screens:
            return ()
        main_screen = self.appkit.NSScreen.mainScreen() or screens[0]
        main_screen_height = float(main_screen.frame().size.height)
        result: list[ScreenInfo] = []
        for index, screen in enumerate(screens):
            frame = Rect.from_cocoa_rect(
                cast(CocoaRect, screen.visibleFrame()),
                main_screen_height=main_screen_height,
            )
            result.append(ScreenInfo(key=f"{index}:{frame.as_key()}", frame=frame))
        return tuple(result)

    def running_pids(self) -> tuple[int, ...]:
        desktop = self.appkit.NSWorkspace.sharedWorkspace()
        pids: list[int] = []
        for app in cast(Iterable[DynamicObjC], desktop.runningApplications()):
            pid = int(app.processIdentifier())
            if pid > 0 and pid != os.getpid():
                pids.append(pid)
        return tuple(sorted(set(pids)))

    def visible_window_index(self) -> VisibleWindowIndex:
        options = (
            self.quartz.kCGWindowListOptionOnScreenOnly
            | self.quartz.kCGWindowListExcludeDesktopElements
        )
        raw_windows = cast(
            Iterable[Mapping[object, object]],
            self.quartz.CGWindowListCopyWindowInfo(
                options, self.quartz.kCGNullWindowID
            ),
        )
        numbers_by_pid: dict[int, set[int]] = {}
        order_by_pid_number: dict[tuple[int, int], int] = {}
        frames_by_pid: dict[int, list[tuple[int, Rect]]] = {}
        for order, raw in enumerate(raw_windows):
            if to_int(raw.get(self.quartz.kCGWindowLayer, 1)) != 0:
                continue
            if not bool(raw.get(self.quartz.kCGWindowIsOnscreen, False)):
                continue
            alpha = to_float(raw.get(self.quartz.kCGWindowAlpha, 1.0))
            if alpha <= 0:
                continue
            pid = to_int(raw.get(self.quartz.kCGWindowOwnerPID, 0))
            number = to_int(raw.get(self.quartz.kCGWindowNumber, 0))
            if pid > 0 and number > 0:
                numbers_by_pid.setdefault(pid, set()).add(number)
                order_by_pid_number[(pid, number)] = order
                bounds = raw.get(self.quartz.kCGWindowBounds)
                if isinstance(bounds, Mapping):
                    frame = Rect.from_quartz_bounds(
                        cast(Mapping[object, object], bounds)
                    )
                    if frame is not None:
                        frames_by_pid.setdefault(pid, []).append((order, frame))
        return VisibleWindowIndex(
            numbers_by_pid=numbers_by_pid,
            order_by_pid_number=order_by_pid_number,
            frames_by_pid={
                pid: tuple(pid_frames) for pid, pid_frames in frames_by_pid.items()
            },
        )

    def focused_window(self) -> WindowInfo | None:
        system = self.hiservices.AXUIElementCreateSystemWide()
        window = self.ax_get(system, self.hiservices.kAXFocusedWindowAttribute)
        if window is None:
            app = self.ax_get(system, self.hiservices.kAXFocusedApplicationAttribute)
            if app is None:
                return None
            window = self.ax_get(app, self.hiservices.kAXFocusedWindowAttribute)
        if window is None:
            return None
        windows = self.collect_windows()
        pid = self.ax_pid(window)
        number = self.window_number(window)
        for candidate in windows:
            if (
                number is not None
                and candidate.pid == pid
                and candidate.window_number == number
            ):
                return candidate
        frame = self.ax_frame(window)
        if pid is None or frame is None:
            return None
        return min(
            (candidate for candidate in windows if candidate.pid == pid),
            key=lambda candidate: candidate.frame.distance_to(frame),
            default=None,
        )

    def collect_windows(self) -> tuple[WindowInfo, ...]:
        screens = self.screens()
        if not screens:
            return ()
        visible_index = self.visible_window_index()
        windows: list[WindowInfo] = []
        for pid in self.running_pids():
            app = self.hiservices.AXUIElementCreateApplication(pid)
            raw_windows = self.ax_get(app, self.hiservices.kAXWindowsAttribute)
            if not raw_windows:
                continue
            for window in cast(Iterable[object], raw_windows):
                info = self.window_info(
                    window,
                    pid=pid,
                    screens=screens,
                    visible_index=visible_index,
                )
                if info is not None:
                    windows.append(info)
        return tuple(sorted(windows, key=window_sort_key))

    def window_info(
        self,
        window: object,
        *,
        pid: int,
        screens: tuple[ScreenInfo, ...],
        visible_index: VisibleWindowIndex,
    ) -> WindowInfo | None:
        if (
            self.ax_get(window, self.hiservices.kAXRoleAttribute)
            != self.hiservices.kAXWindowRole
        ):
            return None
        if (
            self.ax_get(window, self.hiservices.kAXSubroleAttribute)
            != self.hiservices.kAXStandardWindowSubrole
        ):
            return None
        if self.ax_bool(window, self.hiservices.kAXMinimizedAttribute):
            return None
        frame = self.ax_frame(window)
        if frame is None or not frame.valid_window_size:
            return None
        screen = screen_for_frame(frame, screens)
        if screen is None:
            return None
        number = self.window_number(window)
        if not visible_index.contains(pid=pid, number=number, frame=frame):
            return None
        title = str(self.ax_get(window, self.hiservices.kAXTitleAttribute) or "")
        stable_id = self.window_stable_id(window)
        order = number if number is not None else stable_id
        key = f"{pid}:{number}" if number is not None else f"{pid}:fallback:{stable_id}"
        return WindowInfo(
            key=key,
            pid=pid,
            ax=window,
            title=title,
            frame=frame,
            screen_key=screen.key,
            window_number=number,
            order=order,
        )

    def window_number(self, window: object) -> int | None:
        value = self.ax_get(window, self.AX_WINDOW_NUMBER_ATTRIBUTE)
        if value is None:
            return None
        try:
            return to_int(value)
        except (TypeError, ValueError):
            return None

    def window_stable_id(self, window: object) -> int:
        return int(self.core.CFHash(window))


@dataclass(kw_only=True)
class LayoutState:
    columns_by_screen: dict[str, list[list[str]]] = field(default_factory=dict)
    fullscreen_keys: set[str] = field(default_factory=set)
    row_weights_by_key: dict[str, float] = field(default_factory=dict)

    def keep_only(
        self, *, visible_keys: set[str], visible_screen_keys: set[str]
    ) -> None:
        self.fullscreen_keys.intersection_update(visible_keys)
        self.row_weights_by_key = {
            key: weight
            for key, weight in self.row_weights_by_key.items()
            if key in visible_keys
        }
        for screen_key in tuple(self.columns_by_screen):
            if screen_key not in visible_screen_keys:
                del self.columns_by_screen[screen_key]
                continue
            columns = [
                [key for key in column if key in visible_keys]
                for column in self.columns_by_screen[screen_key]
            ]
            self.columns_by_screen[screen_key] = [
                column for column in columns if column
            ]

    def reconcile(
        self,
        *,
        screen: ScreenInfo,
        windows: tuple[WindowInfo, ...],
        config: LayoutConfig,
    ) -> list[list[str]]:
        visible_keys = [window.key for window in windows]
        visible_key_set = set(visible_keys)
        columns = [
            [key for key in column if key in visible_key_set]
            for column in self.columns_by_screen.get(screen.key, [])
        ]
        columns = [column for column in columns if column]
        known_keys = {key for column in columns for key in column}
        for key in visible_keys:
            if key not in known_keys:
                self._append_new_key(columns=columns, key=key, config=config)
        columns = self._fit_column_count(
            columns=columns,
            target=config.target_column_count(window_count=len(visible_keys)),
        )
        self.columns_by_screen[screen.key] = columns
        return columns

    @staticmethod
    def _append_new_key(
        *, columns: list[list[str]], key: str, config: LayoutConfig
    ) -> None:
        if not columns or len(columns) < config.max_column_count:
            columns.append([key])
            return
        columns[-1].append(key)

    @staticmethod
    def _fit_column_count(*, columns: list[list[str]], target: int) -> list[list[str]]:
        if target <= 0:
            return []
        while len(columns) > target:
            extra = columns.pop()
            columns[-1].extend(extra)
        return columns

    def find(self, *, key: str) -> tuple[str, int, int] | None:
        for screen_key, columns in self.columns_by_screen.items():
            for column_index, column in enumerate(columns):
                if key in column:
                    return (screen_key, column_index, column.index(key))
        return None

    def move(self, *, key: str, direction: Direction, config: LayoutConfig) -> bool:
        found = self.find(key=key)
        if found is None:
            return False
        screen_key, column_index, row_index = found
        columns = self.columns_by_screen[screen_key]
        match direction:
            case "up":
                if row_index == 0:
                    return False
                swap_row(
                    column=columns[column_index],
                    row_index=row_index,
                    target_row=row_index - 1,
                )
            case "down":
                if row_index >= len(columns[column_index]) - 1:
                    return False
                swap_row(
                    column=columns[column_index],
                    row_index=row_index,
                    target_row=row_index + 1,
                )
            case "left":
                if column_index == 0:
                    if not can_create_column_from(
                        columns=columns, source_index=column_index, config=config
                    ):
                        return False
                    columns.insert(0, [])
                    column_index += 1
                self.row_weights_by_key.pop(key, None)
                move_between_columns(
                    columns=columns,
                    key=key,
                    source_index=column_index,
                    target_index=column_index - 1,
                )
            case "right":
                if column_index >= len(columns) - 1:
                    if not can_create_column_from(
                        columns=columns, source_index=column_index, config=config
                    ):
                        return False
                    columns.insert(column_index + 1, [])
                self.row_weights_by_key.pop(key, None)
                move_between_columns(
                    columns=columns,
                    key=key,
                    source_index=column_index,
                    target_index=column_index + 1,
                )
            case _ as unreachable:
                assert_never(unreachable)
        self.columns_by_screen[screen_key] = [column for column in columns if column]
        return True

    def capture_row_weights(self, *, windows_by_key: dict[str, WindowInfo]) -> None:
        for columns in self.columns_by_screen.values():
            for column in columns:
                if len(column) <= 1:
                    continue
                for key in column:
                    window = windows_by_key.get(key)
                    if window is not None and window.frame.height > 0:
                        self.row_weights_by_key[key] = float(window.frame.height)


class Ipc:
    CLIENT_TIMEOUT_SECONDS: ClassVar[float] = 10.0

    @staticmethod
    def default_socket_path() -> Path:
        return default_socket_path()

    @staticmethod
    def send(*, path: Path, request: IpcRequest) -> IpcResponse:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(Ipc.CLIENT_TIMEOUT_SECONDS)
                client.connect(str(path))
                client.sendall(request.to_json() + b"\n")
                chunks: list[bytes] = []
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
        except OSError as error:
            return IpcResponse(
                ok=False, message=f"cannot reach daemon at {path}: {error}"
            )
        payload = b"".join(chunks).split(b"\n", maxsplit=1)[0]
        if not payload:
            return IpcResponse(ok=False, message="daemon returned an empty response")
        try:
            return IpcResponse.from_json(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return IpcResponse(ok=False, message=f"invalid daemon response: {error}")


class WindowDaemon:
    EVENT_QUIET_SECONDS: ClassVar[float] = 0.15
    SOCKET_TIMEOUT_SECONDS: ClassVar[float] = 0.5
    IPC_RESPONSE_TIMEOUT_SECONDS: ClassVar[float] = 10.0
    TICK_SECONDS: ClassVar[float] = 0.05

    def __init__(
        self,
        *,
        config: LayoutConfig,
        api: MacApi,
        keybindings: tuple[KeyBinding, ...] | None = None,
        verbose: bool = False,
    ) -> None:
        self.config = config
        self.api = api
        self.verbose = verbose
        self.state = LayoutState()
        self.observers: dict[int, AppObserver] = {}
        self.observed_windows: set[str] = set()
        self.lock = threading.RLock()
        self.pending_ipc_calls: queue.Queue[PendingIpcCall] = queue.Queue()
        self.ax_callback = self._make_ax_callback(api=api)
        self.keybinding_manager = (
            KeyBindingManager(bindings=keybindings, submit=self.submit_keybinding)
            if keybindings is not None
            else None
        )
        self.running = False
        self.ipc_thread: threading.Thread | None = None
        self.tick_timer: object | None = None
        self.next_periodic_at: float | None = None
        self.retile_at: float | None = None
        self.capture_row_weights_at_retile = False
        self.ignore_events_until = 0.0
        self.run_loop: object | None = None

    def _make_ax_callback(self, *, api: MacApi) -> object:
        def callback(
            observer: object, element: object, notification: str, refcon: object
        ) -> None:
            self._ax_callback(observer, element, notification, refcon)

        return api.objc.callbackFor(api.hiservices.AXObserverCreate)(callback)

    def run(self) -> int:
        self.api.ensure_accessibility()
        self.running = True
        self.run_loop = self.api.core.CFRunLoopGetCurrent()
        self._install_signal_handlers()
        self._start_ipc()
        if self.keybinding_manager is not None:
            self.keybinding_manager.start()
        self.refresh_observers()
        self.retile()
        if self.config.poll_seconds != "disabled":
            self.next_periodic_at = time.monotonic() + self.config.poll_seconds
        self._install_tick_timer()
        self.api.core.CFRunLoopRun()
        self.running = False
        self._cleanup()
        return 0

    def _install_signal_handlers(self) -> None:
        def stop_from_signal(_signum: int, _frame: object) -> None:
            self.stop()

        signal.signal(signal.SIGINT, stop_from_signal)
        signal.signal(signal.SIGTERM, stop_from_signal)

    def stop(self) -> None:
        self.running = False
        run_loop = self.run_loop or self.api.core.CFRunLoopGetCurrent()
        self.api.core.CFRunLoopStop(run_loop)

    def _start_ipc(self) -> None:
        socket_path = self.config.socket_path
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            stale = Ipc.send(path=socket_path, request=IpcRequest(kind="status"))
            if stale.ok:
                msg = f"daemon already running at {socket_path}"
                raise RuntimeError(msg)
            socket_path.unlink()
        thread = threading.Thread(target=self._serve_ipc, name="mwm-ipc", daemon=True)
        thread.start()
        self.ipc_thread = thread

    def _install_tick_timer(self) -> None:
        run_loop = self.run_loop or self.api.core.CFRunLoopGetCurrent()
        self.tick_timer = self.api.core.CFRunLoopTimerCreate(
            None,
            float(self.api.core.CFAbsoluteTimeGetCurrent()) + self.TICK_SECONDS,
            self.TICK_SECONDS,
            0,
            0,
            self._tick,
            None,
        )
        self.api.core.CFRunLoopAddTimer(
            run_loop,
            self.tick_timer,
            self.api.core.kCFRunLoopCommonModes,
        )

    def _serve_ipc(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.config.socket_path))
            server.listen()
            server.settimeout(self.SOCKET_TIMEOUT_SECONDS)
            while self.running:
                try:
                    client, _address = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self.running:
                        raise
                    return
                with client:
                    payload = self._read_client_payload(client)
                    response = self._submit_ipc_call(payload)
                    client.sendall(response.to_json() + b"\n")

    def _submit_ipc_call(self, payload: bytes) -> IpcResponse:
        if not self.running:
            return IpcResponse(ok=False, message="daemon is stopping")
        call = PendingIpcCall(payload=payload, source="ipc")
        self.pending_ipc_calls.put(call)
        self._wake_run_loop()
        return call.wait(timeout=self.IPC_RESPONSE_TIMEOUT_SECONDS)

    def submit_keybinding(self, request: IpcRequest) -> None:
        if self.running:
            self.pending_ipc_calls.put(
                PendingIpcCall(payload=request.to_json(), source="keybinding")
            )
            self._wake_run_loop()

    def _wake_run_loop(self) -> None:
        if self.run_loop is not None:
            self.api.core.CFRunLoopWakeUp(self.run_loop)

    @staticmethod
    def _read_client_payload(client: socket.socket) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        return b"".join(chunks).split(b"\n", maxsplit=1)[0]

    def _handle_client_payload(self, payload: bytes, *, source: str) -> IpcResponse:
        try:
            request = IpcRequest.from_json(payload)
            message = self.handle(request)
        except Exception as error:
            if self.verbose:
                print(f"{source}: invalid command -> {error}", flush=True)
            return IpcResponse(ok=False, message=str(error))
        if self.verbose:
            print(f"{source}: {request_summary(request)} -> {message}", flush=True)
        return IpcResponse(ok=True, message=message)

    def _tick(self, _timer: object, _info: object) -> None:
        with self.lock:
            self._drain_ipc_calls()
            if not self.running:
                return
            now = time.monotonic()
            if self.next_periodic_at is not None and now >= self.next_periodic_at:
                self.refresh_observers()
                self.retile()
                poll_seconds = self.config.poll_seconds
                self.next_periodic_at = (
                    None if poll_seconds == "disabled" else now + poll_seconds
                )
                self.retile_at = None
                return
            if self.retile_at is not None and now >= self.retile_at:
                self.retile_at = None
                self.retile()

    def _drain_ipc_calls(self) -> None:
        while True:
            try:
                call = self.pending_ipc_calls.get_nowait()
            except queue.Empty:
                return
            call.respond(self._handle_client_payload(call.payload, source=call.source))

    def handle(self, request: IpcRequest) -> str:
        with self.lock:
            match request.kind:
                case "focus":
                    direction = require_direction(request)
                    return self.focus(direction=direction)
                case "move":
                    direction = require_direction(request)
                    return self.move(direction=direction)
                case "goto-desktop":
                    desktop = require_desktop(request)
                    return self.switch_desktop(number=desktop)
                case "fullscreen":
                    return self.toggle_fullscreen()
                case "close":
                    return self.close_focused_window()
                case "columns":
                    if request.columns is None:
                        msg = "columns command requires a column count"
                        raise ValueError(msg)
                    self.config = self.config.with_columns(request.columns)
                    self.retile()
                    return f"columns set to {request.columns:g}"
                case "retile":
                    self.retile()
                    return "retiled"
                case "status":
                    return self.status()
                case "stop":
                    self.stop()
                    return "stopping"
                case _ as unreachable:
                    assert_never(unreachable)

    def status(self) -> str:
        windows = self.api.collect_windows()
        poll = (
            "disabled"
            if self.config.poll_seconds == "disabled"
            else f"{self.config.poll_seconds:g}s"
        )
        return f"running: columns={self.config.columns:g}, poll={poll}, windows={len(windows)}, socket={self.config.socket_path}"

    def refresh_observers(self) -> None:
        with self.lock:
            running_pids = set(self.api.running_pids())
            for pid in tuple(self.observers):
                if pid not in running_pids:
                    del self.observers[pid]
        for pid in sorted(running_pids - set(self.observers)):
            self._observe_app(pid=pid)
            windows = self.api.collect_windows()
            self.observed_windows.intersection_update(
                {window.key for window in windows}
            )
            for window in windows:
                self._observe_window(window)

    def _observe_app(self, *, pid: int) -> None:
        app = self.api.hiservices.AXUIElementCreateApplication(pid)
        result = parse_ax_copy_value(
            self.api.hiservices.AXObserverCreate(pid, self.ax_callback, None)
        )
        if result.error != self.api.hiservices.kAXErrorSuccess:
            return
        self.observers[pid] = AppObserver(pid=pid, app=app, observer=result.value)
        self.api.core.CFRunLoopAddSource(
            self.run_loop or self.api.core.CFRunLoopGetCurrent(),
            self.api.hiservices.AXObserverGetRunLoopSource(result.value),
            self.api.core.kCFRunLoopCommonModes,
        )
        for notification in (
            self.api.hiservices.kAXWindowCreatedNotification,
            self.api.hiservices.kAXFocusedWindowChangedNotification,
            self.api.hiservices.kAXMainWindowChangedNotification,
        ):
            self._add_notification(pid=pid, element=app, notification=notification)

    def _observe_window(self, window: WindowInfo) -> None:
        observer = self.observers.get(window.pid)
        if observer is None or window.key in self.observed_windows:
            return
        for notification in (
            self.api.hiservices.kAXMovedNotification,
            self.api.hiservices.kAXResizedNotification,
            self.api.hiservices.kAXWindowMovedNotification,
            self.api.hiservices.kAXWindowResizedNotification,
            self.api.hiservices.kAXUIElementDestroyedNotification,
            self.api.hiservices.kAXWindowMiniaturizedNotification,
            self.api.hiservices.kAXWindowDeminiaturizedNotification,
        ):
            self._add_notification(
                pid=window.pid, element=window.ax, notification=notification
            )
        self.observed_windows.add(window.key)

    def _add_notification(
        self, *, pid: int, element: object, notification: object
    ) -> None:
        observer = self.observers.get(pid)
        if observer is None:
            return
        error = self.api.hiservices.AXObserverAddNotification(
            observer.observer, element, notification, None
        )
        if error in (
            self.api.hiservices.kAXErrorSuccess,
            self.api.hiservices.kAXErrorNotificationAlreadyRegistered,
        ):
            return

    def _ax_callback(
        self, _observer: object, _element: object, notification: str, _refcon: object
    ) -> None:
        if notification in cast(
            tuple[object, ...],
            (
                self.api.hiservices.kAXWindowCreatedNotification,
                self.api.hiservices.kAXFocusedWindowChangedNotification,
                self.api.hiservices.kAXMainWindowChangedNotification,
                self.api.hiservices.kAXUIElementDestroyedNotification,
                self.api.hiservices.kAXWindowMiniaturizedNotification,
                self.api.hiservices.kAXWindowDeminiaturizedNotification,
            ),
        ):
            self.refresh_observers()
        self.schedule_retile(
            capture_row_weights=notification
            in cast(
                tuple[object, ...],
                (
                    self.api.hiservices.kAXMovedNotification,
                    self.api.hiservices.kAXResizedNotification,
                    self.api.hiservices.kAXWindowMovedNotification,
                    self.api.hiservices.kAXWindowResizedNotification,
                ),
            )
        )

    def schedule_retile(self, *, capture_row_weights: bool = False) -> None:
        now = time.monotonic()
        if now < self.ignore_events_until:
            return
        self.capture_row_weights_at_retile = (
            self.capture_row_weights_at_retile or capture_row_weights
        )
        target = now + self.EVENT_QUIET_SECONDS
        self.retile_at = (
            target if self.retile_at is None else min(self.retile_at, target)
        )
        self._wake_run_loop()

    def retile(self) -> None:
        with self.lock:
            self.ignore_events_until = time.monotonic() + self.EVENT_QUIET_SECONDS
            windows = self.api.collect_windows()
            windows_by_key = {window.key: window for window in windows}
            screens = {screen.key: screen for screen in self.api.screens()}
            grouped_windows = group_windows_by_screen(windows)
            self.state.keep_only(
                visible_keys=set(windows_by_key),
                visible_screen_keys=set(screens),
            )
            if self.capture_row_weights_at_retile:
                self.state.capture_row_weights(windows_by_key=windows_by_key)
                self.capture_row_weights_at_retile = False
            for screen_key, screen_windows in grouped_windows.items():
                screen = screens.get(screen_key)
                if screen is None:
                    continue
                fullscreen_key = next(
                    (
                        key
                        for key in self.state.fullscreen_keys
                        if key in {w.key for w in screen_windows}
                    ),
                    None,
                )
                if fullscreen_key is not None:
                    self.api.set_frame(windows_by_key[fullscreen_key], screen.frame)
                    continue
                columns = self.state.reconcile(
                    screen=screen, windows=screen_windows, config=self.config
                )
                self._apply_layout(
                    screen=screen, columns=columns, windows_by_key=windows_by_key
                )
            self.ignore_events_until = time.monotonic() + self.EVENT_QUIET_SECONDS

    def _apply_layout(
        self,
        *,
        screen: ScreenInfo,
        columns: list[list[str]],
        windows_by_key: dict[str, WindowInfo],
    ) -> None:
        for key, frame in layout_targets(
            screen=screen,
            columns=columns,
            config=self.config,
            row_weights_by_key=self.state.row_weights_by_key,
        ).items():
            window = windows_by_key.get(key)
            if window is not None:
                self.api.set_frame(window, frame)

    def focus(self, *, direction: Direction) -> str:
        focused = self.api.focused_window()
        if focused is None:
            return "no focused window"
        windows = self.api.collect_windows()
        arranged = self._arranged_windows(windows=windows)
        target = select_focus_target(
            current=focused, direction=direction, arranged=arranged
        )
        if target is None:
            return "no target window"
        self.api.focus_window(target)
        return f"focused {target.title or target.key}"

    def move(self, *, direction: Direction) -> str:
        focused = self.api.focused_window()
        if focused is None:
            return "no focused window"
        changed = self.state.move(
            key=focused.key, direction=direction, config=self.config
        )
        if not changed:
            return "no move target"
        self.retile()
        fresh = self._fresh_window_for_key(focused.key)
        if fresh is not None:
            self.api.focus_window(fresh)
        return f"moved {direction}"

    def switch_desktop(self, *, number: int) -> str:
        if self.api.switch_desktop(number):
            return f"posted desktop shortcut {number}"
        return f"cannot post desktop shortcut {number}"

    def toggle_fullscreen(self) -> str:
        focused = self.api.focused_window()
        if focused is None:
            return "no focused window"
        if focused.key in self.state.fullscreen_keys:
            self.state.fullscreen_keys.remove(focused.key)
            self.retile()
            return "fullscreen off"
        same_screen_keys = {
            window.key
            for window in self.api.collect_windows()
            if window.screen_key == focused.screen_key
        }
        self.state.fullscreen_keys.difference_update(same_screen_keys)
        self.state.fullscreen_keys.add(focused.key)
        self.retile()
        fresh = self._fresh_window_for_key(focused.key)
        if fresh is not None:
            self.api.focus_window(fresh)
        return "fullscreen on"

    def close_focused_window(self) -> str:
        focused = self.api.focused_window()
        if focused is None:
            return "no focused window"
        if self.api.close_window(focused):
            return f"closed {focused.title or focused.key}"
        return "focused window cannot be closed"

    def _fresh_window_for_key(self, key: str) -> WindowInfo | None:
        return next(
            (window for window in self.api.collect_windows() if window.key == key), None
        )

    def _arranged_windows(
        self, *, windows: tuple[WindowInfo, ...]
    ) -> dict[str, list[list[WindowInfo]]]:
        windows_by_key = {window.key: window for window in windows}
        arranged: dict[str, list[list[WindowInfo]]] = {}
        for screen_key, columns in self.state.columns_by_screen.items():
            arranged[screen_key] = [
                [windows_by_key[key] for key in column if key in windows_by_key]
                for column in columns
            ]
        return arranged

    def _cleanup(self) -> None:
        if self.keybinding_manager is not None:
            self.keybinding_manager.stop()
        if self.tick_timer is not None:
            self.api.core.CFRunLoopTimerInvalidate(self.tick_timer)
        self._answer_pending_calls(
            IpcResponse(ok=False, message="daemon stopped before handling request")
        )
        if self.config.socket_path.exists():
            self.config.socket_path.unlink()

    def _answer_pending_calls(self, response: IpcResponse) -> None:
        while True:
            try:
                call = self.pending_ipc_calls.get_nowait()
            except queue.Empty:
                return
            call.respond(response)


def group_windows_by_screen(
    windows: tuple[WindowInfo, ...],
) -> dict[str, tuple[WindowInfo, ...]]:
    grouped: dict[str, list[WindowInfo]] = {}
    for window in windows:
        grouped.setdefault(window.screen_key, []).append(window)
    return {screen_key: tuple(items) for screen_key, items in grouped.items()}


def window_sort_key(window: WindowInfo) -> tuple[str, int, int, str]:
    """Return a stable collection order for initial placement and new windows.

    >>> window_sort_key(_Test.window("b", order=2)) < window_sort_key(_Test.window("a", order=1))
    False
    """
    return (window.screen_key, window.pid, window.order, window.key)


def usable_weights(
    *, weights: tuple[float, ...] | None, count: int
) -> tuple[float, ...]:
    """Return positive finite row weights or an equal split fallback.

    >>> usable_weights(weights=(3, 1), count=2)
    (3, 1)
    >>> usable_weights(weights=(0, 1), count=2)
    (1.0, 1.0)
    >>> usable_weights(weights=None, count=3)
    (1.0, 1.0, 1.0)
    """
    if (
        weights is None
        or len(weights) != count
        or any(weight <= 0 or not math.isfinite(weight) for weight in weights)
    ):
        return tuple(1.0 for _ in range(count))
    return weights


def layout_targets(
    *,
    screen: ScreenInfo,
    columns: list[list[str]],
    config: LayoutConfig,
    row_weights_by_key: dict[str, float] | None = None,
) -> dict[str, Rect]:
    """Map window keys to target frames for the simple column layout.

    >>> screen = ScreenInfo(key="s", frame=Rect(x=0, y=0, width=1000, height=500))
    >>> targets = layout_targets(
    ...     screen=screen,
    ...     columns=[["a"], ["b"], ["c", "d"]],
    ...     config=LayoutConfig(columns=2.5),
    ... )
    >>> [(key, rect.width, rect.height) for key, rect in targets.items()]
    [('a', 400, 500), ('b', 400, 500), ('c', 200, 250), ('d', 200, 250)]
    >>> weighted = layout_targets(
    ...     screen=screen,
    ...     columns=[["a", "b"]],
    ...     config=LayoutConfig(columns=1),
    ...     row_weights_by_key={"a": 3, "b": 1},
    ... )
    >>> [(key, rect.height) for key, rect in weighted.items()]
    [('a', 375), ('b', 125)]
    >>> mixed = layout_targets(
    ...     screen=screen,
    ...     columns=[["a", "b", "c"]],
    ...     config=LayoutConfig(columns=1),
    ...     row_weights_by_key={"a": 300, "c": 100},
    ... )
    >>> [(key, rect.height) for key, rect in mixed.items()]
    [('a', 250), ('b', 167), ('c', 83)]
    """
    column_frames = screen.frame.split_columns(
        count=len(columns), columns=config.columns
    )
    return {
        key: frame
        for column, column_frame in zip(columns, column_frames, strict=True)
        for key, frame in zip(
            column,
            column_frame.split_rows(
                count=len(column),
                weights=column_row_weights(
                    column=column, row_weights_by_key=row_weights_by_key or {}
                ),
            ),
            strict=True,
        )
    }


def column_row_weights(
    *, column: list[str], row_weights_by_key: dict[str, float]
) -> tuple[float, ...]:
    """Return row weights, giving new rows a neutral local weight.

    >>> column_row_weights(column=["a", "b"], row_weights_by_key={})
    (1.0, 1.0)
    >>> column_row_weights(column=["a", "b", "c"], row_weights_by_key={"a": 300, "c": 100})
    (300, 200.0, 100)
    """
    known = [
        row_weights_by_key[key]
        for key in column
        if key in row_weights_by_key
        and row_weights_by_key[key] > 0
        and math.isfinite(row_weights_by_key[key])
    ]
    fallback = sum(known) / len(known) if known else 1.0
    return tuple(row_weights_by_key.get(key, fallback) for key in column)


def screen_for_frame(frame: Rect, screens: tuple[ScreenInfo, ...]) -> ScreenInfo | None:
    if not screens:
        return None
    containing = [
        screen
        for screen in screens
        if screen.frame.contains_point(x=frame.center_x, y=frame.center_y)
    ]
    if containing:
        return containing[0]
    return max(
        screens, key=lambda screen: screen.frame.intersection_area(frame), default=None
    )


def swap_row(*, column: list[str], row_index: int, target_row: int) -> None:
    column[row_index], column[target_row] = column[target_row], column[row_index]


def can_create_column_from(
    *, columns: list[list[str]], source_index: int, config: LayoutConfig
) -> bool:
    """Return whether an edge move may split one item into a new column.

    >>> can_create_column_from(columns=[["a", "b"]], source_index=0, config=LayoutConfig(columns=2))
    True
    >>> can_create_column_from(columns=[["a"]], source_index=0, config=LayoutConfig(columns=2))
    False
    >>> can_create_column_from(columns=[["a"], ["b"]], source_index=0, config=LayoutConfig(columns=2))
    False
    """
    return len(columns) < config.max_column_count and len(columns[source_index]) > 1


def move_between_columns(
    *, columns: list[list[str]], key: str, source_index: int, target_index: int
) -> None:
    """Move horizontally by inserting into the adjacent column.

    >>> columns = [["a"], ["b"], ["c"]]
    >>> move_between_columns(columns=columns, key="b", source_index=1, target_index=0)
    >>> columns
    [['b', 'a'], [], ['c']]
    >>> columns = [["a", "b"], ["c", "d"]]
    >>> move_between_columns(columns=columns, key="b", source_index=0, target_index=1)
    >>> columns
    [['a'], ['c', 'b', 'd']]
    """
    source = columns[source_index]
    target = columns[target_index]
    old_row = source.index(key)
    _ = source.pop(old_row)
    target.insert(min(old_row, len(target)), key)


def select_focus_target(
    *,
    current: WindowInfo,
    direction: Direction,
    arranged: dict[str, list[list[WindowInfo]]],
) -> WindowInfo | None:
    columns = arranged.get(current.screen_key)
    if not columns:
        return geometric_focus_target(
            current=current, direction=direction, candidates=()
        )
    for column_index, column in enumerate(columns):
        for row_index, window in enumerate(column):
            if window.key != current.key:
                continue
            match direction:
                case "up":
                    return column[row_index - 1] if row_index > 0 else None
                case "down":
                    return (
                        column[row_index + 1] if row_index < len(column) - 1 else None
                    )
                case "left":
                    if column_index == 0:
                        return None
                    return closest_by_vertical_center(
                        current=current, candidates=columns[column_index - 1]
                    )
                case "right":
                    if column_index >= len(columns) - 1:
                        return None
                    return closest_by_vertical_center(
                        current=current, candidates=columns[column_index + 1]
                    )
                case _ as unreachable:
                    assert_never(unreachable)
    candidates = tuple(window for column in columns for window in column)
    return geometric_focus_target(
        current=current, direction=direction, candidates=candidates
    )


def closest_by_vertical_center(
    *, current: WindowInfo, candidates: list[WindowInfo]
) -> WindowInfo | None:
    return min(
        candidates,
        key=lambda window: abs(window.frame.center_y - current.frame.center_y),
        default=None,
    )


def geometric_focus_target(
    *,
    current: WindowInfo,
    direction: Direction,
    candidates: tuple[WindowInfo, ...],
) -> WindowInfo | None:
    match direction:
        case "left":
            possible = [
                window
                for window in candidates
                if window.frame.center_x < current.frame.center_x
            ]
        case "right":
            possible = [
                window
                for window in candidates
                if window.frame.center_x > current.frame.center_x
            ]
        case "up":
            possible = [
                window
                for window in candidates
                if window.frame.center_y < current.frame.center_y
            ]
        case "down":
            possible = [
                window
                for window in candidates
                if window.frame.center_y > current.frame.center_y
            ]
        case _ as unreachable:
            assert_never(unreachable)
    return min(
        possible,
        key=lambda window: current.frame.distance_to(window.frame),
        default=None,
    )


def require_direction(request: IpcRequest) -> Direction:
    if request.direction is None:
        msg = f"{request.kind} command requires a direction"
        raise ValueError(msg)
    return request.direction


def request_summary(request: IpcRequest) -> str:
    match request.kind:
        case "focus" | "move":
            return f"{request.kind} {request.direction}"
        case "goto-desktop":
            return f"{request.kind} {request.desktop}"
        case "columns":
            columns = "?" if request.columns is None else f"{request.columns:g}"
            return f"columns {columns}"
        case "fullscreen" | "close" | "retile" | "status" | "stop":
            return request.kind
        case _ as unreachable:
            assert_never(unreachable)


def require_desktop(request: IpcRequest) -> int:
    if request.desktop is None:
        msg = f"{request.kind} command requires a desktop number"
        raise ValueError(msg)
    return request.desktop


def parse_direction(value: str) -> Direction:
    match value:
        case "left" | "right" | "up" | "down":
            return cast(Direction, value)
        case _:
            msg = f"unknown direction: {value}"
            raise ValueError(msg)


def parse_desktop_number(value: object) -> int:
    number = to_int(value)
    if 1 <= number <= DESKTOP_COUNT:
        return number
    msg = f"desktop number must be between 1 and {DESKTOP_COUNT}: {number}"
    raise ValueError(msg)


def parse_command_kind(value: str) -> CommandKind:
    if value in COMMAND_KINDS:
        return cast(CommandKind, value)
    msg = f"unknown command: {value}"
    raise ValueError(msg)


def parse_cli_command(value: str) -> CliCommand:
    if value in CLI_COMMANDS:
        return cast(CliCommand, value)
    msg = f"unknown command: {value}"
    raise ValueError(msg)


@dataclass(kw_only=True)
class _MemoryApi:
    windows: tuple[WindowInfo, ...]
    screen_values: tuple[ScreenInfo, ...]
    focused_key: str
    hiservices: object | None = None
    objc: object | None = None
    frames: dict[str, Rect] = field(default_factory=dict)
    focused_history: list[str] = field(default_factory=list)
    closed_history: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.hiservices is None:
            self.hiservices = _MemoryHiservices()
        if self.objc is None:
            self.objc = _MemoryObjc()

    def collect_windows(self) -> tuple[WindowInfo, ...]:
        return self.windows

    def screens(self) -> tuple[ScreenInfo, ...]:
        return self.screen_values

    def set_frame(self, window: WindowInfo, frame: Rect) -> None:
        self.frames[window.key] = frame

    def focused_window(self) -> WindowInfo | None:
        return next(
            (window for window in self.windows if window.key == self.focused_key), None
        )

    def focus_window(self, window: WindowInfo) -> None:
        self.focused_key = window.key
        self.focused_history.append(window.key)

    def close_window(self, window: WindowInfo) -> bool:
        self.closed_history.append(window.key)
        return True


class _MemoryHiservices:
    AXObserverCreate: ClassVar[object] = object()


class _MemoryObjc:
    @staticmethod
    def callbackFor(_function: object) -> Callable[[object], object]:  # noqa: N802
        def decorate(callback: object) -> object:
            return callback

        return decorate


class _Test:
    @staticmethod
    def screen(
        key: str = "s",
        *,
        x: int = 0,
        y: int = 0,
        width: int = 1000,
        height: int = 500,
    ) -> ScreenInfo:
        return ScreenInfo(key=key, frame=Rect(x=x, y=y, width=width, height=height))

    @staticmethod
    def window(
        key: str,
        *,
        x: int = 0,
        y: int = 0,
        width: int = 100,
        height: int = 100,
        screen_key: str = "s",
        order: int = 0,
    ) -> WindowInfo:
        return WindowInfo(
            key=key,
            pid=1,
            ax=None,
            title=key,
            frame=Rect(x=x, y=y, width=width, height=height),
            screen_key=screen_key,
            window_number=order,
            order=order,
        )

    @staticmethod
    def daemon(*, columns: float = 2.5) -> tuple[WindowDaemon, _MemoryApi]:
        screen = _Test.screen(width=1000, height=500)
        api = _MemoryApi(
            windows=(
                _Test.window("a", x=0, y=0, order=0),
                _Test.window("b", x=100, y=0, order=1),
                _Test.window("c", x=200, y=0, order=2),
                _Test.window("d", x=200, y=100, order=3),
            ),
            screen_values=(screen,),
            focused_key="a",
        )
        daemon = WindowDaemon(
            config=LayoutConfig(columns=columns),
            api=cast(MacApi, api),
        )
        return (daemon, api)


__test__: dict[str, object] = {
    "_Test": _Test,
    "rect_geometry": """
>>> rect = Rect(x=10, y=20, width=30, height=40)
>>> (rect.right, rect.bottom, rect.center_x, rect.center_y)
(40, 60, 25.0, 40.0)
>>> from types import SimpleNamespace
>>> raw = SimpleNamespace(
...     origin=SimpleNamespace(x=5, y=25),
...     size=SimpleNamespace(width=100, height=200),
... )
>>> Rect.from_cocoa_rect(raw, main_screen_height=900)
Rect(x=5, y=675, width=100, height=200)
>>> parse_ax_point(SimpleNamespace(x=1.5, y=2.5))
AxPoint(x=1.5, y=2.5)
>>> parse_ax_size(SimpleNamespace(width=300, height=200))
AxSize(width=300.0, height=200.0)
>>> Rect.from_quartz_bounds({"X": 1.2, "Y": 3.8, "Width": 10, "Height": 20})
Rect(x=1, y=4, width=10, height=20)
>>> rect.contains_point(x=25, y=40)
True
>>> rect.intersection_area(Rect(x=25, y=50, width=10, height=20))
100
>>> rect.distance_to(Rect(x=40, y=20, width=30, height=40))
30.0
>>> [row.as_key() for row in Rect(x=0, y=0, width=10, height=10).split_rows(count=3)]
['0,0,10,4', '0,4,10,3', '0,7,10,3']
""",
    "visible_window_index": """
>>> index = VisibleWindowIndex(
...     numbers_by_pid={7: {11, 12}},
...     order_by_pid_number={(7, 11): 0, (7, 12): 3},
...     frames_by_pid={8: ((2, Rect(x=10, y=10, width=100, height=100)),)},
... )
>>> (index.contains(pid=7, number=11), index.contains(pid=8, number=None))
(True, True)
>>> (index.order(pid=7, number=12), index.order(pid=7, number=99))
(3, 1000000)
>>> index.order(pid=8, number=None, frame=Rect(x=15, y=15, width=80, height=80))
2
""",
    "layout_state": """
>>> screen = _Test.screen()
>>> windows = tuple(_Test.window(key, order=index) for index, key in enumerate(["a", "b", "c", "d"]))
>>> state = LayoutState()
>>> state.reconcile(screen=screen, windows=windows, config=LayoutConfig(columns=2.5))
[['a'], ['b'], ['c', 'd']]
>>> state.find(key="c")
('s', 2, 0)
>>> state.move(key="b", direction="left", config=LayoutConfig(columns=2.5))
True
>>> state.columns_by_screen["s"]
[['b', 'a'], ['c', 'd']]
>>> state.move(key="d", direction="up", config=LayoutConfig(columns=2.5))
True
>>> state.columns_by_screen["s"]
[['b', 'a'], ['d', 'c']]
>>> state.reconcile(screen=screen, windows=windows, config=LayoutConfig(columns=2.5))
[['b', 'a'], ['d', 'c']]
>>> state.keep_only(visible_keys={"b", "d"}, visible_screen_keys={"s"})
>>> state.columns_by_screen
{'s': [['b'], ['d']]}
>>> state.keep_only(visible_keys={"b", "d"}, visible_screen_keys=set())
>>> state.columns_by_screen
{}
>>> state.columns_by_screen["s"] = [["a", "b", "c"]]
>>> state.move(key="b", direction="right", config=LayoutConfig(columns=2))
True
>>> state.columns_by_screen["s"]
[['a', 'c'], ['b']]
>>> state.row_weights_by_key = {"a": 300, "b": 50, "c": 100}
>>> state.move(key="c", direction="right", config=LayoutConfig(columns=2))
True
>>> state.row_weights_by_key
{'a': 300, 'b': 50}
""",
    "layout_targets_and_screens": """
>>> screen = _Test.screen(width=1000, height=500)
>>> targets = layout_targets(
...     screen=screen,
...     columns=[["a"], ["b"], ["c", "d"]],
...     config=LayoutConfig(columns=2.5),
... )
>>> {key: value.as_key() for key, value in targets.items()}
{'a': '0,0,400,500', 'b': '400,0,400,500', 'c': '800,0,200,250', 'd': '800,250,200,250'}
>>> left = _Test.screen("left", x=0, width=500)
>>> right = _Test.screen("right", x=500, width=500)
>>> screen_for_frame(Rect(x=550, y=10, width=100, height=100), (left, right)).key
'right'
>>> [key for key in group_windows_by_screen((_Test.window("a"), _Test.window("b", screen_key="other")))]
['s', 'other']
""",
    "focus_selection": """
>>> a = _Test.window("a", x=0, y=0)
>>> b = _Test.window("b", x=100, y=0)
>>> c = _Test.window("c", x=200, y=0)
>>> d = _Test.window("d", x=200, y=100)
>>> arranged = {"s": [[a], [b], [c, d]]}
>>> select_focus_target(current=b, direction="left", arranged=arranged).key
'a'
>>> select_focus_target(current=b, direction="right", arranged=arranged).key
'c'
>>> select_focus_target(current=c, direction="down", arranged=arranged).key
'd'
>>> select_focus_target(current=a, direction="up", arranged=arranged) is None
True
>>> geometric_focus_target(current=b, direction="left", candidates=(a, c, d)).key
'a'
""",
    "ipc_and_parsing": """
>>> request = IpcRequest(kind="move", direction="left")
>>> IpcRequest.from_json(request.to_json()) == request
True
>>> IpcResponse.from_json(IpcResponse(ok=True, message="done").to_json()).message
'done'
>>> require_direction(IpcRequest(kind="focus", direction="right"))
'right'
>>> call = PendingIpcCall(payload=b"{}")
>>> call.wait(timeout=0).ok
False
>>> call.respond(IpcResponse(ok=True, message="ok"))
>>> call.wait(timeout=0).message
'ok'
>>> parse_command_kind("bogus")
Traceback (most recent call last):
...
ValueError: unknown command: bogus
>>> parse_direction("north")
Traceback (most recent call last):
...
ValueError: unknown direction: north
>>> from types import SimpleNamespace
>>> KeyChord.from_event_key(SimpleNamespace(vk=4, char="ķ"))
'h'
>>> KeyChord.parse("alt-h").matches(key="h", modifiers={"alt"})
True
>>> parse_binding_command("close")
IpcRequest(kind='close', direction=None, desktop=None, columns=None)
>>> parse_cli_args(["daemon"]).poll_seconds
30.0
>>> parse_cli_args(["daemon", "--no-poll"]).poll_seconds
'disabled'
>>> parse_cli_args(["daemon", "--poll-seconds", "5"]).poll_seconds
5.0
""",
    "daemon_with_memory_api": """
>>> daemon, api = _Test.daemon()
>>> daemon.retile()
>>> {key: rect.as_key() for key, rect in api.frames.items()}
{'a': '0,0,400,500', 'b': '400,0,400,500', 'c': '800,0,200,250', 'd': '800,250,200,250'}
>>> daemon.handle(IpcRequest(kind="focus", direction="right"))
'focused b'
>>> api.focused_key
'b'
>>> daemon.handle(IpcRequest(kind="move", direction="left"))
'moved left'
>>> daemon.state.columns_by_screen["s"]
[['b', 'a'], ['c', 'd']]
>>> daemon.handle(IpcRequest(kind="fullscreen"))
'fullscreen on'
>>> api.frames["b"].as_key()
'0,0,1000,500'
>>> daemon.handle(IpcRequest(kind="fullscreen"))
'fullscreen off'
>>> daemon.handle(IpcRequest(kind="columns", columns=1))
'columns set to 1'
>>> daemon.state.columns_by_screen["s"]
[['b', 'a', 'c', 'd']]
>>> daemon.handle(IpcRequest(kind="columns", columns=2))
'columns set to 2'
>>> daemon.handle(IpcRequest(kind="move", direction="right"))
'moved right'
>>> daemon.state.columns_by_screen["s"]
[['a', 'c', 'd'], ['b']]
>>> daemon.handle(IpcRequest(kind="close"))
'closed b'
>>> api.closed_history
['b']
""",
}


@dataclass(frozen=True, kw_only=True)
class DaemonArgs:
    columns: float
    poll_seconds: float | Literal["disabled"]
    socket_path: Path | None
    keybindings_enabled: bool
    keybindings_path: Path | None
    verbose: bool

    def main(self) -> int:
        config = LayoutConfig(
            columns=self.columns,
            poll_seconds=self.poll_seconds,
            socket_path=self.socket_path or Ipc.default_socket_path(),
        )
        keybindings = (
            load_keybindings(self.keybindings_path)
            if self.keybindings_enabled
            else None
        )
        daemon = WindowDaemon(
            config=config,
            api=MacApi.load(),
            keybindings=keybindings,
            verbose=self.verbose,
        )
        return daemon.run()


@dataclass(frozen=True, kw_only=True)
class ClientArgs:
    request: IpcRequest
    socket_path: Path | None = None
    verbose: bool = False

    def main(self) -> int:
        response = Ipc.send(
            path=self.socket_path or Ipc.default_socket_path(), request=self.request
        )
        if self.verbose:
            output = sys.stdout if response.ok else sys.stderr
            print(response.message, file=output)
        return 0 if response.ok else 1


ParsedCli = DaemonArgs | ClientArgs


def add_client_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--socket", type=Path, default=None, dest="socket_path")
    parser.add_argument("--verbose", "-v", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mwm.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daemon_parser = subparsers.add_parser("daemon")
    daemon_parser.add_argument("--columns", "-c", type=float, default=2.0)
    daemon_parser.add_argument("--poll-seconds", type=float, default=30.0)
    daemon_parser.add_argument("--no-poll", action="store_true")
    daemon_parser.add_argument("--socket", type=Path, default=None, dest="socket_path")
    daemon_parser.add_argument("--keybindings", type=Path, default=None)
    daemon_parser.add_argument("--verbose", "-v", action="store_true")
    daemon_parser.add_argument(
        "--no-keybindings", action="store_false", dest="keybindings_enabled"
    )
    daemon_parser.set_defaults(keybindings_enabled=True)

    for command in FOCUS_MOVE_COMMANDS:
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument(
            "direction", choices=("left", "right", "up", "down")
        )
        add_client_options(command_parser)

    fullscreen_parser = subparsers.add_parser("fullscreen")
    add_client_options(fullscreen_parser)

    close_parser = subparsers.add_parser("close")
    add_client_options(close_parser)

    columns_parser = subparsers.add_parser("columns")
    columns_parser.add_argument("number_of_columns", type=float)
    add_client_options(columns_parser)

    goto_desktop_parser = subparsers.add_parser("goto-desktop")
    goto_desktop_parser.add_argument("number", type=parse_desktop_number)
    add_client_options(goto_desktop_parser)

    for command in UTILITY_COMMANDS:
        command_parser = subparsers.add_parser(command)
        add_client_options(command_parser)

    return parser


def parse_cli_args(argv: list[str] | None = None) -> ParsedCli:
    parser = build_parser()
    return cli_from_namespace(parser.parse_args(argv))


def cli_from_namespace(args: argparse.Namespace) -> ParsedCli:
    command = parse_cli_command(cast(str, args.command))
    match command:
        case "daemon":
            columns = cast(float, args.columns)
            no_poll = cast(bool, args.no_poll)
            poll_seconds: float | Literal["disabled"] = (
                "disabled" if no_poll else cast(float, args.poll_seconds)
            )
            socket_path = cast(Path | None, args.socket_path)
            keybindings_enabled = cast(bool, args.keybindings_enabled)
            keybindings_path = cast(Path | None, args.keybindings)
            verbose = cast(bool, args.verbose)
            return DaemonArgs(
                columns=columns,
                poll_seconds=poll_seconds,
                socket_path=socket_path,
                keybindings_enabled=keybindings_enabled,
                keybindings_path=keybindings_path,
                verbose=verbose,
            )
        case "focus" | "move":
            direction = cast(str, args.direction)
            socket_path = cast(Path | None, args.socket_path)
            verbose = cast(bool, args.verbose)
            request = IpcRequest(
                kind=cast(CommandKind, command),
                direction=parse_direction(direction),
            )
            return ClientArgs(request=request, socket_path=socket_path, verbose=verbose)
        case "fullscreen" | "close" | "retile" | "status" | "stop":
            socket_path = cast(Path | None, args.socket_path)
            verbose = cast(bool, args.verbose)
            request = IpcRequest(kind=cast(CommandKind, command))
            return ClientArgs(request=request, socket_path=socket_path, verbose=verbose)
        case "goto-desktop":
            desktop = cast(int, args.number)
            socket_path = cast(Path | None, args.socket_path)
            verbose = cast(bool, args.verbose)
            request = IpcRequest(kind="goto-desktop", desktop=desktop)
            return ClientArgs(request=request, socket_path=socket_path, verbose=verbose)
        case "columns":
            columns = cast(float, args.number_of_columns)
            socket_path = cast(Path | None, args.socket_path)
            verbose = cast(bool, args.verbose)
            request = IpcRequest(kind="columns", columns=columns)
            return ClientArgs(request=request, socket_path=socket_path, verbose=verbose)
        case _ as unreachable:
            assert_never(unreachable)


def main(argv: list[str] | None = None) -> int:
    return parse_cli_args(argv).main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
