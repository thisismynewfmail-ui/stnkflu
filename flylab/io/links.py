"""Serial ports, pointer control, network publishing, sound and files.

Every adapter has the same shape: a class that opens once, methods that do one
thing, and a ``simulated`` flag that follows the value into the run log.
"""

import json
import math
import subprocess
import time
from pathlib import Path

import numpy as np

from .registry import DeviceUnavailable, probe

BAUD_RATES = (300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)


# --------------------------------------------------------------------- serial
class SerialLink:
    """A serial port, or a loopback stand-in when no port is available."""

    def __init__(self, port, baud=115200, timeout_s=1.0, simulate=False, newline="\n"):
        self.port = str(port)
        self.baud = int(baud)
        self.timeout_s = float(timeout_s)
        self.newline = newline
        self.simulated = False
        self._buffer = []
        self._handle = None
        if self.baud not in BAUD_RATES:
            raise ValueError(f"Unusual baud rate {self.baud}; pick one of {BAUD_RATES}")
        if simulate is True and not self.port:
            self.simulated = True
            self.reason = "simulation requested"
            return
        info = probe("serial")
        try:
            if not info["available"]:
                raise DeviceUnavailable("serial")
            import serial

            self._handle = serial.Serial(
                self.port, self.baud, timeout=self.timeout_s, write_timeout=self.timeout_s
            )
        except Exception as exc:
            if not simulate:
                raise DeviceUnavailable("serial", str(exc)) from None
            self.simulated = True
            self.reason = str(exc)

    def write(self, text, raw=False):
        payload = text if isinstance(text, (bytes, bytearray)) else str(text).encode()
        if not raw:
            payload += self.newline.encode()
        if self.simulated:
            self._buffer.append(payload)
            return {"bytes": len(payload), "simulated": True, "port": self.port}
        written = self._handle.write(payload)
        self._handle.flush()
        return {"bytes": int(written), "simulated": False, "port": self.port}

    def read_line(self, timeout_s=None):
        deadline = time.monotonic() + float(self.timeout_s if timeout_s is None else timeout_s)
        if self.simulated:
            while time.monotonic() < deadline:
                if self._buffer:
                    line = self._buffer.pop(0).decode(errors="replace").strip()
                    return {"line": line, "received": True, "simulated": True}
                time.sleep(0.01)
            return {"line": "", "received": False, "simulated": True}
        self._handle.timeout = max(0.0, deadline - time.monotonic())
        raw = self._handle.readline()
        return {
            "line": raw.decode(errors="replace").strip(),
            "received": bool(raw),
            "simulated": False,
        }

    def read_number(self, timeout_s=None, default=0.0):
        result = self.read_line(timeout_s)
        try:
            result["value"] = float(result["line"].split(",")[0].strip())
            result["parsed"] = True
        except (ValueError, IndexError):
            result["value"] = float(default)
            result["parsed"] = False
        return result

    def state(self):
        return {
            "port": self.port,
            "baud": self.baud,
            "simulated": self.simulated,
            "open": bool(self._handle and getattr(self._handle, "is_open", False)),
            "pending": len(self._buffer),
        }

    def close(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass


# -------------------------------------------------------------------- pointer
class Pointer:
    """Mouse and keyboard output, so the network can drive the computer."""

    def __init__(self, simulate=False):
        self.simulated = False
        self.actions = []
        self.backend = None
        info = probe("pointer")
        if simulate is True and not info["available"]:
            self.simulated = True
            self.reason = "no pointer backend importable"
            return
        try:
            if not info["available"]:
                raise DeviceUnavailable("pointer")
            if info["backend_module"] == "pynput":
                from pynput.keyboard import Controller as Keyboard
                from pynput.mouse import Button, Controller as Mouse

                self._mouse = Mouse()
                self._keyboard = Keyboard()
                self._button = Button
                self.backend = "pynput"
            else:
                import pyautogui

                pyautogui.FAILSAFE = True
                self._auto = pyautogui
                self.backend = "pyautogui"
        except Exception as exc:
            if not simulate:
                raise DeviceUnavailable("pointer", str(exc)) from None
            self.simulated = True
            self.reason = str(exc)

    def _record(self, **event):
        event["at"] = time.time()
        event["simulated"] = self.simulated
        self.actions.append(event)
        del self.actions[:-128]
        return event

    def screen_size(self):
        if self.simulated:
            return (1920, 1080)
        if self.backend == "pyautogui":
            return tuple(self._auto.size())
        try:
            from pynput.mouse import Controller as Mouse

            Mouse()  # touch the backend so a missing display raises here
        except Exception:
            pass
        return (1920, 1080)

    def move_to(self, x, y, relative=False):
        x, y = float(x), float(y)
        if not self.simulated:
            if self.backend == "pynput":
                if relative:
                    self._mouse.move(int(x), int(y))
                else:
                    self._mouse.position = (int(x), int(y))
            else:
                (self._auto.moveRel if relative else self._auto.moveTo)(x, y, duration=0)
        return self._record(action="move", x=x, y=y, relative=bool(relative))

    def click(self, button="left", count=1):
        if button not in ("left", "right", "middle"):
            raise ValueError("Button must be left, right or middle")
        count = max(1, min(5, int(count)))
        if not self.simulated:
            if self.backend == "pynput":
                self._mouse.click(getattr(self._button, button), count)
            else:
                self._auto.click(button=button, clicks=count)
        return self._record(action="click", button=button, count=count)

    def scroll(self, amount):
        amount = int(amount)
        if not self.simulated:
            if self.backend == "pynput":
                self._mouse.scroll(0, amount)
            else:
                self._auto.scroll(amount)
        return self._record(action="scroll", amount=amount)

    def key(self, name, text=None):
        if not self.simulated:
            if self.backend == "pynput":
                if text:
                    self._keyboard.type(str(text))
                else:
                    from pynput.keyboard import Key

                    self._keyboard.tap(getattr(Key, name, name))
            else:
                if text:
                    self._auto.typewrite(str(text))
                else:
                    self._auto.press(name)
        return self._record(action="key", key=name, text=text)


# -------------------------------------------------------------------- network
def http_request(url, method="POST", payload=None, headers=None, timeout_s=5.0):
    """Send a value to a webhook or REST endpoint. Only http/https."""
    import urllib.error
    import urllib.request

    if not str(url).lower().startswith(("http://", "https://")):
        raise ValueError("Only http:// and https:// destinations are allowed")
    method = str(method).upper()
    if method not in ("GET", "POST", "PUT"):
        raise ValueError("Method must be GET, POST or PUT")
    body = None
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    if payload is not None and method != "GET":
        body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_s)) as response:
            text = response.read(65536).decode(errors="replace")
            return {
                "ok": True,
                "status": response.status,
                "body": text,
                "seconds": round(time.monotonic() - started, 4),
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": exc.reason, "seconds": round(time.monotonic() - started, 4)}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": str(exc), "seconds": round(time.monotonic() - started, 4)}


