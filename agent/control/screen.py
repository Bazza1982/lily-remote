"""Screen capture and streaming using mss.

This module handles screenshot capture, JPEG compression, frame rate control,
and adaptive quality based on bandwidth for streaming to clients.
"""

import asyncio
import base64
import io
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Awaitable

import mss
import mss.tools
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class ScreenInfo:
    """Screen information."""
    width: int
    height: int
    left: int
    top: int
    dpi: int = 96


@dataclass
class FrameMetrics:
    """Metrics for a captured frame."""
    capture_time_ms: float
    encode_time_ms: float
    frame_size_bytes: int
    quality: int
    timestamp: float


@dataclass
class BandwidthMetrics:
    """Bandwidth estimation metrics."""
    samples: list[tuple[float, int]] = field(default_factory=list)  # (timestamp, bytes)
    window_seconds: float = 5.0
    max_samples: int = 100

    def add_sample(self, size_bytes: int) -> None:
        """Add a frame size sample."""
        now = time.time()
        self.samples.append((now, size_bytes))

        # Prune old samples
        cutoff = now - self.window_seconds
        self.samples = [
            (ts, sz) for ts, sz in self.samples
            if ts > cutoff
        ][-self.max_samples:]

    def estimate_throughput(self) -> float:
        """Estimate current throughput in bytes per second."""
        if len(self.samples) < 2:
            return float('inf')  # Not enough data

        first_ts, _ = self.samples[0]
        last_ts, _ = self.samples[-1]
        duration = last_ts - first_ts

        if duration <= 0:
            return float('inf')

        total_bytes = sum(sz for _, sz in self.samples)
        return total_bytes / duration


class AdaptiveQuality:
    """Adaptive quality controller based on frame delivery performance."""

    def __init__(
        self,
        min_quality: int = 30,
        max_quality: int = 90,
        initial_quality: int = 70,
        target_frame_budget_bytes: int = 100_000,  # 100KB target
    ):
        self.min_quality = min_quality
        self.max_quality = max_quality
        self.quality = initial_quality
        self.target_frame_budget_bytes = target_frame_budget_bytes
        self.bandwidth_metrics = BandwidthMetrics()
        self._lock = threading.Lock()

    def update(self, frame_size_bytes: int, frame_interval_seconds: float) -> int:
        """
        Update quality based on frame performance.

        Args:
            frame_size_bytes: Size of the last frame.
            frame_interval_seconds: Time between frames.

        Returns:
            New quality value.
        """
        with self._lock:
            self.bandwidth_metrics.add_sample(frame_size_bytes)

            # Calculate target bytes per frame based on interval
            # Allow some headroom (80% of theoretical max)
            throughput = self.bandwidth_metrics.estimate_throughput()

            if throughput == float('inf'):
                # Not enough samples, use default
                return self.quality

            theoretical_max = throughput * frame_interval_seconds * 0.8
            actual_budget = min(theoretical_max, self.target_frame_budget_bytes)

            # Adjust quality based on whether we're over/under budget
            if frame_size_bytes > actual_budget * 1.2:
                # Over budget by 20%+, reduce quality
                self.quality = max(self.min_quality, self.quality - 5)
            elif frame_size_bytes < actual_budget * 0.5:
                # Under budget by 50%+, can increase quality
                self.quality = min(self.max_quality, self.quality + 2)

            return self.quality

    def get_quality(self) -> int:
        """Get current quality setting."""
        with self._lock:
            return self.quality

    def set_quality(self, quality: int) -> None:
        """Manually set quality."""
        with self._lock:
            self.quality = max(self.min_quality, min(self.max_quality, quality))

    def reset(self) -> None:
        """Reset to initial state."""
        with self._lock:
            self.quality = 70
            self.bandwidth_metrics = BandwidthMetrics()


