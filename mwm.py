#!/usr/bin/env -S uv run --script
################################################################
# Copyright (c) 2026 Witalis Domitrz <witekdomitrz@gmail.com>
# AGPL License
################################################################
#
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pynput",
#   "pyobjc-framework-ApplicationServices",
#   "pyobjc-framework-Cocoa",
#   "pyobjc-framework-Quartz",
# ]
# ///

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import plistlib
import queue
import shlex
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import FrameType
from typing import (
    ClassVar,
    Literal,
    Protocol,
    Self,
    TypeAlias,
    assert_never,
    cast,
)

import AppKit  # pyright: ignore[reportMissingTypeStubs, reportAssignmentType]
import CoreFoundation  # pyright: ignore[reportMissingTypeStubs, reportAssignmentType]
import HIServices  # pyright: ignore[reportMissingTypeStubs, reportAssignmentType]
import objc  # pyright: ignore[reportMissingTypeStubs, reportAssignmentType]
import Quartz  # pyright: ignore[reportMissingTypeStubs, reportAssignmentType]
from pynput import keyboard

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
WorkKind = Literal["periodic", "retile", "focus-check"]
SimpleCommand = Literal["fullscreen", "close", "retile", "status", "stop"]
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
    "launchd-plist",
]
NumberLike = str | bytes | int | float
KeyBindingMap: TypeAlias = Mapping[str, str]
RawIpcRequest: TypeAlias = Mapping[str, str | int | float]
PlistScalar: TypeAlias = bool | int | float | str | bytes
PlistValue: TypeAlias = PlistScalar | list["PlistValue"] | dict[str, "PlistValue"]
Plist: TypeAlias = dict[str, PlistValue]


class Launchd:
    LABEL: ClassVar[str] = "mwm"
    LOCAL_BIN_NAME: ClassVar[str] = ".local/bin/mwm.py"