def udp_send(host, port, message):
    """Fire-and-forget datagram, for OSC-style and robot control links."""
    import socket

    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("UDP port must be between 1 and 65535")
    payload = message if isinstance(message, (bytes, bytearray)) else str(message).encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1.0)
        sent = sock.sendto(payload, (str(host), port))
    return {"ok": True, "bytes": int(sent), "host": str(host), "port": port}


class MqttLink:
    def __init__(self, host, port=1883, client_id="flylab", simulate=False, timeout_s=5.0):
        self.host, self.port = str(host), int(port)
        self.simulated = False
        self.published = []
        info = probe("mqtt")
        try:
            if not info["available"]:
                raise DeviceUnavailable("mqtt")
            import paho.mqtt.client as mqtt

            self._client = mqtt.Client(client_id=client_id)
            self._client.connect(self.host, self.port, keepalive=int(timeout_s * 4))
            self._client.loop_start()
        except Exception as exc:
            if not simulate:
                raise DeviceUnavailable("mqtt", str(exc)) from None
            self.simulated = True
            self.reason = str(exc)

    def publish(self, topic, payload, qos=0, retain=False):
        record = {"topic": str(topic), "payload": payload, "qos": int(qos), "retain": bool(retain)}
        if self.simulated:
            self.published.append(record)
            del self.published[:-64]
            return {**record, "ok": True, "simulated": True}
        text = payload if isinstance(payload, str) else json.dumps(payload)
        info = self._client.publish(str(topic), text, qos=int(qos), retain=bool(retain))
        return {**record, "ok": info.rc == 0, "simulated": False}

    def close(self):
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass


# ----------------------------------------------------------------------- sound
class Microphone:
    """Short recordings reduced to band energies for Johnston's organ."""

    def __init__(self, bands=6, sample_rate=16000, simulate=False, device=None):
        self.bands = max(1, min(32, int(bands)))
        self.sample_rate = int(sample_rate)
        self.device = device
        self.simulated = False
        info = probe("audio")
        try:
            if not info["available"]:
                raise DeviceUnavailable("audio")
            import sounddevice

            self._sd = sounddevice
        except Exception as exc:
            if not simulate:
                raise DeviceUnavailable("audio", str(exc)) from None
            self.simulated = True
            self.reason = str(exc)
            self._rng = np.random.default_rng(3)

    def listen(self, seconds=0.1):
        seconds = max(0.01, min(5.0, float(seconds)))
        if self.simulated:
            time.sleep(min(seconds, 0.05))
            wobble = (math.sin(time.monotonic() * 1.7) + 1) / 2
            energies = np.clip(
                self._rng.random(self.bands) * 0.4 + wobble * np.linspace(1, 0.2, self.bands),
                0,
                1,
            )
            return {
                "bands": [round(float(v), 4) for v in energies],
                "level": round(float(energies.mean()), 4),
                "simulated": True,
                "seconds": seconds,
            }
        frames = int(seconds * self.sample_rate)
        audio = self._sd.rec(
            frames, samplerate=self.sample_rate, channels=1, dtype="float32", device=self.device
        )
        self._sd.wait()
        signal = np.asarray(audio).reshape(-1)
        spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
        edges = np.linspace(0, len(spectrum), self.bands + 1).astype(int)
        energies = np.asarray(
            [spectrum[a:b].mean() if b > a else 0.0 for a, b in zip(edges, edges[1:])]
        )
        peak = float(energies.max()) or 1.0
        return {
            "bands": [round(float(v / peak), 4) for v in energies],
            "level": round(float(np.sqrt(np.mean(signal**2))), 5),
            "simulated": False,
            "seconds": seconds,
        }


# ----------------------------------------------------------------------- files
def read_value(path, field=None, default=0.0):
    """Read a number from a text, JSON or single-line CSV file."""
    path = Path(path)
    if not path.exists():
        return {"ok": False, "value": float(default), "reason": "file not found", "path": str(path)}
    text = path.read_text(errors="replace").strip()
    if not text:
        return {"ok": False, "value": float(default), "reason": "file empty", "path": str(path)}
    if field:
        try:
            data = json.loads(text)
            for part in str(field).split("."):
                data = data[part] if not isinstance(data, list) else data[int(part)]
            return {"ok": True, "value": float(data), "path": str(path), "field": field}
        except Exception as exc:
            return {"ok": False, "value": float(default), "reason": str(exc), "path": str(path)}
    first = text.splitlines()[-1].split(",")[0].strip()
    try:
        return {"ok": True, "value": float(first), "path": str(path)}
    except ValueError:
        return {"ok": False, "value": float(default), "reason": "not a number", "path": str(path), "text": first[:80]}


def append_record(path, record, fmt="jsonl"):
    """Append one row to a log. JSONL keeps structure; CSV is flat."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with path.open("a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    elif fmt == "csv":
        exists = path.exists() and path.stat().st_size > 0
        keys = list(record)
        with path.open("a") as handle:
            if not exists:
                handle.write(",".join(keys) + "\n")
            handle.write(
                ",".join(str(record[k]).replace(",", ";") for k in keys) + "\n"
            )
    else:
        raise ValueError("Log format must be jsonl or csv")
    return {"ok": True, "path": str(path), "format": fmt, "bytes": path.stat().st_size}


def run_command(command, timeout_s=10.0, allow=False):
    """Run a local command. Disabled unless a workflow explicitly allows it."""
    if not allow:
        raise PermissionError(
            "Shell output is disabled. Enable 'Allow shell commands' on this"
            " block if you intend a workflow to run programs on this machine."
        )
    if not isinstance(command, list) or not command:
        raise ValueError("Give the command as a list of arguments, not a shell string")
    started = time.monotonic()
    try:
        done = subprocess.run(
            [str(part) for part in command],
            capture_output=True,
            text=True,
            timeout=float(timeout_s),
            check=False,
        )
        return {
            "ok": done.returncode == 0,
            "returncode": done.returncode,
            "stdout": done.stdout[-4096:],
            "stderr": done.stderr[-4096:],
            "seconds": round(time.monotonic() - started, 4),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "timed out", "seconds": float(timeout_s)}
