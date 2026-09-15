"""Raspberry Pi GPIO: pins in, pins out, PWM, servos and ping sensors.

Pin numbers are BCM (the GPIO numbers printed in pinout diagrams), not board
positions, because every supported backend speaks BCM. The board layout is
published in ``PINOUT`` so the interface can show which physical pin a number
lands on and refuse the ones that are not general-purpose.

A simulated controller keeps pin state in memory and records every write. It
exists so a workflow can be built and stepped through on a laptop; results
from it are marked ``simulated`` everywhere they appear.
"""

import threading
import time

from .registry import DeviceUnavailable, probe

# BCM number -> (physical pin, what the pin is normally used for)
PINOUT = {
    2: (3, "I2C SDA"), 3: (5, "I2C SCL"), 4: (7, "general purpose"),
    5: (29, "general purpose"), 6: (31, "general purpose"),
    7: (26, "SPI CE1"), 8: (24, "SPI CE0"), 9: (21, "SPI MISO"),
    10: (19, "SPI MOSI"), 11: (23, "SPI SCLK"), 12: (32, "hardware PWM0"),
    13: (33, "hardware PWM1"), 14: (8, "UART TX"), 15: (10, "UART RX"),
    16: (36, "general purpose"), 17: (11, "general purpose"),
    18: (12, "hardware PWM0"), 19: (35, "hardware PWM1"),
    20: (38, "general purpose"), 21: (40, "general purpose"),
    22: (15, "general purpose"), 23: (16, "general purpose"),
    24: (18, "general purpose"), 25: (22, "general purpose"),
    26: (37, "general purpose"), 27: (13, "general purpose"),
}

PULL = ("none", "up", "down")
EDGES = ("rising", "falling", "both")
SPEED_OF_SOUND_CM_S = 34300.0  # dry air at about 20 C


def check_pin(pin):
    pin = int(pin)
    if pin not in PINOUT:
        raise ValueError(
            f"BCM {pin} is not a general-purpose pin on a 40-pin header. "
            f"Valid pins: {sorted(PINOUT)}"
        )
    return pin


def check_ping_pins(trigger_pin, echo_pin):
    """An ultrasonic module needs a separate trigger and echo line."""
    trigger_pin, echo_pin = check_pin(trigger_pin), check_pin(echo_pin)
    if trigger_pin == echo_pin:
        raise ValueError("Trigger and echo must be different pins")
    return trigger_pin, echo_pin


def describe_pin(pin):
    physical, purpose = PINOUT[check_pin(pin)]
    return {
        "bcm": int(pin),
        "physical": physical,
        "default_use": purpose,
        "label": f"BCM {pin} (header pin {physical})",
    }


def pinout():
    return [describe_pin(p) for p in sorted(PINOUT)]