class DesktopShortcut:
    KEY_CODES: ClassVar[Mapping[int, int]] = {
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


class CliCommands:
    FOCUS_MOVE: ClassVar[tuple[Literal["focus", "move"], ...]] = ("focus", "move")
    UTILITY: ClassVar[tuple[Literal["retile", "status", "stop"], ...]] = (
        "retile",
        "status",
        "stop",
    )


class DynamicObjC(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> DynamicObjC: ...
    def __getattr__(self, name: str) -> DynamicObjC: ...
    def __iter__(self) -> Iterator[object]: ...
    def __bool__(self) -> bool: ...
    def __int__(self) -> int: ...
    def __float__(self) -> float: ...
    def __or__(self, other: object) -> DynamicObjC: ...
    def __ror__(self, other: object) -> DynamicObjC: ...


AppKit: DynamicObjC
CoreFoundation: DynamicObjC
HIServices: DynamicObjC
objc: DynamicObjC
Quartz: DynamicObjC


AxElement: TypeAlias = Hashable
AxAttribute: TypeAlias = DynamicObjC | str
ObjCValue: TypeAlias = None | bool | NumberLike | Hashable | DynamicObjC
AxCallback: TypeAlias = DynamicObjC
RunLoopHandle: TypeAlias = DynamicObjC
TimerHandle: TypeAlias = DynamicObjC
KeyboardCallback: TypeAlias = Callable[[keyboard.Key | keyboard.KeyCode | None], None]


class Subparsers(Protocol):
    def add_parser(self, name: str, **kwargs: object) -> argparse.ArgumentParser: ...


class KeyboardKey(Protocol):
    name: str | None
    vk: int | None
    char: str | None


class CocoaPoint(Protocol):
    x: float
    y: float


class CocoaSize(Protocol):
    width: float
    height: float


class CocoaRect(Protocol):
    origin: CocoaPoint
    size: CocoaSize


QuartzWindowInfo: TypeAlias = Mapping[DynamicObjC, ObjCValue]


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base = (
        Path(runtime_dir)
        if runtime_dir is not None and runtime_dir != ""
        else Path(tempfile.gettempdir())
    )
    return base / f"mwm-{os.getuid()}.sock"


@dataclass(frozen=True, kw_only=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def rounded(cls, *, x: float, y: float, width: float, height: float) -> Self:
        return cls(
            x=round(x),
            y=round(y),
            width=round(width),
            height=round(height),
        )

    MIN_WIDTH: ClassVar[int] = 40
    MIN_HEIGHT: ClassVar[int] = 40

    @classmethod
    def from_ax_values(cls, *, position: CocoaPoint, size: CocoaSize) -> Rect:
        return cls.rounded(
            x=position.x,
            y=position.y,
            width=size.width,
            height=size.height,
        )

    @classmethod
    def from_cocoa_rect(
        cls,
        raw: CocoaRect,
        *,
        main_screen_height: float,
    ) -> Rect:
        x = round(raw.origin.x)
        height = round(raw.size.height)
        y = round(main_screen_height - raw.origin.y - raw.size.height)
        width = round(raw.size.width)
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
        if len(weights) == 0:
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
    ax: AxElement
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
    pids_with_visible_windows: set[int]


@dataclass(frozen=True, kw_only=True)
class LayoutConfig:
    columns: float = 2.0
    poll_seconds: float | Literal["disabled"] = "disabled"
    socket_path: Path = field(default_factory=default_socket_path)

    def __post_init__(self) -> None:
        """Validate user-controlled layout values.

        >>> LayoutConfig(columns=2.5, poll_seconds=0.25).max_column_count
        3
        >>> LayoutConfig().poll_seconds
        'disabled'
        >>> LayoutConfig(poll_seconds="disabled").poll_seconds
        'disabled'
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

    @property
    def max_column_count(self) -> int:
        return max(1, math.ceil(self.columns))


@dataclass(frozen=True, kw_only=True)
class IpcRequestJson:
    def dumps(self) -> str:
        return json.dumps(asdict(self))


@dataclass(frozen=True, kw_only=True)
class FocusRequest(IpcRequestJson):
    direction: Direction
    kind: Literal["focus"] = field(default="focus", init=False)


@dataclass(frozen=True, kw_only=True)
class MoveRequest(IpcRequestJson):
    direction: Direction
    kind: Literal["move"] = field(default="move", init=False)


@dataclass(frozen=True, kw_only=True)
class GotoDesktopRequest(IpcRequestJson):
    desktop: int
    kind: Literal["goto-desktop"] = field(default="goto-desktop", init=False)


@dataclass(frozen=True, kw_only=True)
class ColumnsRequest(IpcRequestJson):
    columns: float
    kind: Literal["columns"] = field(default="columns", init=False)


@dataclass(frozen=True, kw_only=True)
class SimpleRequest(IpcRequestJson):
    kind: SimpleCommand


IpcRequest: TypeAlias = (
    FocusRequest | MoveRequest | GotoDesktopRequest | ColumnsRequest | SimpleRequest
)


def load_ipc_request(data: str) -> IpcRequest:
    raw = cast(RawIpcRequest, json.loads(data))
    kind = cast(CommandKind, raw["kind"])
    match kind:
        case "focus":
            return FocusRequest(direction=cast(Direction, raw["direction"]))
        case "move":
            return MoveRequest(direction=cast(Direction, raw["direction"]))
        case "goto-desktop":
            return GotoDesktopRequest(desktop=int(raw["desktop"]))
        case "columns":
            return ColumnsRequest(columns=float(raw["columns"]))
        case "fullscreen" | "close" | "retile" | "status" | "stop":
            return SimpleRequest(kind=kind)
        case _:
            assert_never(kind)


@dataclass(frozen=True, kw_only=True)
class IpcResponse:
    ok: bool
    message: str

    @classmethod
    def loads(cls, payload: str) -> IpcResponse:
        return cls(**json.loads(payload))  # pyright: ignore[reportAny]

    def dumps(self) -> str:
        return json.dumps(asdict(self))


@dataclass(frozen=True, kw_only=True)
class AppObserver:
    pid: int
    app: AxElement
    observer: AxElement


@dataclass(kw_only=True)
class PendingIpcCall:
    payload: str
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


class KeyboardNames:
    MODIFIER_ORDER: ClassVar[Mapping[ModifierName, int]] = {
        "cmd": 0,
        "ctrl": 1,
        "alt": 2,
        "shift": 3,
    }
    MODIFIER_ALIASES: ClassVar[Mapping[str, ModifierName]] = {
        "cmd": "cmd",
        "cmd_l": "cmd",
        "cmd_r": "cmd",
        "ctrl": "ctrl",
        "ctrl_l": "ctrl",
        "ctrl_r": "ctrl",
        "alt": "alt",
        "alt_l": "alt",
        "alt_r": "alt",
        "shift": "shift",
        "shift_l": "shift",
        "shift_r": "shift",
    }
    VK_ALIASES: ClassVar[Mapping[int, str]] = {
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
        0x7B: "left",
        0x7C: "right",
        0x7D: "down",
        0x7E: "up",
    }


@dataclass(frozen=True, kw_only=True)
class KeyChord:
    modifiers: tuple[ModifierName, ...]
    key: str

    @classmethod
    def parse(cls, value: str) -> KeyChord:
        raw_tokens = value.replace("+", "-").split("-")
        tokens = [token.strip().casefold() for token in raw_tokens if token.strip()]
        if len(tokens) == 0:
            msg = "key chord must not be empty"
            raise ValueError(msg)
        key = cls.normalise_key(tokens[-1])
        modifier_set: set[ModifierName] = {
            cls.normalise_modifier(token) for token in tokens[:-1]
        }
        modifiers = tuple(
            sorted(
                modifier_set,
                key=lambda item: KeyboardNames.MODIFIER_ORDER[item],
            )
        )
        return cls(modifiers=modifiers, key=key)

    @classmethod
    def from_event_key(cls, key: KeyboardKey) -> str | None:
        name = getattr(key, "name", None)
        if isinstance(name, str):
            return name
        vk = getattr(key, "vk", None)
        if isinstance(vk, int):
            return KeyboardNames.VK_ALIASES.get(vk, f"vk:{vk}")
        char = getattr(key, "char", None)
        if isinstance(char, str) and char != "":
            return cls.normalise_key(char)
        return None

    @classmethod
    def normalise_modifier(cls, value: str) -> ModifierName:
        modifier = KeyboardNames.MODIFIER_ALIASES.get(value.strip().casefold())
        if modifier is not None:
            return modifier
        msg = f"unknown modifier: {value}"
        raise ValueError(msg)

    @classmethod
    def normalise_key(cls, value: str) -> str:
        token = value.strip().casefold()
        if token.startswith("0x"):
            return f"vk:{int(token, 0)}"
        return token

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
            "cmd-left": "focus left",
            "cmd-down": "focus down",
            "cmd-up": "focus up",
            "cmd-right": "focus right",
            "shift-alt-h": "move left",
            "shift-alt-j": "move down",
            "shift-alt-k": "move up",
            "shift-alt-l": "move right",
            "shift-cmd-left": "move left",
            "shift-cmd-down": "move down",
            "shift-cmd-up": "move up",
            "shift-cmd-right": "move right",
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
    return parse_keybinding_map(
        cast(KeyBindingMap, json.loads(path.expanduser().read_text(encoding="utf-8")))
    )


def parse_keybinding_map(raw: KeyBindingMap) -> tuple[KeyBinding, ...]:
    """Parse a simple chord-to-command JSON object.

    >>> [binding.request for binding in parse_keybinding_map({"alt-h": "focus left", "alt-f": "fullscreen"})]
    [FocusRequest(direction='left', kind='focus'), SimpleRequest(kind='fullscreen')]
    """
    return tuple(
        KeyBinding(
            chord=KeyChord.parse(chord),
            request=parse_binding_command(command),
        )
        for chord, command in raw.items()
    )


def parse_binding_command(command: str) -> IpcRequest:
    """Parse the command side of a keybinding.

    >>> parse_binding_command("move left")
    MoveRequest(direction='left', kind='move')
    >>> parse_binding_command("goto-desktop 2")
    GotoDesktopRequest(desktop=2, kind='goto-desktop')
    >>> parse_binding_command("columns 1.7")
    ColumnsRequest(columns=1.7, kind='columns')
    """
    match shlex.split(command):
        case ["focus", direction]:
            return FocusRequest(direction=cast(Direction, direction))
        case ["move", direction]:
            return MoveRequest(direction=cast(Direction, direction))
        case ["goto-desktop", desktop]:
            return GotoDesktopRequest(desktop=int(desktop))
        case ["columns", columns]:
            return ColumnsRequest(columns=float(columns))
        case [("fullscreen" | "close" | "retile" | "status" | "stop") as kind]:
            return SimpleRequest(kind=kind)
        case _:
            msg = f"invalid keybinding command: {command}"
            raise ValueError(msg)


class KeyBindingManager:
    def __init__(
        self, *, bindings: tuple[KeyBinding, ...], submit: Callable[[IpcRequest], None]
    ) -> None:
        self.bindings: tuple[KeyBinding, ...] = bindings
        self.submit: Callable[[IpcRequest], None] = submit
        self.lock: threading.RLock = threading.RLock()
        self.pressed_modifiers: set[ModifierName] = set()
        self.pressed_keys: set[str] = set()
        self.suppressed_releases: set[str] = set()
        self.consume_current_event: bool = False
        self.listener: keyboard.Listener | None = None

    def start(self) -> None:
        listener = keyboard.Listener(
            on_press=cast(KeyboardCallback, self._on_press),
            on_release=cast(KeyboardCallback, self._on_release),
            suppress=False,
            darwin_intercept=self._intercept,
        )
        _ = listener.start()
        _ = listener.wait()
        self.listener = listener

    def stop(self) -> None:
        if self.listener is None:
            return
        _ = self.listener.stop()
        _ = self.listener.join(timeout=2)
        self.listener = None

    def _on_press(
        self, key: KeyboardKey, injected: Literal[True] | None = None
    ) -> None:
        with self.lock:
            self.consume_current_event = False
            if injected is True:
                return
            key_name = KeyChord.from_event_key(key)
            if key_name is None:
                return
            if modifier := KeyboardNames.MODIFIER_ALIASES.get(key_name):
                self.pressed_modifiers.add(modifier)
                return
            repeated = key_name in self.pressed_keys
            self.pressed_keys.add(key_name)
            consume = self._handle_key_press(key_name, repeated=repeated)
            self.consume_current_event = consume
            if consume:
                self.suppressed_releases.add(key_name)

    def _on_release(
        self, key: KeyboardKey, injected: Literal[True] | None = None
    ) -> None:
        with self.lock:
            self.consume_current_event = False
            if injected is True:
                return
            key_name = KeyChord.from_event_key(key)
            if key_name is None:
                return
            if modifier := KeyboardNames.MODIFIER_ALIASES.get(key_name):
                self.pressed_modifiers.discard(modifier)
                return
            self.pressed_keys.discard(key_name)
            if key_name in self.suppressed_releases:
                self.suppressed_releases.remove(key_name)
                self.consume_current_event = True

    def _intercept(
        self, _event_type: DynamicObjC, event: DynamicObjC
    ) -> DynamicObjC | None:
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


class Ax:
    WINDOW_NUMBER_ATTRIBUTE: ClassVar[str] = "AXWindowNumber"


class AxNotifications:
    @staticmethod
    def app() -> tuple[AxAttribute, ...]:
        return (
            HIServices.kAXWindowCreatedNotification,
            *AxNotifications.focus_changed(),
        )

    @staticmethod
    def window() -> tuple[AxAttribute, ...]:
        return (
            HIServices.kAXMovedNotification,
            HIServices.kAXResizedNotification,
            HIServices.kAXWindowMovedNotification,
            HIServices.kAXWindowResizedNotification,
            HIServices.kAXUIElementDestroyedNotification,
            HIServices.kAXWindowMiniaturizedNotification,
            HIServices.kAXWindowDeminiaturizedNotification,
        )

    @staticmethod
    def refresh_observers() -> tuple[AxAttribute, ...]:
        return (
            HIServices.kAXWindowCreatedNotification,
            HIServices.kAXFocusedWindowChangedNotification,
            HIServices.kAXMainWindowChangedNotification,
            HIServices.kAXUIElementDestroyedNotification,
            HIServices.kAXWindowMiniaturizedNotification,
            HIServices.kAXWindowDeminiaturizedNotification,
        )

    @staticmethod
    def capture_row_weights() -> tuple[AxAttribute, ...]:
        return (
            HIServices.kAXMovedNotification,
            HIServices.kAXResizedNotification,
            HIServices.kAXWindowMovedNotification,
            HIServices.kAXWindowResizedNotification,
        )

    @staticmethod
    def focus_changed() -> tuple[AxAttribute, ...]:
        return (
            HIServices.kAXFocusedWindowChangedNotification,
            HIServices.kAXMainWindowChangedNotification,
            HIServices.kAXFocusedUIElementChangedNotification,
        )

    @staticmethod
    def window_disappeared() -> tuple[AxAttribute, ...]:
        return (
            HIServices.kAXUIElementDestroyedNotification,
            HIServices.kAXWindowMiniaturizedNotification,
        )


class MacOS:
    @staticmethod
    def ensure_accessibility() -> None:
        if bool(HIServices.AXIsProcessTrusted()):
            return
        prompt_key = HIServices.kAXTrustedCheckOptionPrompt
        _trusted = HIServices.AXIsProcessTrustedWithOptions({prompt_key: True})
        raise SystemExit(
            "Accessibility permission is required; grant it in System Settings and start the daemon again."
        )

    @staticmethod
    def ax_get(element: AxElement, attribute: AxAttribute) -> ObjCValue | None:
        error, value = cast(
            tuple[ObjCValue, ObjCValue],
            cast(
                object,
                HIServices.AXUIElementCopyAttributeValue(element, attribute, None),
            ),
        )
        if error != HIServices.kAXErrorSuccess:
            return None
        return value

    @staticmethod
    def ax_set(element: AxElement, attribute: AxAttribute, value: ObjCValue) -> bool:
        error = HIServices.AXUIElementSetAttributeValue(element, attribute, value)
        return error == HIServices.kAXErrorSuccess

    @staticmethod
    def ax_action(element: AxElement, action: AxAttribute) -> bool:
        error = HIServices.AXUIElementPerformAction(element, action)
        return error == HIServices.kAXErrorSuccess

    @staticmethod
    def ax_pid(element: AxElement) -> int | None:
        error, pid = cast(
            tuple[ObjCValue, NumberLike],
            cast(object, HIServices.AXUIElementGetPid(element, None)),
        )
        if error != HIServices.kAXErrorSuccess:
            return None
        return int(pid)

    @staticmethod
    def ax_bool(
        element: AxElement, attribute: AxAttribute, *, default: bool = False
    ) -> bool:
        value = MacOS.ax_get(element, attribute)
        if value is None:
            return default
        return bool(value)

    @staticmethod
    def ax_string(element: AxElement, attribute: AxAttribute) -> str:
        value = MacOS.ax_get(element, attribute)
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def ax_point(value: ObjCValue) -> CocoaPoint | None:
        ok, point = cast(
            tuple[bool, CocoaPoint],
            cast(
                object,
                HIServices.AXValueGetValue(value, HIServices.kAXValueTypeCGPoint, None),
            ),
        )
        if not ok:
            return None
        return point

    @staticmethod
    def ax_size(value: ObjCValue) -> CocoaSize | None:
        ok, size = cast(
            tuple[bool, CocoaSize],
            cast(
                object,
                HIServices.AXValueGetValue(value, HIServices.kAXValueTypeCGSize, None),
            ),
        )
        if not ok:
            return None
        return size

    @staticmethod
    def ax_frame(window: AxElement) -> Rect | None:
        position_value = MacOS.ax_get(window, HIServices.kAXPositionAttribute)
        size_value = MacOS.ax_get(window, HIServices.kAXSizeAttribute)
        if position_value is None or size_value is None:
            return None
        position = MacOS.ax_point(position_value)
        size = MacOS.ax_size(size_value)
        if position is None or size is None:
            return None
        return Rect.from_ax_values(position=position, size=size)

    @staticmethod
    def set_frame(window: WindowInfo, frame: Rect) -> None:
        point = HIServices.AXValueCreate(
            HIServices.kAXValueTypeCGPoint, frame.as_ax_position()
        )
        size = HIServices.AXValueCreate(
            HIServices.kAXValueTypeCGSize, frame.as_ax_size()
        )
        _ = MacOS.ax_set(window.ax, HIServices.kAXPositionAttribute, point)
        _ = MacOS.ax_set(window.ax, HIServices.kAXSizeAttribute, size)

    @staticmethod
    def focus_window(window: WindowInfo) -> None:
        app = cast(AxElement, HIServices.AXUIElementCreateApplication(window.pid))
        _ = MacOS.ax_set(app, HIServices.kAXFrontmostAttribute, value=True)
        _ = MacOS.ax_set(window.ax, HIServices.kAXMainAttribute, value=True)
        _ = MacOS.ax_set(window.ax, HIServices.kAXFocusedAttribute, value=True)
        _ = MacOS.ax_action(window.ax, HIServices.kAXRaiseAction)

    @staticmethod
    def close_window(window: WindowInfo) -> bool:
        button = MacOS.ax_get(window.ax, HIServices.kAXCloseButtonAttribute)
        if button is None:
            return False
        return MacOS.ax_action(button, HIServices.kAXPressAction)

    @staticmethod
    def switch_desktop(number: int) -> bool:
        key_code = DesktopShortcut.KEY_CODES.get(number)
        if key_code is None:
            return False
        for is_down in (True, False):
            event = cast(
                DynamicObjC | None,
                Quartz.CGEventCreateKeyboardEvent(None, key_code, is_down),
            )
            if event is None:
                return False
            _ = Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskControl)
            _ = Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return True

    @staticmethod
    def screens() -> tuple[ScreenInfo, ...]:
        screens = tuple(cast(Iterable[DynamicObjC], AppKit.NSScreen.screens()))
        if len(screens) == 0:
            return ()
        main_screen = AppKit.NSScreen.mainScreen() or screens[0]
        main_screen_height = float(main_screen.frame().size.height)
        result: list[ScreenInfo] = []
        for index, screen in enumerate(screens):
            frame = Rect.from_cocoa_rect(
                cast(CocoaRect, cast(object, screen.visibleFrame())),
                main_screen_height=main_screen_height,
            )
            result.append(ScreenInfo(key=f"{index}:{frame.as_key()}", frame=frame))
        return tuple(result)

    @staticmethod
    def running_pids() -> tuple[int, ...]:
        desktop = AppKit.NSWorkspace.sharedWorkspace()
        pids: list[int] = []
        for app in cast(Iterable[DynamicObjC], desktop.runningApplications()):
            pid = int(app.processIdentifier())
            if pid > 0 and pid != os.getpid():
                pids.append(pid)
        return tuple(sorted(set(pids)))

    @staticmethod
    def frontmost_pid() -> int | None:
        raw_app = cast(
            DynamicObjC | None,
            AppKit.NSWorkspace.sharedWorkspace().frontmostApplication(),
        )
        if raw_app is None:
            return None
        pid = int(raw_app.processIdentifier())
        if pid <= 0 or pid == os.getpid():
            return None
        return pid

    @staticmethod
    def visible_window_index() -> VisibleWindowIndex:
        options = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        raw_windows = cast(
            Iterable[QuartzWindowInfo],
            Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID),
        )
        numbers_by_pid: dict[int, set[int]] = {}
        pids_with_visible_windows: set[int] = set()
        for raw in raw_windows:
            if int(cast(NumberLike, raw.get(Quartz.kCGWindowLayer, 1))) != 0:
                continue
            if not bool(raw.get(Quartz.kCGWindowIsOnscreen, False)):
                continue
            alpha = float(cast(NumberLike, raw.get(Quartz.kCGWindowAlpha, 1.0)))
            if alpha <= 0:
                continue
            pid = int(cast(NumberLike, raw.get(Quartz.kCGWindowOwnerPID, 0)))
            number = int(cast(NumberLike, raw.get(Quartz.kCGWindowNumber, 0)))
            if pid > 0 and number > 0:
                numbers_by_pid.setdefault(pid, set()).add(number)
                pids_with_visible_windows.add(pid)
        return VisibleWindowIndex(
            numbers_by_pid=numbers_by_pid,
            pids_with_visible_windows=pids_with_visible_windows,
        )

    @staticmethod
    def focused_window() -> WindowInfo | None:
        system = cast(AxElement, HIServices.AXUIElementCreateSystemWide())
        app = MacOS.ax_get(system, HIServices.kAXFocusedApplicationAttribute)
        front_pid = MacOS.frontmost_pid()
        if app is None and front_pid is not None:
            app = HIServices.AXUIElementCreateApplication(front_pid)
        focused_app = cast(AxElement | None, app)
        app_pid = MacOS.ax_pid(focused_app) if focused_app is not None else None
        if app_pid is None:
            app_pid = front_pid
        windows = MacOS.collect_windows()
        candidate_windows = MacOS.focused_window_candidates(
            system=system, focused_app=focused_app
        )
        for window in candidate_windows:
            info = MacOS._focused_window_info(window, app_pid=app_pid, windows=windows)
            if info is not None:
                return info
        if app_pid is None:
            return None
        return min(
            (candidate for candidate in windows if candidate.pid == app_pid),
            key=lambda candidate: candidate.order,
            default=None,
        )

    @staticmethod
    def focused_window_candidates(
        *, system: AxElement, focused_app: AxElement | None
    ) -> tuple[AxElement, ...]:
        candidate_windows: list[AxElement] = []
        system_focused_window = MacOS.ax_get(
            system, HIServices.kAXFocusedWindowAttribute
        )
        if system_focused_window is not None:
            candidate_windows.append(system_focused_window)
        if focused_app is not None:
            focused_window = MacOS.ax_get(
                focused_app, HIServices.kAXFocusedWindowAttribute
            )
            if focused_window is not None:
                candidate_windows.append(focused_window)
            main_window = MacOS.ax_get(focused_app, HIServices.kAXMainWindowAttribute)
            if main_window is not None:
                candidate_windows.append(main_window)
            raw_windows = MacOS.ax_get(focused_app, HIServices.kAXWindowsAttribute)
            if raw_windows is not None:
                candidate_windows.extend(cast(Iterable[AxElement], raw_windows))
        return tuple(candidate_windows)

    @staticmethod
    def _focused_window_info(
        window: AxElement,
        *,
        app_pid: int | None,
        windows: tuple[WindowInfo, ...],
    ) -> WindowInfo | None:
        pid = MacOS.ax_pid(window)
        if pid is None:
            pid = app_pid
        number = MacOS.window_number(window)
        for candidate in windows:
            if (
                number is not None
                and candidate.pid == pid
                and candidate.window_number == number
            ):
                return candidate
        frame = MacOS.ax_frame(window)
        if pid is None or frame is None:
            return None
        nearest = min(
            (candidate for candidate in windows if candidate.pid == pid),
            key=lambda candidate: candidate.frame.distance_to(frame),
            default=None,
        )
        if nearest is not None:
            return nearest
        if not frame.valid_window_size:
            return None
        screen = screen_for_frame(frame, MacOS.screens())
        if screen is None:
            return None
        title = MacOS.ax_string(window, HIServices.kAXTitleAttribute)
        return MacOS._window_info_from_values(
            window=window,
            pid=pid,
            title=title,
            frame=frame,
            screen=screen,
            number=number,
        )

    @staticmethod
    def collect_windows() -> tuple[WindowInfo, ...]:
        screen_infos = MacOS.screens()
        if len(screen_infos) == 0:
            return ()
        visible_index = MacOS.visible_window_index()
        windows: list[WindowInfo] = []
        for pid in MacOS.running_pids():
            app = cast(AxElement, HIServices.AXUIElementCreateApplication(pid))
            raw_windows = MacOS.ax_get(app, HIServices.kAXWindowsAttribute)
            if raw_windows is None:
                continue
            for window in cast(Iterable[AxElement], raw_windows):
                info = MacOS.window_info(
                    window,
                    pid=pid,
                    screens=screen_infos,
                    visible_index=visible_index,
                )
                if info is not None:
                    windows.append(info)
        return tuple(sorted(windows, key=window_sort_key))

    @staticmethod
    def window_info(
        window: AxElement,
        *,
        pid: int,
        screens: tuple[ScreenInfo, ...],
        visible_index: VisibleWindowIndex,
    ) -> WindowInfo | None:
        if (
            MacOS.ax_get(window, HIServices.kAXRoleAttribute)
            != HIServices.kAXWindowRole
        ):
            return None
        if (
            MacOS.ax_get(window, HIServices.kAXSubroleAttribute)
            != HIServices.kAXStandardWindowSubrole
        ):
            return None
        if MacOS.ax_bool(window, HIServices.kAXMinimizedAttribute):
            return None
        frame = MacOS.ax_frame(window)
        if frame is None or not frame.valid_window_size:
            return None
        screen = screen_for_frame(frame, screens)
        if screen is None:
            return None
        number = MacOS.window_number(window)
        if number is None:
            if pid not in visible_index.pids_with_visible_windows:
                return None
        elif number not in visible_index.numbers_by_pid.get(pid, set()):
            return None
        title = MacOS.ax_string(window, HIServices.kAXTitleAttribute)
        return MacOS._window_info_from_values(
            window=window,
            pid=pid,
            title=title,
            frame=frame,
            screen=screen,
            number=number,
        )

    @staticmethod
    def _window_info_from_values(
        *,
        window: AxElement,
        pid: int,
        title: str,
        frame: Rect,
        screen: ScreenInfo,
        number: int | None,
    ) -> WindowInfo:
        if number is None:
            stable_id = MacOS.window_stable_id(window)
            key = f"{pid}:fallback:{stable_id}"
            order = stable_id
        else:
            key = f"{pid}:{number}"
            order = number
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

    @staticmethod
    def window_number(window: AxElement) -> int | None:
        value = MacOS.ax_get(window, Ax.WINDOW_NUMBER_ATTRIBUTE)
        if value is None:
            return None
        try:
            return int(cast(NumberLike, value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def window_stable_id(window: AxElement) -> int:
        return int(CoreFoundation.CFHash(window))


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
            if key in known_keys:
                continue
            if len(columns) == 0 or len(columns) < config.max_column_count:
                columns.append([key])
            else:
                columns[-1].append(key)
        target_columns = min(len(visible_keys), config.max_column_count)
        while len(columns) > target_columns:
            extra = columns.pop()
            columns[-1].extend(extra)
        self.columns_by_screen[screen.key] = columns
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
                _ = self.row_weights_by_key.pop(key, None)
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
                _ = self.row_weights_by_key.pop(key, None)
                move_between_columns(
                    columns=columns,
                    key=key,
                    source_index=column_index,
                    target_index=column_index + 1,
                )
            case _:
                assert_never(direction)
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
    def send(*, path: Path, request: IpcRequest) -> IpcResponse:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(Ipc.CLIENT_TIMEOUT_SECONDS)
                client.connect(str(path))
                client.sendall((request.dumps() + "\n").encode())
                chunks: list[bytes] = []
                while True:
                    chunk = client.recv(4096)
                    if len(chunk) == 0:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
        except OSError as error:
            return IpcResponse(
                ok=False, message=f"cannot reach daemon at {path}: {error}"
            )
        payload = b"".join(chunks).split(b"\n", maxsplit=1)[0]
        if len(payload) == 0:
            return IpcResponse(ok=False, message="daemon returned an empty response")
        try:
            return IpcResponse.loads(payload.decode())
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return IpcResponse(ok=False, message=f"invalid daemon response: {error}")


class WindowDaemon:
    EVENT_QUIET_SECONDS: ClassVar[float] = 0.15
    SOCKET_TIMEOUT_SECONDS: ClassVar[float] = 0.5
    IPC_RESPONSE_TIMEOUT_SECONDS: ClassVar[float] = 10.0

    def __init__(
        self,
        *,
        config: LayoutConfig,
        keybindings: tuple[KeyBinding, ...] | None = None,
        verbose: bool = False,
    ) -> None:
        self.config: LayoutConfig = config
        self.verbose: bool = verbose
        self.state: LayoutState = LayoutState()
        self.observers: dict[int, AppObserver] = {}
        self.observed_windows: set[str] = set()
        self.lock: threading.RLock = threading.RLock()
        self.pending_ipc_calls: queue.Queue[PendingIpcCall] = queue.Queue()
        self.ax_callback: AxCallback = self._make_ax_callback()
        self.keybinding_manager: KeyBindingManager | None = (
            KeyBindingManager(bindings=keybindings, submit=self.submit_keybinding)
            if keybindings is not None
            else None
        )
        self.running: bool = False
        self.ipc_thread: threading.Thread | None = None
        self.work_timer: TimerHandle | None = None
        self.work_deadlines: dict[WorkKind, float] = {}
        self.capture_row_weights_at_retile: bool = False
        self.ignore_events_until: float = 0.0
        self.run_loop: RunLoopHandle | None = None
        self.last_focused_window: WindowInfo | None = None

    def _make_ax_callback(self) -> AxCallback:
        def callback(
            observer: AxElement,
            element: AxElement,
            notification: str,
            refcon: ObjCValue,
        ) -> None:
            self._ax_callback(observer, element, notification, refcon)

        return objc.callbackFor(HIServices.AXObserverCreate)(callback)

    def run(self) -> int:
        MacOS.ensure_accessibility()
        self.running = True
        self.run_loop = CoreFoundation.CFRunLoopGetCurrent()
        self._install_signal_handlers()
        self._start_ipc()
        if self.keybinding_manager is not None:
            self.keybinding_manager.start()
        self.refresh_observers()
        self.retile()
        self._remember_focused_window()
        with self.lock:
            if self.config.poll_seconds != "disabled":
                self.work_deadlines["periodic"] = (
                    time.monotonic() + self.config.poll_seconds
                )
            self._reschedule_work_timer_locked()
        _ = CoreFoundation.CFRunLoopRun()
        self.running = False
        self._cleanup()
        return 0

    def _install_signal_handlers(self) -> None:
        def stop_from_signal(_signum: int, _frame: FrameType | None) -> None:
            self.stop()

        _ = signal.signal(signal.SIGINT, stop_from_signal)
        _ = signal.signal(signal.SIGTERM, stop_from_signal)

    def stop(self) -> None:
        self.running = False
        run_loop = self.run_loop or CoreFoundation.CFRunLoopGetCurrent()
        _ = CoreFoundation.CFRunLoopStop(run_loop)

    def _start_ipc(self) -> None:
        socket_path = self.config.socket_path
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            stale = Ipc.send(path=socket_path, request=SimpleRequest(kind="status"))
            if stale.ok:
                raise SystemExit(f"daemon already running at {socket_path}")
            socket_path.unlink()
        thread = threading.Thread(target=self._serve_ipc, name="mwm-ipc", daemon=True)
        thread.start()
        self.ipc_thread = thread

    def _reschedule_work_timer_locked(self) -> None:
        if self.work_timer is not None:
            _ = CoreFoundation.CFRunLoopTimerInvalidate(self.work_timer)
            self.work_timer = None
        if self.run_loop is None or not self.running:
            return
        now = time.monotonic()
        deadline = (
            now
            if not self.pending_ipc_calls.empty()
            else min(self.work_deadlines.values(), default=None)
        )
        if deadline is None:
            return
        self.work_timer = CoreFoundation.CFRunLoopTimerCreate(
            None,
            float(CoreFoundation.CFAbsoluteTimeGetCurrent())
            + max(0.0, deadline - now),
            0.0,
            0,
            0,
            self._run_scheduled_work,
            None,
        )
        _ = CoreFoundation.CFRunLoopAddTimer(
            self.run_loop,
            self.work_timer,
            CoreFoundation.kCFRunLoopCommonModes,
        )

    def _serve_ipc(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.config.socket_path))
            server.listen()
            server.settimeout(self.SOCKET_TIMEOUT_SECONDS)
            while self.running:
                try:
                    client = server.accept()[0]
                except TimeoutError:
                    continue
                except OSError:
                    if self.running:
                        raise
                    return
                with client:
                    payload = self._read_client_payload(client)
                    response = self._submit_ipc_call(payload)
                    client.sendall((response.dumps() + "\n").encode())

    def _submit_ipc_call(self, payload: str) -> IpcResponse:
        if not self.running:
            return IpcResponse(ok=False, message="daemon is stopping")
        call = PendingIpcCall(payload=payload, source="ipc")
        self.pending_ipc_calls.put(call)
        self.schedule_queued_work()
        return call.wait(timeout=self.IPC_RESPONSE_TIMEOUT_SECONDS)

    def submit_keybinding(self, request: IpcRequest) -> None:
        if self.running:
            self.pending_ipc_calls.put(
                PendingIpcCall(payload=request.dumps(), source="keybinding")
            )
            self.schedule_queued_work()

    def _wake_run_loop(self) -> None:
        if self.run_loop is not None:
            _ = CoreFoundation.CFRunLoopWakeUp(self.run_loop)

    def schedule_queued_work(self) -> None:
        with self.lock:
            self._reschedule_work_timer_locked()
        self._wake_run_loop()

    def _schedule_deadline(
        self, kind: WorkKind, deadline: float, *, latest: bool = False
    ) -> None:
        with self.lock:
            current = self.work_deadlines.get(kind)
            if current is not None:
                deadline = max(current, deadline) if latest else min(current, deadline)
            self.work_deadlines[kind] = deadline
            self._reschedule_work_timer_locked()
        self._wake_run_loop()

    @staticmethod
    def _read_client_payload(client: socket.socket) -> str:
        chunks: list[str] = []
        while True:
            chunk = client.recv(4096).decode()
            if len(chunk) == 0:
                break
            chunks.append(chunk)
            if "\n" in chunk:
                break
        return "".join(chunks).strip()

    def _handle_client_payload(self, payload: str, *, source: str) -> IpcResponse:
        try:
            request = load_ipc_request(payload)
            message = self.handle(request)
        except (AssertionError, KeyError, TypeError, ValueError) as error:
            if self.verbose:
                print(f"{source}: invalid command -> {error}", flush=True)
            return IpcResponse(ok=False, message=str(error))
        if self.verbose:
            print(f"{source}: {request_summary(request)} -> {message}", flush=True)
        return IpcResponse(ok=True, message=message)

    def _run_scheduled_work(self, timer: TimerHandle, _info: ObjCValue) -> None:
        with self.lock:
            if self.work_timer is timer:
                self.work_timer = None
            self._drain_ipc_calls()
            if not self.running:
                return
            now = time.monotonic()
            if self._pop_due_work("periodic", now=now):
                self.refresh_observers()
                self.retile()
                poll_seconds = self.config.poll_seconds
                if poll_seconds != "disabled":
                    self.work_deadlines["periodic"] = now + poll_seconds
                _ = self.work_deadlines.pop("retile", None)
            if self._pop_due_work("retile", now=now):
                self.retile()
            if self._pop_due_work("focus-check", now=now):
                self._sync_or_repair_focus()
            self._reschedule_work_timer_locked()

    def _pop_due_work(self, kind: WorkKind, *, now: float) -> bool:
        deadline = self.work_deadlines.get(kind)
        if deadline is None or now < deadline:
            return False
        del self.work_deadlines[kind]
        return True

    def _drain_ipc_calls(self) -> None:
        while True:
            try:
                call = self.pending_ipc_calls.get_nowait()
            except queue.Empty:
                return
            call.respond(self._handle_client_payload(call.payload, source=call.source))

    def handle(self, request: IpcRequest) -> str:
        with self.lock:
            match request:
                case FocusRequest(direction=direction):
                    return self.focus(direction=direction)
                case MoveRequest(direction=direction):
                    return self.move(direction=direction)
                case GotoDesktopRequest(desktop=desktop):
                    return self.switch_desktop(number=desktop)
                case ColumnsRequest(columns=columns):
                    self.config = LayoutConfig(
                        columns=columns,
                        poll_seconds=self.config.poll_seconds,
                        socket_path=self.config.socket_path,
                    )
                    self.retile()
                    return f"columns set to {columns:g}"
                case SimpleRequest(kind=kind):
                    match kind:
                        case "fullscreen":
                            return self.toggle_fullscreen()
                        case "close":
                            return self.close_focused_window()
                        case "retile":
                            self.retile()
                            return "retiled"
                        case "status":
                            return self.status()
                        case "stop":
                            self.stop()
                            return "stopping"
                        case _:
                            assert_never(kind)
                case _:
                    assert_never(request)

    def status(self) -> str:
        windows = MacOS.collect_windows()
        poll = (
            "disabled"
            if self.config.poll_seconds == "disabled"
            else f"{self.config.poll_seconds:g}s"
        )
        return f"running: columns={self.config.columns:g}, poll={poll}, windows={len(windows)}, socket={self.config.socket_path}"

    def refresh_observers(self) -> None:
        with self.lock:
            active_pids = set(MacOS.running_pids())
            for pid in tuple(self.observers):
                if pid not in active_pids:
                    del self.observers[pid]
        for pid in sorted(active_pids - set(self.observers)):
            self._observe_app(pid=pid)
            windows = MacOS.collect_windows()
            self.observed_windows.intersection_update(
                {window.key for window in windows}
            )
            for window in windows:
                self._observe_window(window)

    def _observe_app(self, *, pid: int) -> None:
        app = cast(AxElement, HIServices.AXUIElementCreateApplication(pid))
        error, observer = cast(
            tuple[ObjCValue, AxElement],
            cast(
                object,
                HIServices.AXObserverCreate(pid, self.ax_callback, None),
            ),
        )
        if error != HIServices.kAXErrorSuccess:
            return
        self.observers[pid] = AppObserver(pid=pid, app=app, observer=observer)
        _ = CoreFoundation.CFRunLoopAddSource(
            self.run_loop or CoreFoundation.CFRunLoopGetCurrent(),
            HIServices.AXObserverGetRunLoopSource(observer),
            CoreFoundation.kCFRunLoopCommonModes,
        )
        for notification in AxNotifications.app():
            self._add_notification(pid=pid, element=app, notification=notification)

    def _observe_window(self, window: WindowInfo) -> None:
        observer = self.observers.get(window.pid)
        if observer is None or window.key in self.observed_windows:
            return
        for notification in AxNotifications.window():
            self._add_notification(
                pid=window.pid, element=window.ax, notification=notification
            )
        self.observed_windows.add(window.key)

    def _add_notification(
        self, *, pid: int, element: AxElement, notification: AxAttribute
    ) -> None:
        observer = self.observers.get(pid)
        if observer is None:
            return
        error = HIServices.AXObserverAddNotification(
            observer.observer, element, notification, None
        )
        if error in (
            HIServices.kAXErrorSuccess,
            HIServices.kAXErrorNotificationAlreadyRegistered,
        ):
            return

    def _ax_callback(
        self,
        _observer: AxElement,
        _element: AxElement,
        notification: str,
        _refcon: ObjCValue,
    ) -> None:
        if notification in AxNotifications.refresh_observers():
            self.refresh_observers()
        if notification in AxNotifications.focus_changed():
            self._remember_focused_window()
            self.schedule_focus_check()
        elif notification in AxNotifications.window_disappeared():
            self.schedule_focus_check()
        self.schedule_retile(
            capture_row_weights=notification in AxNotifications.capture_row_weights()
        )

    def schedule_retile(self, *, capture_row_weights: bool = False) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.ignore_events_until:
                return
            self.capture_row_weights_at_retile = (
                self.capture_row_weights_at_retile or capture_row_weights
            )
        self._schedule_deadline("retile", now + self.EVENT_QUIET_SECONDS)

    def schedule_focus_check(self) -> None:
        self._schedule_deadline(
            "focus-check",
            time.monotonic() + self.EVENT_QUIET_SECONDS,
            latest=True,
        )

    def retile(self) -> None:
        with self.lock:
            self.ignore_events_until = time.monotonic() + self.EVENT_QUIET_SECONDS
            windows = MacOS.collect_windows()
            windows_by_key = {window.key: window for window in windows}
            screen_infos = {screen.key: screen for screen in MacOS.screens()}
            grouped_windows = group_windows_by_screen(windows)
            self.state.keep_only(
                visible_keys=set(windows_by_key),
                visible_screen_keys=set(screen_infos),
            )
            if self.capture_row_weights_at_retile:
                self.state.capture_row_weights(windows_by_key=windows_by_key)
                self.capture_row_weights_at_retile = False
            for screen_key, screen_windows in grouped_windows.items():
                screen = screen_infos.get(screen_key)
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
                    MacOS.set_frame(windows_by_key[fullscreen_key], screen.frame)
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
                MacOS.set_frame(window, frame)

    def focus(self, *, direction: Direction) -> str:
        focused = MacOS.focused_window()
        if focused is None:
            return "no focused window"
        windows = MacOS.collect_windows()
        arranged = self._arranged_windows(windows=windows)
        target = select_focus_target(
            current=focused,
            direction=direction,
            arranged=arranged,
            fallback_candidates=windows,
        )
        if target is None:
            return "no target window"
        MacOS.focus_window(target)
        return f"focused {target.title or target.key}"

    def move(self, *, direction: Direction) -> str:
        focused = MacOS.focused_window()
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
            MacOS.focus_window(fresh)
        return f"moved {direction}"

    def switch_desktop(self, *, number: int) -> str:
        if MacOS.switch_desktop(number):
            return f"posted desktop shortcut {number}"
        return f"cannot post desktop shortcut {number}"

    def toggle_fullscreen(self) -> str:
        focused = MacOS.focused_window()
        if focused is None:
            return "no focused window"
        if focused.key in self.state.fullscreen_keys:
            self.state.fullscreen_keys.remove(focused.key)
            self.retile()
            return "fullscreen off"
        same_screen_keys = {
            window.key
            for window in MacOS.collect_windows()
            if window.screen_key == focused.screen_key
        }
        self.state.fullscreen_keys.difference_update(same_screen_keys)
        self.state.fullscreen_keys.add(focused.key)
        self.retile()
        fresh = self._fresh_window_for_key(focused.key)
        if fresh is not None:
            MacOS.focus_window(fresh)
        return "fullscreen on"

    def close_focused_window(self) -> str:
        focused = MacOS.focused_window()
        if focused is None:
            return "no focused window"
        if MacOS.close_window(focused):
            return f"closed {focused.title or focused.key}"
        return "focused window cannot be closed"

    def _fresh_window_for_key(self, key: str) -> WindowInfo | None:
        return next(
            (window for window in MacOS.collect_windows() if window.key == key),
            None,
        )

    def _remember_focused_window(self) -> None:
        focused = MacOS.focused_window()
        if focused is None:
            return
        self.last_focused_window = focused

    def _sync_or_repair_focus(self) -> None:
        windows = MacOS.collect_windows()
        visible_keys = {window.key for window in windows}
        focused = MacOS.focused_window()
        if focused is not None and focused.key in visible_keys:
            self.last_focused_window = focused
            return
        closed = self.last_focused_window
        if closed is None:
            return
        if closed.key in visible_keys:
            return
        frontmost_pid = MacOS.frontmost_pid()
        if frontmost_pid is not None and frontmost_pid != closed.pid:
            return
        replacement = min(
            (window for window in windows if window.screen_key == closed.screen_key),
            key=lambda window: closed.frame.distance_to(window.frame),
            default=None,
        )
        if replacement is None and len(windows) > 0:
            replacement = windows[0]
        if replacement is not None:
            MacOS.focus_window(replacement)
            self.last_focused_window = replacement

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
        if self.work_timer is not None:
            _ = CoreFoundation.CFRunLoopTimerInvalidate(self.work_timer)
            self.work_timer = None
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


def window_sort_key(window: WindowInfo) -> tuple[str, int, int, int, int, str]:
    """Return a geometry-first order for initial placement and new windows.

    >>> left = WindowInfo(key="left", pid=2, ax="left", title="", frame=Rect(x=0, y=0, width=100, height=100), screen_key="s", window_number=2, order=2)
    >>> right = WindowInfo(key="right", pid=1, ax="right", title="", frame=Rect(x=100, y=0, width=100, height=100), screen_key="s", window_number=1, order=1)
    >>> [window.key for window in sorted((right, left), key=window_sort_key)]
    ['left', 'right']
    """
    return (
        window.screen_key,
        window.frame.x,
        window.frame.y,
        window.pid,
        window.order,
        window.key,
    )


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
                    column=column,
                    row_weights_by_key=(
                        row_weights_by_key if row_weights_by_key is not None else {}
                    ),
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
    fallback = sum(known) / len(known) if len(known) > 0 else 1.0
    return tuple(row_weights_by_key.get(key, fallback) for key in column)


def screen_for_frame(frame: Rect, screens: tuple[ScreenInfo, ...]) -> ScreenInfo | None:
    if len(screens) == 0:
        return None
    containing = [
        screen
        for screen in screens
        if screen.frame.contains_point(x=frame.center_x, y=frame.center_y)
    ]
    if len(containing) > 0:
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
    fallback_candidates: tuple[WindowInfo, ...],
) -> WindowInfo | None:
    columns = arranged.get(current.screen_key)
    if columns is None or len(columns) == 0:
        return geometric_focus_target(
            current=current, direction=direction, candidates=fallback_candidates
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
                case _:
                    assert_never(direction)
    return geometric_focus_target(
        current=current, direction=direction, candidates=fallback_candidates
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
        case _:
            assert_never(direction)
    return min(
        possible,
        key=lambda window: current.frame.distance_to(window.frame),
        default=None,
    )


def request_summary(request: IpcRequest) -> str:
    match request:
        case FocusRequest(direction=direction):
            return f"focus {direction}"
        case MoveRequest(direction=direction):
            return f"move {direction}"
        case GotoDesktopRequest(desktop=desktop):
            return f"{request.kind} {desktop}"
        case ColumnsRequest(columns=columns):
            return f"columns {columns:g}"
        case SimpleRequest():
            return request.kind
        case _:
            assert_never(request)


@dataclass(frozen=True, kw_only=True)
class DaemonArgs:
    columns: float
    poll_seconds: float | Literal["disabled"]
    socket_path: Path | None
    keybindings_enabled: bool
    keybindings_path: Path | None
    verbose: bool

    @classmethod
    def add_parser(cls, subparsers: Subparsers) -> None:
        parser = subparsers.add_parser("daemon")
        _ = parser.add_argument("--columns", "-c", type=float, default=2.0)
        _ = parser.add_argument("--poll-seconds", type=float, default=None)
        _ = parser.add_argument("--socket", type=Path, default=None, dest="socket_path")
        _ = parser.add_argument("--keybindings", type=Path, default=None)
        _ = parser.add_argument("--verbose", "-v", action="store_true")
        _ = parser.add_argument(
            "--no-keybindings", action="store_false", dest="keybindings_enabled"
        )
        _ = parser.set_defaults(keybindings_enabled=True)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> DaemonArgs:
        poll_seconds_arg = cast(float | None, args.poll_seconds)
        poll_seconds: float | Literal["disabled"] = (
            "disabled" if poll_seconds_arg is None else poll_seconds_arg
        )
        return cls(
            columns=cast(float, args.columns),
            poll_seconds=poll_seconds,
            socket_path=cast(Path | None, args.socket_path),
            keybindings_enabled=cast(bool, args.keybindings_enabled),
            keybindings_path=cast(Path | None, args.keybindings),
            verbose=cast(bool, args.verbose),
        )

    def main(self) -> int:
        config = LayoutConfig(
            columns=self.columns,
            poll_seconds=self.poll_seconds,
            socket_path=self.socket_path or default_socket_path(),
        )
        keybindings = (
            load_keybindings(self.keybindings_path)
            if self.keybindings_enabled
            else None
        )
        daemon = WindowDaemon(
            config=config,
            keybindings=keybindings,
            verbose=self.verbose,
        )
        return daemon.run()


@dataclass(frozen=True, kw_only=True)
class ClientArgs:
    request: IpcRequest
    socket_path: Path | None = None
    verbose: bool = False

    @staticmethod
    def add_options(parser: argparse.ArgumentParser) -> None:
        _ = parser.add_argument("--socket", type=Path, default=None, dest="socket_path")
        _ = parser.add_argument("--verbose", "-v", action="store_true")

    @classmethod
    def add_parsers(cls, subparsers: Subparsers) -> None:
        for command in CliCommands.FOCUS_MOVE:
            parser = subparsers.add_parser(command)
            _ = parser.add_argument(
                "direction", choices=("left", "right", "up", "down")
            )
            cls.add_options(parser)

        fullscreen_parser = subparsers.add_parser("fullscreen")
        cls.add_options(fullscreen_parser)

        close_parser = subparsers.add_parser("close")
        cls.add_options(close_parser)

        columns_parser = subparsers.add_parser("columns")
        _ = columns_parser.add_argument("number_of_columns", type=float)
        cls.add_options(columns_parser)

        goto_desktop_parser = subparsers.add_parser("goto-desktop")
        _ = goto_desktop_parser.add_argument("number", type=int)
        cls.add_options(goto_desktop_parser)

        for command in CliCommands.UTILITY:
            parser = subparsers.add_parser(command)
            cls.add_options(parser)

    @classmethod
    def from_args(cls, *, command: CommandKind, args: argparse.Namespace) -> ClientArgs:
        socket_path = cast(Path | None, args.socket_path)
        verbose = cast(bool, args.verbose)
        match command:
            case "focus":
                return cls(
                    request=FocusRequest(direction=cast(Direction, args.direction)),
                    socket_path=socket_path,
                    verbose=verbose,
                )
            case "move":
                return cls(
                    request=MoveRequest(direction=cast(Direction, args.direction)),
                    socket_path=socket_path,
                    verbose=verbose,
                )
            case "fullscreen" | "close" | "retile" | "status" | "stop":
                return cls(
                    request=SimpleRequest(kind=command),
                    socket_path=socket_path,
                    verbose=verbose,
                )
            case "goto-desktop":
                return cls(
                    request=GotoDesktopRequest(desktop=cast(int, args.number)),
                    socket_path=socket_path,
                    verbose=verbose,
                )
            case "columns":
                return cls(
                    request=ColumnsRequest(columns=cast(float, args.number_of_columns)),
                    socket_path=socket_path,
                    verbose=verbose,
                )
            case _:
                assert_never(command)

    def main(self) -> int:
        response = Ipc.send(
            path=self.socket_path or default_socket_path(), request=self.request
        )
        if self.verbose:
            output = sys.stdout if response.ok else sys.stderr
            print(response.message, file=output)
        return 0 if response.ok else 1


@dataclass(frozen=True, kw_only=True)
class LaunchdPlistArgs:
    label: str
    uv: Path
    mwm_bin: Path
    workdir: Path
    stdout_log: Path
    stderr_log: Path
    output: Path | None

    @classmethod
    def add_parser(cls, subparsers: Subparsers) -> None:
        parser = subparsers.add_parser("launchd-plist")
        _ = parser.add_argument("--label", default=Launchd.LABEL)
        _ = parser.add_argument("--uv", type=Path, default=None)
        _ = parser.add_argument("--mwm-bin", type=Path, default=None)
        _ = parser.add_argument("--workdir", type=Path, default=None)
        _ = parser.add_argument("--stdout-log", type=Path, default=None)
        _ = parser.add_argument("--stderr-log", type=Path, default=None)
        _ = parser.add_argument("--output", type=Path, default=None)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> LaunchdPlistArgs:
        defaults = cls.default(
            uv=cast(Path | None, args.uv),
            label=cast(str, args.label),
        )
        return cls(
            label=defaults.label,
            uv=defaults.uv,
            mwm_bin=cast(Path | None, args.mwm_bin) or defaults.mwm_bin,
            workdir=cast(Path | None, args.workdir) or defaults.workdir,
            stdout_log=cast(Path | None, args.stdout_log) or defaults.stdout_log,
            stderr_log=cast(Path | None, args.stderr_log) or defaults.stderr_log,
            output=cast(Path | None, args.output) or defaults.output,
        )

    @classmethod
    def default(
        cls,
        *,
        uv: Path | None = None,
        label: str = Launchd.LABEL,
    ) -> LaunchdPlistArgs:
        home = Path.home()
        user = getpass.getuser()
        log_dir = Path("/") / "tmp"
        return cls(
            label=label,
            uv=uv or cls.default_uv_path(),
            mwm_bin=home / Launchd.LOCAL_BIN_NAME,
            workdir=home,
            stdout_log=log_dir / f"mwm_{user}.out.log",
            stderr_log=log_dir / f"mwm_{user}.err.log",
            output=None,
        )

    @staticmethod
    def default_uv_path() -> Path:
        uv = shutil.which("uv")
        if uv is None:
            raise SystemExit("uv was not found on PATH; install uv or pass --uv PATH")
        return Path(uv)

    @staticmethod
    def plist(
        *,
        label: str,
        uv: Path,
        mwm_bin: Path,
        workdir: Path,
        stdout_log: Path,
        stderr_log: Path,
    ) -> Plist:
        """Build the LaunchAgent plist.

        >>> plist = LaunchdPlistArgs.plist(
        ...     label="mwm",
        ...     uv=Path("/usr/local/bin/uv"),
        ...     mwm_bin=Path("/Users/me/.local/bin/mwm.py"),
        ...     workdir=Path("/Users/me"),
        ...     stdout_log=Path("/tmp/mwm_me.out.log"),
        ...     stderr_log=Path("/tmp/mwm_me.err.log"),
        ... )
        >>> plist["ProgramArguments"]
        ['/usr/local/bin/uv', 'run', '/Users/me/.local/bin/mwm.py', 'daemon']
        >>> plist["StandardErrorPath"]
        '/tmp/mwm_me.err.log'
        """
        return {
            "Label": label,
            "ProgramArguments": [str(uv), "run", str(mwm_bin), "daemon"],
            "WorkingDirectory": str(workdir),
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardOutPath": str(stdout_log),
            "StandardErrorPath": str(stderr_log),
        }

    def main(self) -> int:
        payload = plistlib.dumps(
            self.plist(
                label=self.label,
                uv=self.uv,
                mwm_bin=self.mwm_bin,
                workdir=self.workdir,
                stdout_log=self.stdout_log,
                stderr_log=self.stderr_log,
            )
        )
        if self.output is None:
            _ = sys.stdout.buffer.write(payload)
        else:
            _ = self.output.write_bytes(payload)
        return 0


ParsedCli = DaemonArgs | ClientArgs | LaunchdPlistArgs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mwm.py")
    subparsers = cast(
        Subparsers,
        cast(object, parser.add_subparsers(dest="command", required=True)),
    )
    DaemonArgs.add_parser(subparsers)
    LaunchdPlistArgs.add_parser(subparsers)
    ClientArgs.add_parsers(subparsers)
    return parser


def parse_cli_args(argv: list[str] | None = None) -> ParsedCli:
    parser = build_parser()
    return cli_from_namespace(parser.parse_args(argv))


def cli_from_namespace(args: argparse.Namespace) -> ParsedCli:
    command = cast(CliCommand, args.command)
    match command:
        case "daemon":
            return DaemonArgs.from_args(args)
        case "launchd-plist":
            return LaunchdPlistArgs.from_args(args)
        case (
            "focus"
            | "move"
            | "fullscreen"
            | "close"
            | "retile"
            | "status"
            | "stop"
            | "goto-desktop"
            | "columns"
        ):
            return ClientArgs.from_args(command=command, args=args)
        case _:
            assert_never(command)


def main(argv: list[str] | None = None) -> int:
    return parse_cli_args(argv).main()


if __name__ == "__main__":
    raise SystemExit(main())