class ScreenCapture:
    """Screen capture using mss with JPEG compression."""

    def __init__(
        self,
        monitor_index: int = 0,
        default_quality: int = 70,
    ):
        """
        Initialize screen capture.

        Args:
            monitor_index: Index of monitor to capture (0 = primary).
            default_quality: Default JPEG quality (1-100).
        """
        self.monitor_index = monitor_index
        self.default_quality = default_quality
        self._sct: Optional[mss.mss] = None
        self._lock = threading.Lock()

    def _get_sct(self) -> mss.mss:
        """Get or create mss instance (thread-local)."""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def get_screen_info(self) -> ScreenInfo:
        """Get information about the target screen."""
        with self._lock:
            sct = self._get_sct()
            monitors = sct.monitors

            # monitors[0] is the "all monitors" virtual screen
            # monitors[1+] are individual monitors
            if self.monitor_index == 0:
                # Primary monitor (first real monitor)
                mon = monitors[1] if len(monitors) > 1 else monitors[0]
            else:
                idx = min(self.monitor_index, len(monitors) - 1)
                mon = monitors[idx]

            return ScreenInfo(
                width=mon["width"],
                height=mon["height"],
                left=mon["left"],
                top=mon["top"],
            )

    def capture_raw(self) -> tuple[bytes, int, int]:
        """
        Capture raw screenshot data.

        Returns:
            Tuple of (raw_bgra_bytes, width, height).
        """
        with self._lock:
            sct = self._get_sct()
            monitors = sct.monitors

            if self.monitor_index == 0:
                mon = monitors[1] if len(monitors) > 1 else monitors[0]
            else:
                idx = min(self.monitor_index, len(monitors) - 1)
                mon = monitors[idx]

            screenshot = sct.grab(mon)
            return screenshot.raw, screenshot.width, screenshot.height

    def capture_jpeg(
        self,
        quality: Optional[int] = None,
        scale: float = 1.0,
    ) -> tuple[bytes, FrameMetrics]:
        """
        Capture screenshot and encode as JPEG.

        Args:
            quality: JPEG quality (1-100). Uses default if not specified.
            scale: Scale factor for image (0.1-1.0). Lower = smaller frames.

        Returns:
            Tuple of (jpeg_bytes, frame_metrics).
        """
        quality = quality or self.default_quality
        quality = max(1, min(100, quality))
        scale = max(0.1, min(1.0, scale))

        timestamp = time.time()

        # Capture
        capture_start = time.perf_counter()
        raw_data, width, height = self.capture_raw()
        capture_time = (time.perf_counter() - capture_start) * 1000

        # Convert to PIL Image (BGRA -> RGB)
        encode_start = time.perf_counter()
        img = Image.frombytes("RGB", (width, height), raw_data, "raw", "BGRX")

        # Scale if needed
        if scale < 1.0:
            new_width = int(width * scale)
            new_height = int(height * scale)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Encode as JPEG
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        jpeg_data = buffer.getvalue()
        encode_time = (time.perf_counter() - encode_start) * 1000

        metrics = FrameMetrics(
            capture_time_ms=capture_time,
            encode_time_ms=encode_time,
            frame_size_bytes=len(jpeg_data),
            quality=quality,
            timestamp=timestamp,
        )

        return jpeg_data, metrics

    def capture_base64(
        self,
        quality: Optional[int] = None,
        scale: float = 1.0,
    ) -> tuple[str, FrameMetrics]:
        """
        Capture screenshot and return as base64-encoded JPEG.

        Args:
            quality: JPEG quality (1-100).
            scale: Scale factor for image.

        Returns:
            Tuple of (base64_jpeg_string, frame_metrics).
        """
        jpeg_data, metrics = self.capture_jpeg(quality=quality, scale=scale)
        b64_data = base64.b64encode(jpeg_data).decode("ascii")
        return b64_data, metrics

    def close(self) -> None:
        """Close the mss instance."""
        with self._lock:
            if self._sct is not None:
                self._sct.close()
                self._sct = None