class Controller:
    """Common interface for every GPIO backend."""

    backend = "none"
    simulated = False

    def setup_output(self, pin, initial=False):
        raise NotImplementedError

    def setup_input(self, pin, pull="none"):
        raise NotImplementedError

    def write(self, pin, value):
        raise NotImplementedError

    def read(self, pin):
        raise NotImplementedError

    def pwm(self, pin, duty, frequency=1000.0):
        raise NotImplementedError

    def servo(self, pin, angle):
        """Map 0-180 degrees onto the usual 1.0-2.0 ms pulse at 50 Hz."""
        angle = max(0.0, min(180.0, float(angle)))
        pulse_ms = 1.0 + angle / 180.0
        return self.pwm(pin, duty=pulse_ms / 20.0 * 100.0, frequency=50.0)

    def wait_for_edge(self, pin, edge="rising", timeout_s=5.0, pull="none"):
        """Block until a pin changes, or the timeout expires.

        Returns a dict with ``triggered`` and ``waited_seconds``. Polled at
        1 kHz by the fallback implementation, which is fast enough for
        buttons and end-stops but not for counting fast pulse trains.
        """
        if edge not in EDGES:
            raise ValueError(f"Edge must be one of {EDGES}")
        timeout_s = float(timeout_s)
        if not 0 < timeout_s <= 3600:
            raise ValueError("Edge timeout must be between 0 and 3600 seconds")
        self.setup_input(pin, pull)
        start = time.monotonic()
        previous = self.read(pin)
        while time.monotonic() - start < timeout_s:
            current = self.read(pin)
            if current != previous:
                rising = current and not previous
                if edge == "both" or (edge == "rising") == bool(rising):
                    return {
                        "triggered": True,
                        "value": bool(current),
                        "edge": "rising" if rising else "falling",
                        "waited_seconds": round(time.monotonic() - start, 6),
                        "simulated": self.simulated,
                    }
                previous = current
            else:
                time.sleep(0.001)
                previous = current
        return {
            "triggered": False,
            "value": bool(self.read(pin)),
            "edge": None,
            "waited_seconds": round(time.monotonic() - start, 6),
            "simulated": self.simulated,
        }

    def ping(self, trigger_pin, echo_pin, timeout_s=0.06, max_distance_cm=400.0):
        """HC-SR04 style ultrasonic range finder.

        Sends a 10 microsecond pulse on the trigger pin and times how long the
        echo pin stays high. Distance is half the round trip at the speed of
        sound. Returns ``None`` distance on timeout, which is what an open
        space or a missed echo looks like.
        """
        trigger_pin, echo_pin = check_ping_pins(trigger_pin, echo_pin)
        self.setup_output(trigger_pin, False)
        self.setup_input(echo_pin, "none")
        self.write(trigger_pin, False)
        time.sleep(0.000002)
        self.write(trigger_pin, True)
        time.sleep(0.00001)
        self.write(trigger_pin, False)
        start = time.perf_counter()
        while not self.read(echo_pin):
            if time.perf_counter() - start > timeout_s:
                return self._ping_result(None, "no echo started", timeout_s)
        rise = time.perf_counter()
        while self.read(echo_pin):
            if time.perf_counter() - rise > timeout_s:
                return self._ping_result(None, "echo never fell", timeout_s)
        elapsed = time.perf_counter() - rise
        distance = elapsed * SPEED_OF_SOUND_CM_S / 2
        if distance > max_distance_cm:
            return self._ping_result(None, "beyond maximum range", elapsed)
        return self._ping_result(distance, "ok", elapsed)

    def _ping_result(self, distance, status, elapsed):
        return {
            "distance_cm": None if distance is None else round(float(distance), 2),
            "status": status,
            "echo_seconds": round(float(elapsed), 6),
            "simulated": self.simulated,
        }

    def state(self):
        return {}

    def close(self):
        pass


class SimulatedController(Controller):
    backend = "simulated"
    simulated = True

    def __init__(self, reason="simulation requested"):
        self.reason = reason
        self.pins = {}
        self.writes = []
        self.lock = threading.Lock()
        self._opened = time.monotonic()

    def _pin(self, pin, mode, pull="none"):
        pin = check_pin(pin)
        with self.lock:
            entry = self.pins.setdefault(
                pin, {"mode": mode, "value": False, "pull": pull, "duty": None, "frequency": None}
            )
            entry["mode"] = mode
            entry["pull"] = pull
        return pin

    def setup_output(self, pin, initial=False):
        pin = self._pin(pin, "output")
        self.pins[pin]["value"] = bool(initial)
        return pin

    def setup_input(self, pin, pull="none"):
        if pull not in PULL:
            raise ValueError(f"Pull must be one of {PULL}")
        pin = self._pin(pin, "input", pull)
        if pull == "up":
            self.pins[pin]["value"] = True
        return pin

    def write(self, pin, value):
        pin = self._pin(pin, "output")
        with self.lock:
            self.pins[pin]["value"] = bool(value)
            self.writes.append((time.monotonic(), pin, bool(value)))
            del self.writes[:-256]
        return {"pin": pin, "value": bool(value), "simulated": True}

    def read(self, pin):
        pin = check_pin(pin)
        entry = self.pins.get(pin)
        if entry is None:
            return False
        if entry["mode"] == "input":
            # A slow square wave, so an edge-waiting block completes instead of
            # hanging. Clearly synthetic, and reported as such.
            return bool(int((time.monotonic() - self._opened) * 2) % 2)
        return bool(entry["value"])

    def pwm(self, pin, duty, frequency=1000.0):
        pin = self._pin(pin, "pwm")
        duty = max(0.0, min(100.0, float(duty)))
        with self.lock:
            self.pins[pin].update(duty=duty, frequency=float(frequency), value=duty > 0)
        return {"pin": pin, "duty_percent": duty, "frequency_hz": float(frequency), "simulated": True}

    def ping(self, trigger_pin, echo_pin, timeout_s=0.06, max_distance_cm=400.0):
        check_ping_pins(trigger_pin, echo_pin)
        sweep = (time.monotonic() - self._opened) % 8.0
        distance = 15.0 + 60.0 * abs(1 - sweep / 4.0)
        return self._ping_result(distance, "ok", distance * 2 / SPEED_OF_SOUND_CM_S)

    def state(self):
        return {
            "backend": self.backend,
            "simulated": True,
            "reason": self.reason,
            "pins": {str(k): dict(v) for k, v in sorted(self.pins.items())},
            "recent_writes": [
                {"pin": p, "value": v, "seconds_ago": round(time.monotonic() - t, 3)}
                for t, p, v in self.writes[-16:]
            ],
        }


