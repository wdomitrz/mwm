#!/usr/bin/env python3
################################################################
# Copyright (c) 2026 Witalis Domitrz <witekdomitrz@gmail.com>
# AGPL License
################################################################
#
# /// script
# dependencies = [
#   "typer",
#   "pyobjc-framework-ApplicationServices",
#   "pyobjc-framework-Cocoa",
#   "pyobjc-framework-Quartz",
# ]
# ///

from __future__ import annotations

import json
import math
import os
import queue
import signal
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, assert_never, cast

import typer

Direction = Literal["left", "right", "up", "down"]
CommandKind = Literal[
    "focus", "move", "fullscreen", "columns", "retile", "status", "stop"
]
JsonMap = dict[str, Any]


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir())
    return base / f"m3-{os.getuid()}.sock"


@dataclass(frozen=True, kw_only=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    MIN_WIDTH: ClassVar[int] = 40
    MIN_HEIGHT: ClassVar[int] = 40

    @classmethod
    def from_ax_rect(cls, raw: tuple[tuple[float, float], tuple[float, float]]) -> Rect:
        (x, y), (width, height) = raw
        return cls(x=round(x), y=round(y), width=round(width), height=round(height))

    @classmethod
    def from_quartz_bounds(cls, raw: Mapping[str, Any]) -> Rect | None:
        """Convert a CGWindow bounds dictionary into a rectangle.

        >>> Rect.from_quartz_bounds({"X": 1.2, "Y": 3.8, "Width": 10, "Height": 20})
        Rect(x=1, y=4, width=10, height=20)
        >>> Rect.from_quartz_bounds({"X": 1})
        """
        try:
            return cls(
                x=round(float(raw["X"])),
                y=round(float(raw["Y"])),
                width=round(float(raw["Width"])),
                height=round(float(raw["Height"])),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_cocoa_rect(
        cls,
        raw: Any,
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

    def split_columns(
        self, *, count: int, columns: float, gap: int
    ) -> tuple[Rect, ...]:
        """Return i3-like columns, including fractional final columns.

        >>> frame = Rect(x=0, y=0, width=1000, height=500)
        >>> [rect.width for rect in frame.split_columns(count=3, columns=2.5, gap=0)]
        [400, 400, 200]
        >>> [rect.width for rect in frame.split_columns(count=2, columns=2.5, gap=0)]
        [400, 600]
        """
        if count <= 0:
            return ()
        if count == 1:
            return (self,)

        available_width = max(0, self.width - gap * (count - 1))
        slot = 1.0 / columns
        weights = tuple([slot] * (count - 1) + [max(0.001, 1 - slot * (count - 1))])
        widths = self._partition(available_width, weights)
        x = self.x
        rects: list[Rect] = []
        for width in widths:
            rects.append(Rect(x=x, y=self.y, width=width, height=self.height))
            x += width + gap
        return tuple(rects)

    def split_rows(self, *, count: int, gap: int) -> tuple[Rect, ...]:
        if count <= 0:
            return ()
        if count == 1:
            return (self,)

        available_height = max(0, self.height - gap * (count - 1))
        heights = self._partition(available_height, tuple(1.0 for _ in range(count)))
        y = self.y
        rects: list[Rect] = []
        for height in heights:
            rects.append(Rect(x=self.x, y=y, width=self.width, height=height))
            y += height + gap
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
    ax: Any
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
    gap: int = 0
    poll_seconds: float = 1.0
    socket_path: Path = field(default_factory=default_socket_path)

    def __post_init__(self) -> None:
        """Validate user-controlled layout values.

        >>> LayoutConfig(columns=2.5, gap=4, poll_seconds=0.25).max_column_count
        3
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
        if self.gap < 0:
            msg = "gap must not be negative"
            raise ValueError(msg)
        if not math.isfinite(self.poll_seconds) or self.poll_seconds <= 0:
            msg = "poll_seconds must be a finite positive number"
            raise ValueError(msg)

    def with_columns(self, columns: float) -> LayoutConfig:
        return LayoutConfig(
            columns=columns,
            gap=self.gap,
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
    columns: float | None = None

    @classmethod
    def from_json(cls, payload: bytes) -> IpcRequest:
        raw = cast(JsonMap, json.loads(payload.decode()))
        kind = parse_command_kind(str(raw["kind"]))
        direction = (
            parse_direction(str(raw["direction"]))
            if raw.get("direction") is not None
            else None
        )
        columns = float(raw["columns"]) if raw.get("columns") is not None else None
        return cls(kind=kind, direction=direction, columns=columns)

    def to_json(self) -> bytes:
        return json.dumps(
            {"kind": self.kind, "direction": self.direction, "columns": self.columns},
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True, kw_only=True)
class IpcResponse:
    ok: bool
    message: str

    @classmethod
    def from_json(cls, payload: bytes) -> IpcResponse:
        raw = cast(JsonMap, json.loads(payload.decode()))
        return cls(ok=bool(raw["ok"]), message=str(raw["message"]))

    def to_json(self) -> bytes:
        return json.dumps(
            {"ok": self.ok, "message": self.message}, separators=(",", ":")
        ).encode()


@dataclass(frozen=True, kw_only=True)
class AppObserver:
    pid: int
    app: Any
    observer: Any


@dataclass(kw_only=True)
class PendingIpcCall:
    payload: bytes
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
class MacApi:
    appkit: Any
    core: Any
    hiservices: Any
    quartz: Any

    AX_WINDOW_NUMBER_ATTRIBUTE: ClassVar[str] = "AXWindowNumber"

    @classmethod
    def load(cls) -> MacApi:
        try:
            import AppKit  # pyright: ignore[reportMissingImports]
            import CoreFoundation  # pyright: ignore[reportMissingImports]
            import HIServices  # pyright: ignore[reportMissingImports]
            import Quartz  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            msg = (
                "m3 requires PyObjC on macOS. Install the script dependencies with uv, "
                "pipx, or pip on the machine that will run the daemon."
            )
            raise RuntimeError(msg) from error
        return cls(
            appkit=AppKit, core=CoreFoundation, hiservices=HIServices, quartz=Quartz
        )

    def ensure_accessibility(self) -> None:
        if cast(bool, self.hiservices.AXIsProcessTrusted()):
            return
        prompt_key = self.hiservices.kAXTrustedCheckOptionPrompt
        _trusted = self.hiservices.AXIsProcessTrustedWithOptions({prompt_key: True})
        msg = "Accessibility permission is required; grant it in System Settings and start the daemon again."
        raise RuntimeError(msg)

    def ax_get(self, element: Any, attribute: str) -> Any | None:
        error, value = self.hiservices.AXUIElementCopyAttributeValue(
            element, attribute, None
        )
        if error != self.hiservices.kAXErrorSuccess:
            return None
        return value

    def ax_set(self, element: Any, attribute: str, value: Any) -> bool:
        error = self.hiservices.AXUIElementSetAttributeValue(element, attribute, value)
        return cast(bool, error == self.hiservices.kAXErrorSuccess)

    def ax_action(self, element: Any, action: str) -> bool:
        error = self.hiservices.AXUIElementPerformAction(element, action)
        return cast(bool, error == self.hiservices.kAXErrorSuccess)

    def ax_pid(self, element: Any) -> int | None:
        error, pid = self.hiservices.AXUIElementGetPid(element, None)
        if error != self.hiservices.kAXErrorSuccess:
            return None
        return int(pid)

    def ax_bool(self, element: Any, attribute: str, *, default: bool = False) -> bool:
        value = self.ax_get(element, attribute)
        if value is None:
            return default
        return bool(value)

    def ax_point(self, value: Any) -> tuple[float, float] | None:
        ok, point = self.hiservices.AXValueGetValue(
            value, self.hiservices.kAXValueTypeCGPoint, None
        )
        if not ok:
            return None
        x, y = cast(tuple[float, float], point)
        return (float(x), float(y))

    def ax_size(self, value: Any) -> tuple[float, float] | None:
        ok, size = self.hiservices.AXValueGetValue(
            value, self.hiservices.kAXValueTypeCGSize, None
        )
        if not ok:
            return None
        width, height = cast(tuple[float, float], size)
        return (float(width), float(height))

    def ax_frame(self, window: Any) -> Rect | None:
        position_value = self.ax_get(window, self.hiservices.kAXPositionAttribute)
        size_value = self.ax_get(window, self.hiservices.kAXSizeAttribute)
        if position_value is None or size_value is None:
            return None
        position = self.ax_point(position_value)
        size = self.ax_size(size_value)
        if position is None or size is None:
            return None
        return Rect.from_ax_rect((position, size))

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

    def screens(self) -> tuple[ScreenInfo, ...]:
        screens = tuple(self.appkit.NSScreen.screens())
        if not screens:
            return ()
        main_screen = self.appkit.NSScreen.mainScreen() or screens[0]
        main_screen_height = float(main_screen.frame().size.height)
        result: list[ScreenInfo] = []
        for index, screen in enumerate(screens):
            frame = Rect.from_cocoa_rect(
                screen.visibleFrame(), main_screen_height=main_screen_height
            )
            result.append(ScreenInfo(key=f"{index}:{frame.as_key()}", frame=frame))
        return tuple(result)

    def running_pids(self) -> tuple[int, ...]:
        workspace = self.appkit.NSWorkspace.sharedWorkspace()
        pids: list[int] = []
        for app in workspace.runningApplications():
            pid = int(app.processIdentifier())
            if pid > 0 and pid != os.getpid():
                pids.append(pid)
        return tuple(sorted(set(pids)))

    def visible_window_index(self) -> VisibleWindowIndex:
        options = (
            self.quartz.kCGWindowListOptionOnScreenOnly
            | self.quartz.kCGWindowListExcludeDesktopElements
        )
        raw_windows = self.quartz.CGWindowListCopyWindowInfo(
            options, self.quartz.kCGNullWindowID
        )
        numbers_by_pid: dict[int, set[int]] = {}
        order_by_pid_number: dict[tuple[int, int], int] = {}
        frames_by_pid: dict[int, list[tuple[int, Rect]]] = {}
        for order, raw in enumerate(raw_windows):
            if int(raw.get(self.quartz.kCGWindowLayer, 1)) != 0:
                continue
            if not bool(raw.get(self.quartz.kCGWindowIsOnscreen, False)):
                continue
            alpha = float(raw.get(self.quartz.kCGWindowAlpha, 1.0))
            if alpha <= 0:
                continue
            pid = int(raw.get(self.quartz.kCGWindowOwnerPID, 0))
            number = int(raw.get(self.quartz.kCGWindowNumber, 0))
            if pid > 0 and number > 0:
                numbers_by_pid.setdefault(pid, set()).add(number)
                order_by_pid_number[(pid, number)] = order
                bounds = raw.get(self.quartz.kCGWindowBounds)
                if isinstance(bounds, Mapping):
                    frame = Rect.from_quartz_bounds(cast(Mapping[str, Any], bounds))
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
            for window in raw_windows:
                info = self.window_info(
                    window, pid=pid, screens=screens, visible_index=visible_index
                )
                if info is not None:
                    windows.append(info)
        return tuple(
            sorted(windows, key=lambda item: (item.screen_key, item.order, item.title))
        )

    def window_info(
        self,
        window: Any,
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
        order = visible_index.order(pid=pid, number=number, frame=frame)
        fallback_title = title or f"untitled-{order}"
        key = (
            f"{pid}:{number}"
            if number is not None
            else f"{pid}:fallback:{order}:{fallback_title}"
        )
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

    def window_number(self, window: Any) -> int | None:
        value = self.ax_get(window, self.AX_WINDOW_NUMBER_ATTRIBUTE)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


@dataclass(kw_only=True)
class LayoutState:
    columns_by_screen: dict[str, list[list[str]]] = field(default_factory=dict)
    fullscreen_keys: set[str] = field(default_factory=set)

    def keep_only(
        self, *, visible_keys: set[str], visible_screen_keys: set[str]
    ) -> None:
        self.fullscreen_keys.intersection_update(visible_keys)
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
        while len(columns) < target:
            source = max(range(len(columns)), key=lambda index: len(columns[index]))
            if len(columns[source]) <= 1:
                break
            key = columns[source].pop()
            columns.insert(source + 1, [key])
        return columns

    def find(self, *, key: str) -> tuple[str, int, int] | None:
        for screen_key, columns in self.columns_by_screen.items():
            for column_index, column in enumerate(columns):
                if key in column:
                    return (screen_key, column_index, column.index(key))
        return None

    def move(self, *, key: str, direction: Direction) -> bool:
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
                    return False
                move_between_columns(
                    columns=columns,
                    key=key,
                    source_index=column_index,
                    target_index=column_index - 1,
                )
            case "right":
                if column_index >= len(columns) - 1:
                    return False
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

    def __init__(self, *, config: LayoutConfig, api: MacApi) -> None:
        self.config = config
        self.api = api
        self.state = LayoutState()
        self.observers: dict[int, AppObserver] = {}
        self.observed_windows: set[str] = set()
        self.lock = threading.RLock()
        self.pending_ipc_calls: queue.Queue[PendingIpcCall] = queue.Queue()
        self.running = False
        self.ipc_thread: threading.Thread | None = None
        self.tick_timer: Any | None = None
        self.next_periodic_at = 0.0
        self.retile_at: float | None = None
        self.ignore_events_until = 0.0
        self.run_loop: Any | None = None

    def run(self) -> int:
        self.api.ensure_accessibility()
        self.running = True
        self.run_loop = self.api.core.CFRunLoopGetCurrent()
        self._install_signal_handlers()
        self._start_ipc()
        self.refresh_observers()
        self.retile()
        self.next_periodic_at = time.monotonic() + self.config.poll_seconds
        self._install_tick_timer()
        self.api.core.CFRunLoopRun()
        self.running = False
        self._cleanup()
        return 0

    def _install_signal_handlers(self) -> None:
        def stop_from_signal(_signum: int, _frame: Any) -> None:
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
        thread = threading.Thread(target=self._serve_ipc, name="m3-ipc", daemon=True)
        thread.start()
        self.ipc_thread = thread

    def _install_tick_timer(self) -> None:
        run_loop = self.run_loop or self.api.core.CFRunLoopGetCurrent()
        self.tick_timer = self.api.core.CFRunLoopTimerCreate(
            None,
            self.api.core.CFAbsoluteTimeGetCurrent() + self.TICK_SECONDS,
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
        call = PendingIpcCall(payload=payload)
        self.pending_ipc_calls.put(call)
        self._wake_run_loop()
        return call.wait(timeout=self.IPC_RESPONSE_TIMEOUT_SECONDS)

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

    def _handle_client_payload(self, payload: bytes) -> IpcResponse:
        try:
            request = IpcRequest.from_json(payload)
            message = self.handle(request)
        except Exception as error:
            return IpcResponse(ok=False, message=str(error))
        return IpcResponse(ok=True, message=message)

    def _tick(self, _timer: Any, _info: Any) -> None:
        with self.lock:
            self._drain_ipc_calls()
            if not self.running:
                return
            now = time.monotonic()
            if now >= self.next_periodic_at:
                self.refresh_observers()
                self.retile()
                self.next_periodic_at = now + self.config.poll_seconds
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
            call.respond(self._handle_client_payload(call.payload))

    def handle(self, request: IpcRequest) -> str:
        with self.lock:
            match request.kind:
                case "focus":
                    direction = require_direction(request)
                    return self.focus(direction=direction)
                case "move":
                    direction = require_direction(request)
                    return self.move(direction=direction)
                case "fullscreen":
                    return self.toggle_fullscreen()
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
        return f"running: columns={self.config.columns:g}, windows={len(windows)}, socket={self.config.socket_path}"

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
        error, observer = self.api.hiservices.AXObserverCreate(
            pid, self._ax_callback, None
        )
        if error != self.api.hiservices.kAXErrorSuccess:
            return
        self.observers[pid] = AppObserver(pid=pid, app=app, observer=observer)
        self.api.core.CFRunLoopAddSource(
            self.run_loop or self.api.core.CFRunLoopGetCurrent(),
            self.api.hiservices.AXObserverGetRunLoopSource(observer),
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

    def _add_notification(self, *, pid: int, element: Any, notification: str) -> None:
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
        self, _observer: Any, _element: Any, notification: str, _refcon: Any
    ) -> None:
        if notification in (
            self.api.hiservices.kAXWindowCreatedNotification,
            self.api.hiservices.kAXFocusedWindowChangedNotification,
            self.api.hiservices.kAXMainWindowChangedNotification,
            self.api.hiservices.kAXUIElementDestroyedNotification,
            self.api.hiservices.kAXWindowMiniaturizedNotification,
            self.api.hiservices.kAXWindowDeminiaturizedNotification,
        ):
            self.refresh_observers()
        self.schedule_retile()

    def schedule_retile(self) -> None:
        now = time.monotonic()
        if now < self.ignore_events_until:
            return
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
            screen=screen, columns=columns, config=self.config
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
        changed = self.state.move(key=focused.key, direction=direction)
        if not changed:
            return "no move target"
        self.retile()
        fresh = self._fresh_window_for_key(focused.key)
        if fresh is not None:
            self.api.focus_window(fresh)
        return f"moved {direction}"

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


def layout_targets(
    *,
    screen: ScreenInfo,
    columns: list[list[str]],
    config: LayoutConfig,
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
    """
    column_frames = screen.frame.split_columns(
        count=len(columns), columns=config.columns, gap=config.gap
    )
    return {
        key: frame
        for column, column_frame in zip(columns, column_frames, strict=True)
        for key, frame in zip(
            column,
            column_frame.split_rows(count=len(column), gap=config.gap),
            strict=True,
        )
    }


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


def move_between_columns(
    *, columns: list[list[str]], key: str, source_index: int, target_index: int
) -> None:
    """Move horizontally without collapsing one-window columns into no-ops.

    >>> columns = [["a"], ["b"], ["c"]]
    >>> move_between_columns(columns=columns, key="b", source_index=1, target_index=0)
    >>> columns
    [['b'], ['a'], ['c']]
    >>> columns = [["a", "b"], ["c"]]
    >>> move_between_columns(columns=columns, key="b", source_index=0, target_index=1)
    >>> columns
    [['a'], ['c', 'b']]
    """
    source = columns[source_index]
    target = columns[target_index]
    old_row = source.index(key)
    if old_row < len(target):
        source[old_row], target[old_row] = target[old_row], source[old_row]
        return
    _ = source.pop(old_row)
    target.append(key)


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


def parse_direction(value: str) -> Direction:
    match value:
        case "left" | "right" | "up" | "down":
            return cast(Direction, value)
        case _:
            msg = f"unknown direction: {value}"
            raise ValueError(msg)


def parse_command_kind(value: str) -> CommandKind:
    match value:
        case "focus" | "move" | "fullscreen" | "columns" | "retile" | "status" | "stop":
            return cast(CommandKind, value)
        case _:
            msg = f"unknown command: {value}"
            raise ValueError(msg)


@dataclass(kw_only=True)
class _MemoryApi:
    windows: tuple[WindowInfo, ...]
    screen_values: tuple[ScreenInfo, ...]
    focused_key: str
    frames: dict[str, Rect] = field(default_factory=dict)
    focused_history: list[str] = field(default_factory=list)

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
>>> Rect.from_quartz_bounds({"X": 1.2, "Y": 3.8, "Width": 10, "Height": 20})
Rect(x=1, y=4, width=10, height=20)
>>> rect.contains_point(x=25, y=40)
True
>>> rect.intersection_area(Rect(x=25, y=50, width=10, height=20))
100
>>> rect.distance_to(Rect(x=40, y=20, width=30, height=40))
30.0
>>> [row.as_key() for row in Rect(x=0, y=0, width=10, height=10).split_rows(count=3, gap=1)]
['0,0,10,3', '0,4,10,3', '0,8,10,2']
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
>>> state.move(key="b", direction="left")
True
>>> state.columns_by_screen["s"]
[['b'], ['a'], ['c', 'd']]
>>> state.move(key="d", direction="up")
True
>>> state.columns_by_screen["s"]
[['b'], ['a'], ['d', 'c']]
>>> state.reconcile(screen=screen, windows=windows, config=LayoutConfig(columns=2.5))
[['b'], ['a'], ['d', 'c']]
>>> state.keep_only(visible_keys={"b", "d"}, visible_screen_keys={"s"})
>>> state.columns_by_screen
{'s': [['b'], ['d']]}
>>> state.keep_only(visible_keys={"b", "d"}, visible_screen_keys=set())
>>> state.columns_by_screen
{}
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
[['b'], ['a'], ['c', 'd']]
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
""",
}


@dataclass(frozen=True, kw_only=True)
class DaemonArgs:
    columns: float
    gap: int
    poll_seconds: float
    socket_path: Path | None

    def main(self) -> int:
        config = LayoutConfig(
            columns=self.columns,
            gap=self.gap,
            poll_seconds=self.poll_seconds,
            socket_path=self.socket_path or Ipc.default_socket_path(),
        )
        daemon = WindowDaemon(config=config, api=MacApi.load())
        return daemon.run()


@dataclass(frozen=True, kw_only=True)
class ClientArgs:
    request: IpcRequest
    socket_path: Path | None = None

    def main(self) -> int:
        response = Ipc.send(
            path=self.socket_path or Ipc.default_socket_path(), request=self.request
        )
        print(response.message)
        return 0 if response.ok else 1


app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def daemon(
    columns: Annotated[float, typer.Option("--columns", "-c")] = 2.0,
    gap: Annotated[int, typer.Option("--gap", "-g")] = 0,
    poll_seconds: Annotated[float, typer.Option("--poll-seconds")] = 1.0,
    socket_path: Annotated[Path | None, typer.Option("--socket")] = None,
) -> None:
    raise typer.Exit(
        DaemonArgs(
            columns=columns, gap=gap, poll_seconds=poll_seconds, socket_path=socket_path
        ).main()
    )


@app.command()
def focus(
    direction: Annotated[str, typer.Argument()],
    socket_path: Annotated[Path | None, typer.Option("--socket")] = None,
) -> None:
    request = IpcRequest(kind="focus", direction=parse_direction(direction))
    raise typer.Exit(ClientArgs(request=request, socket_path=socket_path).main())


@app.command()
def move(
    direction: Annotated[str, typer.Argument()],
    socket_path: Annotated[Path | None, typer.Option("--socket")] = None,
) -> None:
    request = IpcRequest(kind="move", direction=parse_direction(direction))
    raise typer.Exit(ClientArgs(request=request, socket_path=socket_path).main())


@app.command()
def fullscreen(
    socket_path: Annotated[Path | None, typer.Option("--socket")] = None,
) -> None:
    request = IpcRequest(kind="fullscreen")
    raise typer.Exit(ClientArgs(request=request, socket_path=socket_path).main())


@app.command("columns")
def set_columns(
    number_of_columns: Annotated[float, typer.Argument()],
    socket_path: Annotated[Path | None, typer.Option("--socket")] = None,
) -> None:
    request = IpcRequest(kind="columns", columns=number_of_columns)
    raise typer.Exit(ClientArgs(request=request, socket_path=socket_path).main())


@app.command()
def retile(
    socket_path: Annotated[Path | None, typer.Option("--socket")] = None,
) -> None:
    raise typer.Exit(
        ClientArgs(request=IpcRequest(kind="retile"), socket_path=socket_path).main()
    )


@app.command()
def status(
    socket_path: Annotated[Path | None, typer.Option("--socket")] = None,
) -> None:
    raise typer.Exit(
        ClientArgs(request=IpcRequest(kind="status"), socket_path=socket_path).main()
    )


@app.command()
def stop(socket_path: Annotated[Path | None, typer.Option("--socket")] = None) -> None:
    raise typer.Exit(
        ClientArgs(request=IpcRequest(kind="stop"), socket_path=socket_path).main()
    )


if __name__ == "__main__":
    try:
        app()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        raise typer.Exit(1) from error
