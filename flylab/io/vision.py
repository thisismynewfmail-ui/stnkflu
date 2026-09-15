"""Getting pictures into the network: screen, camera, files, test patterns.

Everything returns an HxWx3 uint8 RGB array, which is what the photoreceptor
channels sample. Capture size is decoupled from what the eye sees: the sampler
reads one pixel per mapped receptor at its inferred column, so a large capture
is not more information to the network, only more work for the CPU.
"""

import math
import time
from pathlib import Path

import numpy as np

from .registry import DeviceUnavailable, probe

DEFAULT_SIZE = (320, 180)


def _as_rgb(array):
    array = np.asarray(array)
    if array.ndim == 2:
        array = np.stack([array] * 3, axis=-1)
    if array.ndim != 3:
        raise ValueError("Expected a 2D or 3D image array")
    if array.shape[2] == 4:
        array = array[:, :, :3]
    if array.shape[2] != 3:
        raise ValueError("Expected 3 or 4 colour channels")
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(array)


def resize(frame, size):
    """Nearest-neighbour resize; no external image library required."""
    width, height = int(size[0]), int(size[1])
    if width < 1 or height < 1:
        raise ValueError("Capture size must be at least 1x1")
    h, w = frame.shape[:2]
    if (w, h) == (width, height):
        return frame
    ys = np.minimum((np.arange(height) * h) // height, h - 1)
    xs = np.minimum((np.arange(width) * w) // width, w - 1)
    return np.ascontiguousarray(frame[ys][:, xs])


def test_pattern(size=DEFAULT_SIZE, phase=0.0, label="SIMULATED"):
    """A moving pattern, used whenever a capture device is simulated."""
    width, height = int(size[0]), int(size[1])
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    drift = float(phase) % 1.0
    bars = 0.5 + 0.5 * np.sin(2 * math.pi * (6 * x + drift))
    rings = 0.5 + 0.5 * np.sin(
        2 * math.pi * (8 * np.sqrt((x - 0.5) ** 2 + (y - 0.5) ** 2) - drift)
    )
    frame = np.zeros((height, width, 3), dtype=np.float32)
    frame[:, :, 0] = bars * (1 - y)
    frame[:, :, 1] = rings
    frame[:, :, 2] = (0.4 + 0.6 * y) * (1 - bars * 0.5)
    frame = (np.clip(frame, 0, 1) * 235 + 12).astype(np.uint8)
    # A corner block keeps the simulated origin visible in the preview.
    frame[: max(2, height // 12), : max(2, width // 12)] = (255, 96, 0)
    return frame


class Source:
    """Base class: ``read()`` returns (frame, info)."""

    kind = "source"
    simulated = False

    def read(self):
        raise NotImplementedError

    def close(self):
        pass

    def describe(self):
        return {"kind": self.kind, "simulated": self.simulated}


class PatternSource(Source):
    kind = "pattern"
    simulated = True

    def __init__(self, size=DEFAULT_SIZE, speed=0.25, reason="simulation requested"):
        self.size = size
        self.speed = float(speed)
        self.reason = reason
        self.start = time.monotonic()

    def read(self):
        phase = (time.monotonic() - self.start) * self.speed
        frame = test_pattern(self.size, phase)
        return frame, {
            "kind": self.kind,
            "simulated": True,
            "reason": self.reason,
            "size": list(frame.shape[1::-1]),
            "phase": round(phase % 1.0, 4),
        }


class ScreenSource(Source):
    kind = "screen"

    def __init__(self, region=None, size=DEFAULT_SIZE, monitor=1):
        self.region = tuple(region) if region else None
        self.size = size
        self.monitor = int(monitor)
        self._grab = None
        info = probe("screen")
        if not info["available"]:
            raise DeviceUnavailable("screen")
        self.backend = info["backend_module"]

    def _open(self):
        if self._grab is not None:
            return self._grab
        if self.backend == "mss":
            import mss

            self._sct = mss.mss()
            monitors = self._sct.monitors
            box = monitors[min(self.monitor, len(monitors) - 1)]
            if self.region:
                left, top, width, height = self.region
                box = {
                    "left": box["left"] + int(left),
                    "top": box["top"] + int(top),
                    "width": int(width),
                    "height": int(height),
                }
            self._box = box
            self._grab = lambda: np.asarray(self._sct.grab(self._box))[:, :, :3][:, :, ::-1]
        else:
            from PIL import ImageGrab

            box = tuple(
                (self.region[0], self.region[1],
                 self.region[0] + self.region[2], self.region[1] + self.region[3])
            ) if self.region else None
            self._grab = lambda: np.asarray(ImageGrab.grab(bbox=box).convert("RGB"))
        return self._grab

    def read(self):
        try:
            frame = _as_rgb(self._open()())
        except Exception as exc:  # a locked screen, no display, denied permission
            raise DeviceUnavailable("screen", f"Capture failed: {exc}") from None
        frame = resize(frame, self.size)
        return frame, {
            "kind": self.kind,
            "simulated": False,
            "backend": self.backend,
            "region": list(self.region) if self.region else "full monitor",
            "size": list(frame.shape[1::-1]),
        }

    def close(self):
        sct = getattr(self, "_sct", None)
        if sct is not None:
            sct.close()


class CameraSource(Source):
    kind = "camera"

    def __init__(self, index=0, size=DEFAULT_SIZE):
        self.index = int(index)
        self.size = size
        info = probe("camera")
        if not info["available"]:
            raise DeviceUnavailable("camera")
        import cv2

        self._cv2 = cv2
        self._capture = cv2.VideoCapture(self.index)
        if not self._capture.isOpened():
            raise DeviceUnavailable("camera", f"Camera {self.index} did not open.")

    def read(self):
        ok, frame = self._capture.read()
        if not ok:
            raise DeviceUnavailable("camera", "The camera returned no frame.")
        frame = resize(_as_rgb(frame[:, :, ::-1]), self.size)
        return frame, {
            "kind": self.kind,
            "simulated": False,
            "index": self.index,
            "size": list(frame.shape[1::-1]),
        }

    def close(self):
        capture = getattr(self, "_capture", None)
        if capture is not None:
            capture.release()


class FileSource(Source):
    kind = "file"

    def __init__(self, path, size=DEFAULT_SIZE, loop=True):
        self.path = Path(path)
        self.size = size
        self.loop = bool(loop)
        self.files = self._collect()
        if not self.files:
            raise FileNotFoundError(f"No image found at {self.path}")
        self.position = 0

    def _collect(self):
        suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
        if self.path.is_dir():
            return sorted(p for p in self.path.iterdir() if p.suffix.lower() in suffixes)
        return [self.path] if self.path.exists() else []

    def read(self):
        from PIL import Image

        if self.position >= len(self.files):
            if not self.loop:
                raise StopIteration("End of image sequence")
            self.position = 0
        path = self.files[self.position]
        self.position += 1
        with Image.open(path) as image:
            frame = _as_rgb(np.asarray(image.convert("RGB")))
        frame = resize(frame, self.size)
        return frame, {
            "kind": self.kind,
            "simulated": False,
            "path": str(path),
            "index": self.position - 1,
            "count": len(self.files),
            "size": list(frame.shape[1::-1]),
        }


class VideoSource(Source):
    kind = "video"

    def __init__(self, path, size=DEFAULT_SIZE, loop=True):
        self.path = str(path)
        self.size = size
        self.loop = bool(loop)
        info = probe("camera")
        if not info["available"]:
            raise DeviceUnavailable("camera", "Video files need OpenCV.")
        import cv2

        self._cv2 = cv2
        self._capture = cv2.VideoCapture(self.path)
        if not self._capture.isOpened():
            raise FileNotFoundError(f"Could not open video {self.path}")
        self.frames = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def read(self):
        ok, frame = self._capture.read()
        if not ok:
            if not self.loop:
                raise StopIteration("End of video")
            self._capture.set(self._cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._capture.read()
            if not ok:
                raise RuntimeError("Video could not be rewound")
        out = resize(_as_rgb(frame[:, :, ::-1]), self.size)
        return out, {
            "kind": self.kind,
            "simulated": False,
            "path": self.path,
            "position": int(self._capture.get(self._cv2.CAP_PROP_POS_FRAMES)),
            "frames": self.frames,
            "size": list(out.shape[1::-1]),
        }

    def close(self):
        capture = getattr(self, "_capture", None)
        if capture is not None:
            capture.release()


class BrowserSource(Source):
    kind = "browser"

    def __init__(self, url, size=(1280, 720), output=DEFAULT_SIZE, wait_ms=800):
        self.url = str(url)
        self.size = size
        self.output = output
        self.wait_ms = int(wait_ms)
        info = probe("browser")
        if not info["available"]:
            raise DeviceUnavailable("browser")
        from playwright.sync_api import sync_playwright

        self._context = sync_playwright().start()
        self._browser = self._context.chromium.launch()
        self._page = self._browser.new_page(
            viewport={"width": int(size[0]), "height": int(size[1])}
        )
        self._loaded = None

    def read(self):
        if self._loaded != self.url:
            self._page.goto(self.url, wait_until="load")
            self._loaded = self.url
        self._page.wait_for_timeout(self.wait_ms)
        import io

        from PIL import Image

        shot = self._page.screenshot(type="png")
        with Image.open(io.BytesIO(shot)) as image:
            frame = _as_rgb(np.asarray(image.convert("RGB")))
        frame = resize(frame, self.output)
        return frame, {
            "kind": self.kind,
            "simulated": False,
            "url": self.url,
            "size": list(frame.shape[1::-1]),
        }

    def close(self):
        for attribute in ("_browser", "_context"):
            handle = getattr(self, attribute, None)
            if handle is not None:
                try:
                    handle.stop() if attribute == "_context" else handle.close()
                except Exception:
                    pass


def open_source(kind, *, simulate=False, **options):
    """Open a capture source, falling back to a pattern only when told to."""
    size = tuple(options.pop("size", DEFAULT_SIZE))
    builders = {
        "screen": lambda: ScreenSource(options.get("region"), size, options.get("monitor", 1)),
        "camera": lambda: CameraSource(options.get("index", 0), size),
        "image": lambda: FileSource(options.get("path", ""), size, options.get("loop", True)),
        "video": lambda: VideoSource(options.get("path", ""), size, options.get("loop", True)),
        "browser": lambda: BrowserSource(
            options.get("url", "about:blank"),
            tuple(options.get("viewport", (1280, 720))),
            size,
            options.get("wait_ms", 800),
        ),
        "pattern": lambda: PatternSource(size, options.get("speed", 0.25), "test pattern selected"),
    }
    if kind not in builders:
        raise ValueError(f"Unknown vision source: {kind}")
    if kind == "pattern":
        return builders[kind]()
    try:
        return builders[kind]()
    except Exception as exc:
        if not simulate:
            raise
        return PatternSource(size, options.get("speed", 0.25), f"{kind} unavailable: {exc}")


def to_png(frame):
    """Encode a frame as PNG bytes for the interface preview."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(_as_rgb(frame)).save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()