class FrameStreamer:
    """
    Manages frame streaming with configurable FPS and adaptive quality.

    This class runs a capture loop and broadcasts frames to subscribers.
    """

    def __init__(
        self,
        capture: Optional[ScreenCapture] = None,
        min_fps: float = 2.0,
        max_fps: float = 10.0,
        initial_fps: float = 5.0,
        adaptive_quality: bool = True,
        min_quality: int = 30,
        max_quality: int = 90,
        initial_quality: int = 70,
        scale: float = 1.0,
    ):
        """
        Initialize frame streamer.

        Args:
            capture: ScreenCapture instance. Creates new one if not provided.
            min_fps: Minimum frames per second (2-10).
            max_fps: Maximum frames per second (2-10).
            initial_fps: Initial frame rate.
            adaptive_quality: Whether to use adaptive quality.
            min_quality: Minimum JPEG quality.
            max_quality: Maximum JPEG quality.
            initial_quality: Initial JPEG quality.
            scale: Image scale factor.
        """
        self.capture = capture or ScreenCapture()
        self.min_fps = max(1.0, min(30.0, min_fps))
        self.max_fps = max(self.min_fps, min(30.0, max_fps))
        self._target_fps = max(self.min_fps, min(self.max_fps, initial_fps))
        self.scale = scale

        self._adaptive_quality: Optional[AdaptiveQuality] = None
        if adaptive_quality:
            self._adaptive_quality = AdaptiveQuality(
                min_quality=min_quality,
                max_quality=max_quality,
                initial_quality=initial_quality,
            )
        else:
            self._fixed_quality = initial_quality

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._frame_callback: Optional[Callable[[str, FrameMetrics], Awaitable[None]]] = None
        self._last_frame_time = 0.0
        self._lock = asyncio.Lock()

    @property
    def target_fps(self) -> float:
        """Get current target FPS."""
        return self._target_fps

    @target_fps.setter
    def target_fps(self, value: float) -> None:
        """Set target FPS (clamped to min/max)."""
        self._target_fps = max(self.min_fps, min(self.max_fps, value))

    @property
    def frame_interval(self) -> float:
        """Get interval between frames in seconds."""
        return 1.0 / self._target_fps

    def get_quality(self) -> int:
        """Get current quality setting."""
        if self._adaptive_quality:
            return self._adaptive_quality.get_quality()
        return self._fixed_quality

    def set_quality(self, quality: int) -> None:
        """Set quality (disables adaptive quality if enabled)."""
        if self._adaptive_quality:
            self._adaptive_quality.set_quality(quality)
        else:
            self._fixed_quality = max(1, min(100, quality))

    def set_frame_callback(
        self,
        callback: Callable[[str, FrameMetrics], Awaitable[None]],
    ) -> None:
        """
        Set callback for frame delivery.

        Args:
            callback: Async function called with (base64_data, metrics).
        """
        self._frame_callback = callback

    async def start(self) -> None:
        """Start the frame streaming loop."""
        async with self._lock:
            if self._running:
                return

            self._running = True
            self._task = asyncio.create_task(self._capture_loop())
            logger.info(
                "Frame streamer started (fps=%.1f, quality=%d, adaptive=%s)",
                self._target_fps,
                self.get_quality(),
                self._adaptive_quality is not None,
            )

    async def stop(self) -> None:
        """Stop the frame streaming loop."""
        async with self._lock:
            if not self._running:
                return

            self._running = False

            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None

            logger.info("Frame streamer stopped")

    async def _capture_loop(self) -> None:
        """Main capture loop."""
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                frame_start = time.perf_counter()

                # Get current quality
                quality = self.get_quality()

                # Capture frame in thread pool to avoid blocking
                b64_data, metrics = await loop.run_in_executor(
                    None,
                    self.capture.capture_base64,
                    quality,
                    self.scale,
                )

                # Update adaptive quality if enabled
                if self._adaptive_quality:
                    self._adaptive_quality.update(
                        metrics.frame_size_bytes,
                        self.frame_interval,
                    )

                # Deliver frame via callback
                if self._frame_callback:
                    try:
                        await self._frame_callback(b64_data, metrics)
                    except Exception as e:
                        logger.warning("Frame callback error: %s", e)

                # Calculate sleep time to maintain target FPS
                elapsed = time.perf_counter() - frame_start
                sleep_time = max(0, self.frame_interval - elapsed)

                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                elif elapsed > self.frame_interval * 1.5:
                    # Warn if we're falling behind
                    logger.debug(
                        "Frame capture falling behind (%.1fms > %.1fms)",
                        elapsed * 1000,
                        self.frame_interval * 1000,
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Capture loop error: %s", e)
                await asyncio.sleep(0.1)  # Brief pause on error

    async def capture_single_frame(self) -> tuple[str, FrameMetrics]:
        """
        Capture a single frame outside the streaming loop.

        Useful for on-demand screenshots.

        Returns:
            Tuple of (base64_data, metrics).
        """
        loop = asyncio.get_event_loop()
        quality = self.get_quality()

        return await loop.run_in_executor(
            None,
            self.capture.capture_base64,
            quality,
            self.scale,
        )

    def close(self) -> None:
        """Clean up resources."""
        self.capture.close()


def get_all_monitors() -> list[ScreenInfo]:
    """Get information about all available monitors."""
    with mss.mss() as sct:
        monitors = []
        for i, mon in enumerate(sct.monitors[1:], start=1):  # Skip virtual "all" monitor
            monitors.append(ScreenInfo(
                width=mon["width"],
                height=mon["height"],
                left=mon["left"],
                top=mon["top"],
            ))
        return monitors


def get_primary_monitor_info() -> ScreenInfo:
    """Get information about the primary monitor."""
    capture = ScreenCapture(monitor_index=0)
    try:
        return capture.get_screen_info()
    finally:
        capture.close()