class GpiozeroController(Controller):
    backend = "gpiozero"

    def __init__(self):
        from gpiozero import DigitalInputDevice, DigitalOutputDevice, PWMOutputDevice

        self._out = DigitalOutputDevice
        self._in = DigitalInputDevice
        self._pwm = PWMOutputDevice
        self.devices = {}

    def _device(self, pin, factory, **kwargs):
        pin = check_pin(pin)
        existing = self.devices.get(pin)
        if existing is not None and isinstance(existing, factory):
            return existing
        if existing is not None:
            existing.close()
        device = factory(pin, **kwargs)
        self.devices[pin] = device
        return device

    def setup_output(self, pin, initial=False):
        device = self._device(pin, self._out, initial_value=bool(initial))
        return check_pin(pin)

    def setup_input(self, pin, pull="none"):
        if pull not in PULL:
            raise ValueError(f"Pull must be one of {PULL}")
        self._device(pin, self._in, pull_up={"up": True, "down": False, "none": None}[pull])
        return check_pin(pin)

    def write(self, pin, value):
        device = self._device(pin, self._out)
        device.value = 1 if value else 0
        return {"pin": check_pin(pin), "value": bool(value), "simulated": False}

    def read(self, pin):
        device = self.devices.get(check_pin(pin))
        if device is None or isinstance(device, self._out):
            device = self._device(pin, self._in, pull_up=None)
        return bool(device.value)

    def pwm(self, pin, duty, frequency=1000.0):
        device = self._device(pin, self._pwm, frequency=float(frequency))
        device.frequency = float(frequency)
        duty = max(0.0, min(100.0, float(duty)))
        device.value = duty / 100.0
        return {
            "pin": check_pin(pin),
            "duty_percent": duty,
            "frequency_hz": float(frequency),
            "simulated": False,
        }

    def state(self):
        return {
            "backend": self.backend,
            "simulated": False,
            "pins": {
                str(pin): {"type": type(device).__name__, "value": float(device.value)}
                for pin, device in sorted(self.devices.items())
            },
        }

    def close(self):
        for device in self.devices.values():
            try:
                device.close()
            except Exception:
                pass
        self.devices.clear()


class RpiGpioController(Controller):
    backend = "RPi.GPIO"

    def __init__(self):
        import RPi.GPIO as GPIO

        self.GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        self.modes = {}
        self.pwms = {}

    def setup_output(self, pin, initial=False):
        pin = check_pin(pin)
        if self.modes.get(pin) != "output":
            self.GPIO.setup(pin, self.GPIO.OUT, initial=self.GPIO.HIGH if initial else self.GPIO.LOW)
            self.modes[pin] = "output"
        return pin

    def setup_input(self, pin, pull="none"):
        if pull not in PULL:
            raise ValueError(f"Pull must be one of {PULL}")
        pin = check_pin(pin)
        mapping = {
            "none": self.GPIO.PUD_OFF,
            "up": self.GPIO.PUD_UP,
            "down": self.GPIO.PUD_DOWN,
        }
        if self.modes.get(pin) != f"input:{pull}":
            self.GPIO.setup(pin, self.GPIO.IN, pull_up_down=mapping[pull])
            self.modes[pin] = f"input:{pull}"
        return pin

    def write(self, pin, value):
        pin = self.setup_output(pin)
        self.GPIO.output(pin, self.GPIO.HIGH if value else self.GPIO.LOW)
        return {"pin": pin, "value": bool(value), "simulated": False}

    def read(self, pin):
        pin = check_pin(pin)
        if not str(self.modes.get(pin, "")).startswith("input"):
            self.setup_input(pin)
        return bool(self.GPIO.input(pin))

    def pwm(self, pin, duty, frequency=1000.0):
        pin = self.setup_output(pin)
        duty = max(0.0, min(100.0, float(duty)))
        handle = self.pwms.get(pin)
        if handle is None:
            handle = self.GPIO.PWM(pin, float(frequency))
            handle.start(duty)
            self.pwms[pin] = handle
        else:
            handle.ChangeFrequency(float(frequency))
            handle.ChangeDutyCycle(duty)
        return {
            "pin": pin,
            "duty_percent": duty,
            "frequency_hz": float(frequency),
            "simulated": False,
        }

    def wait_for_edge(self, pin, edge="rising", timeout_s=5.0, pull="none"):
        pin = self.setup_input(pin, pull)
        mapping = {
            "rising": self.GPIO.RISING,
            "falling": self.GPIO.FALLING,
            "both": self.GPIO.BOTH,
        }
        start = time.monotonic()
        result = self.GPIO.wait_for_edge(
            pin, mapping[edge], timeout=int(float(timeout_s) * 1000)
        )
        return {
            "triggered": result is not None,
            "value": bool(self.GPIO.input(pin)),
            "edge": edge if result is not None else None,
            "waited_seconds": round(time.monotonic() - start, 6),
            "simulated": False,
        }

    def state(self):
        return {"backend": self.backend, "simulated": False, "pins": dict(self.modes)}

    def close(self):
        for handle in self.pwms.values():
            try:
                handle.stop()
            except Exception:
                pass
        self.pwms.clear()
        try:
            self.GPIO.cleanup()
        except Exception:
            pass


