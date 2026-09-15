"""Device adapters: honest reporting, guards, and labelled simulation."""

import pytest

from flylab.io import gpio, links, registry, vision


def test_every_capability_is_described_and_probed():
    for entry in registry.probe_all():
        assert entry["name"] and entry["plain"] and entry["detail"]
        assert isinstance(entry["available"], bool)
        if not entry["available"]:
            assert entry["install_hint"], f"{entry['key']} must say how to install it"


def test_the_environment_snapshot_is_complete():
    environment = registry.environment()
    assert environment["python"] and environment["platform"]
    assert len(environment["capabilities"]) == len(registry.CAPABILITIES)


# ------------------------------------------------------------------- gpio
def test_only_general_purpose_pins_are_accepted():
    assert gpio.check_pin(17) == 17
    for bad in (0, 1, 28, 99, -3):
        with pytest.raises(ValueError, match="general-purpose"):
            gpio.check_pin(bad)


def test_the_pinout_names_the_physical_position():
    described = gpio.describe_pin(18)
    assert described["physical"] == 12
    assert "hardware PWM" in described["default_use"]
    assert len(gpio.pinout()) == len(gpio.PINOUT)


def test_a_simulated_controller_records_what_it_would_have_done():
    controller = gpio.open_controller(simulate=True)
    assert controller.simulated is True
    assert controller.write(17, True)["simulated"] is True
    assert controller.read(17) is True
    state = controller.state()
    assert state["simulated"] is True
    assert state["pins"]["17"]["value"] is True
    assert state["recent_writes"]


def test_pwm_and_servo_are_clamped_to_their_ranges():
    controller = gpio.open_controller(simulate=True)
    assert controller.pwm(18, 250)["duty_percent"] == 100
    assert controller.pwm(18, -40)["duty_percent"] == 0
    servo = controller.servo(13, 400)
    assert servo["frequency_hz"] == 50
    # 180 degrees is a 2.0 ms pulse in a 20 ms frame.
    assert servo["duty_percent"] == pytest.approx(10.0)
    assert controller.servo(13, -90)["duty_percent"] == pytest.approx(5.0)


def test_a_ping_sensor_needs_two_different_pins():
    controller = gpio.open_controller(simulate=True)
    with pytest.raises(ValueError, match="different pins"):
        controller.ping(23, 23)
    reading = controller.ping(23, 24)
    assert reading["simulated"] is True
    assert 0 < reading["distance_cm"] <= 400


def test_waiting_for_an_edge_reports_whether_it_arrived():
    controller = gpio.open_controller(simulate=True)
    result = controller.wait_for_edge(4, "both", timeout_s=1.5)
    assert set(result) >= {"triggered", "waited_seconds", "simulated"}
    assert result["waited_seconds"] <= 1.6
    with pytest.raises(ValueError):
        controller.wait_for_edge(4, "sideways", timeout_s=0.5)


def test_an_absent_controller_is_refused_unless_simulation_is_asked_for():
    info = registry.probe("gpio")
    if info["available"]:
        pytest.skip("this machine has a GPIO backend")
    with pytest.raises(registry.DeviceUnavailable):
        gpio.open_controller(simulate=False)


# ------------------------------------------------------------------ serial
def test_a_simulated_serial_port_loops_back():
    link = links.SerialLink("", simulate=True)
    assert link.simulated is True
    assert link.write("PING 42")["bytes"] > 0
    line = link.read_line(0.2)
    assert line["line"] == "PING 42" and line["simulated"] is True
    assert link.read_line(0.05)["received"] is False


def test_an_unusual_baud_rate_is_refused():
    with pytest.raises(ValueError, match="baud"):
        links.SerialLink("", baud=1234, simulate=True)


def test_an_unparseable_line_falls_back_rather_than_raising():
    link = links.SerialLink("", simulate=True)
    link.write("not a number")
    result = link.read_number(0.2, default=-1)
    assert result["parsed"] is False and result["value"] == -1


# ----------------------------------------------------------------- pointer
def test_a_simulated_pointer_records_instead_of_moving():
    pointer = links.Pointer(simulate=True)
    if not pointer.simulated:
        pytest.skip("this machine has a real pointer backend")
    pointer.move_to(100, 200)
    pointer.click("left", 2)
    assert [a["action"] for a in pointer.actions] == ["move", "click"]
    assert all(a["simulated"] for a in pointer.actions)
    with pytest.raises(ValueError):
        pointer.click("sideways")


# ----------------------------------------------------------------- network
def test_only_http_destinations_are_allowed():
    for bad in ("file:///etc/passwd", "ftp://host/x", "javascript:alert(1)"):
        with pytest.raises(ValueError, match="http"):
            links.http_request(bad)


def test_a_udp_port_must_be_in_range():
    with pytest.raises(ValueError, match="port"):
        links.udp_send("127.0.0.1", 99999, "x")


def test_shell_commands_are_refused_without_explicit_permission():
    with pytest.raises(PermissionError, match="disabled"):
        links.run_command(["echo", "hi"])
    with pytest.raises(ValueError, match="list of arguments"):
        links.run_command("echo hi", allow=True)
    result = links.run_command(["echo", "hi"], allow=True)
    assert result["ok"] and result["stdout"].strip() == "hi"


# ------------------------------------------------------------------ vision
def test_a_capture_source_falls_back_only_when_simulation_is_allowed():
    source = vision.open_source("camera", simulate=True)
    frame, info = source.read()
    assert frame.shape[2] == 3 and frame.dtype.name == "uint8"
    assert info["simulated"] is True and info["reason"]
    with pytest.raises(Exception):
        vision.open_source("camera", simulate=False, index=97)


def test_the_test_pattern_changes_over_time():
    first = vision.test_pattern((64, 36), phase=0.0)
    later = vision.test_pattern((64, 36), phase=0.5)
    assert first.shape == (36, 64, 3)
    assert not (first == later).all()


def test_resizing_keeps_the_frame_valid():
    frame = vision.test_pattern((80, 40))
    smaller = vision.resize(frame, (20, 10))
    assert smaller.shape == (10, 20, 3)
    with pytest.raises(ValueError):
        vision.resize(frame, (0, 10))


def test_a_frame_encodes_to_png():
    assert vision.to_png(vision.test_pattern((32, 18))).startswith(b"\x89PNG")


def test_an_audio_buffer_reduces_to_bands():
    microphone = links.Microphone(bands=6, simulate=True)
    if not microphone.simulated:
        pytest.skip("this machine has a real microphone backend")
    reading = microphone.listen(0.05)
    assert len(reading["bands"]) == 6
    assert all(0 <= b <= 1 for b in reading["bands"])
    assert reading["simulated"] is True