class LgpioController(Controller):
    backend = "lgpio"

    def __init__(self, chip=0):
        import lgpio

        self.lgpio = lgpio
        self.handle = lgpio.gpiochip_open(int(chip))
        self.claimed = {}

    def setup_output(self, pin, initial=False):
        pin = check_pin(pin)
        if self.claimed.get(pin) != "output":
            self._release(pin)
            self.lgpio.gpio_claim_output(self.handle, pin, int(bool(initial)))
            self.claimed[pin] = "output"
        return pin

    def setup_input(self, pin, pull="none"):
        if pull not in PULL:
            raise ValueError(f"Pull must be one of {PULL}")
        pin = check_pin(pin)
        if self.claimed.get(pin) != f"input:{pull}":
            self._release(pin)
            flags = {
                "none": 0,
                "up": self.lgpio.SET_PULL_UP,
                "down": self.lgpio.SET_PULL_DOWN,
            }[pull]
            self.lgpio.gpio_claim_input(self.handle, pin, flags)
            self.claimed[pin] = f"input:{pull}"
        return pin

    def _release(self, pin):
        if pin in self.claimed:
            try:
                self.lgpio.gpio_free(self.handle, pin)
            except Exception:
                pass
            self.claimed.pop(pin, None)

    def write(self, pin, value):
        pin = self.setup_output(pin)
        self.lgpio.gpio_write(self.handle, pin, int(bool(value)))
        return {"pin": pin, "value": bool(value), "simulated": False}

    def read(self, pin):
        pin = check_pin(pin)
        if not str(self.claimed.get(pin, "")).startswith("input"):
            self.setup_input(pin)
        return bool(self.lgpio.gpio_read(self.handle, pin))

    def pwm(self, pin, duty, frequency=1000.0):
        pin = self.setup_output(pin)
        duty = max(0.0, min(100.0, float(duty)))
        self.lgpio.tx_pwm(self.handle, pin, float(frequency), duty)
        return {
            "pin": pin,
            "duty_percent": duty,
            "frequency_hz": float(frequency),
            "simulated": False,
        }

    def state(self):
        return {"backend": self.backend, "simulated": False, "pins": dict(self.claimed)}

    def close(self):
        for pin in list(self.claimed):
            self._release(pin)
        try:
            self.lgpio.gpiochip_close(self.handle)
        except Exception:
            pass


_CONTROLLERS = {
    "gpiozero": GpiozeroController,
    "lgpio": LgpioController,
    "RPi.GPIO": RpiGpioController,
}


def open_controller(simulate=False, prefer=None):
    """Open the best available GPIO backend, or a simulated one if allowed."""
    if simulate is True and prefer is None:
        return SimulatedController("simulation requested")
    info = probe("gpio")
    order = ([prefer] if prefer else []) + [
        b["module"] for b in info["backends"] if b["importable"]
    ]
    errors = []
    for module in order:
        factory = _CONTROLLERS.get(module)
        if factory is None:
            continue
        try:
            return factory()
        except Exception as exc:
            errors.append(f"{module}: {exc}")
    if simulate:
        reason = "; ".join(errors) or "no GPIO backend importable on this machine"
        return SimulatedController(reason)
    raise DeviceUnavailable("gpio", " ".join(errors))
