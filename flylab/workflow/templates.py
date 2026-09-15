"""Ready-made workflows, so a new project starts from something that runs.

Each template is built with the same API the editor uses, so what you load is
an ordinary workflow you can take apart. The descriptions say what the
workflow does and what hardware, if any, it expects.
"""

from .graph import Workflow

TEMPLATES = {}


def template(key, name, plain, detail, needs=(), tags=()):
    def wrap(builder):
        TEMPLATES[key] = {
            "key": key, "name": name, "plain": plain, "detail": detail,
            "needs": list(needs), "tags": list(tags), "build": builder,
        }
        return builder

    return wrap


def catalogue():
    return [
        {k: v for k, v in entry.items() if k != "build"}
        for entry in TEMPLATES.values()
    ]


def build(key):
    if key not in TEMPLATES:
        raise KeyError(f"Unknown template: {key}")
    workflow = TEMPLATES[key]["build"]()
    workflow.meta["template"] = key
    return workflow


@template(
    "empty", "Empty workflow", "Just a Start block",
    "A blank canvas with the one block every workflow needs. Build from here.",
)
def _empty():
    workflow = Workflow("New workflow")
    workflow.add("flow.start", 80, 200)
    return workflow


@template(
    "bench", "Bench check", "Show a test pattern to the eyes and watch the network respond",
    "Needs no hardware at all. A drifting test pattern goes into the"
    " photoreceptors, the network runs for half a second of neural time, and"
    " the descending and memory readouts are shown live. Run this first to"
    " confirm the whole path works before wiring anything up.",
    tags=("no hardware", "first run"),
)
def _bench():
    workflow = Workflow("Bench check")
    start = workflow.add("flow.start", 60, 240)
    goal = workflow.add("goal.define", 240, 60, params={
        "goal": "Confirm that a picture reaches the photoreceptors and that the"
                " network produces output.",
        "success": "Descending cells fire at all.",
        "failure": "Nothing fires anywhere.",
    })
    pattern = workflow.add("vision.pattern", 240, 240)
    eye = workflow.add("encode.eye", 470, 240)
    step = workflow.add("brain.step", 690, 240, params={"duration": 500.0, "learning": False})
    steering = workflow.add("decode.difference", 930, 120, params={"channel": "motor.steering"})
    descending = workflow.add("decode.rate", 930, 330, params={"channel": "motor.descending"})
    memory = workflow.add("decode.memory", 930, 530)
    show_steer = workflow.add("output.display", 1200, 120, params={
        "label": "Steering (right minus left)", "style": "meter",
        "minimum": -40, "maximum": 40, "unit": "Hz"})
    show_rate = workflow.add("output.display", 1200, 330, params={
        "label": "Descending neurons", "style": "gauge", "minimum": 0, "maximum": 60, "unit": "Hz"})
    show_memory = workflow.add("output.display", 1200, 530, params={
        "label": "Synapses changed", "style": "number", "minimum": 0, "maximum": 1000})
    workflow.connect(start.id, "next", goal.id)
    workflow.connect(goal.id, "next", eye.id)
    workflow.connect(eye.id, "next", step.id)
    workflow.connect(step.id, "next", show_steer.id)
    workflow.connect(show_steer.id, "next", show_rate.id)
    workflow.connect(show_rate.id, "next", show_memory.id)
    workflow.connect(pattern.id, "frame", eye.id, "frame")
    workflow.connect(step.id, "spikes", steering.id, "spikes")
    workflow.connect(step.id, "spikes", descending.id, "spikes")
    workflow.connect(steering.id, "value", show_steer.id, "value")
    workflow.connect(descending.id, "hz", show_rate.id, "value")
    workflow.connect(memory.id, "changed", show_memory.id, "value")
    return workflow


@template(
    "steer", "Look and steer", "Camera in, steering command out",
    "A camera frame goes into the eyes, the network runs, and the left-right"
    " difference across the DNa02 steering pair is rescaled into a servo angle"
    " and a motor speed. The commit gate means nothing moves unless the"
    " network actually fired. Swap the camera for a screen region to drive a"
    " window instead of a robot.",
    needs=("camera", "gpio"), tags=("robot", "hardware"),
)
def _steer():
    workflow = Workflow("Look and steer")
    start = workflow.add("flow.start", 60, 300)
    goal = workflow.add("goal.define", 230, 60, params={
        "goal": "Turn toward whatever the camera is pointed at, using the fly's"
                " own steering pathway.",
        "success": "The servo follows the side of the scene with more going on.",
        "failure": "The servo sits at one extreme regardless of the scene.",
    })
    camera = workflow.add("vision.camera", 230, 300)
    eye = workflow.add("encode.eye", 450, 300)
    step = workflow.add("brain.step", 650, 300, params={"duration": 300.0, "learning": False})
    gate = workflow.add("decode.gate", 880, 440, params={"channel": "motor.gate", "minimum": 1})
    steering = workflow.add("decode.difference", 880, 180, params={"channel": "motor.steering", "scale": 1.0})
    smooth = workflow.add("logic.smooth", 1080, 180, params={"window": 4})
    angle = workflow.add("logic.map", 1270, 120, params={
        "in_low": -20, "in_high": 20, "out_low": 30, "out_high": 150, "clamp": True})
    servo = workflow.add("output.servo", 1470, 120, params={"pin": 13, "minimum": 30, "maximum": 150})
    speed = workflow.add("logic.map", 1270, 300, params={
        "in_low": 0, "in_high": 30, "out_low": 0, "out_high": 70, "clamp": True})
    forward = workflow.add("decode.rate", 880, 300, params={"channel": "motor.walk_forward"})
    motor = workflow.add("output.pwm", 1470, 300, params={"pin": 18, "frequency": 1000})
    readout = workflow.add("output.display", 1470, 460, params={
        "label": "Steering command", "style": "meter", "minimum": -20, "maximum": 20, "unit": "Hz"})
    workflow.connect(start.id, "next", goal.id)
    workflow.connect(goal.id, "next", eye.id)
    workflow.connect(eye.id, "next", step.id)
    workflow.connect(step.id, "next", servo.id)
    workflow.connect(servo.id, "next", motor.id)
    workflow.connect(motor.id, "next", readout.id)
    workflow.connect(camera.id, "frame", eye.id, "frame")
    workflow.connect(step.id, "spikes", steering.id, "spikes")
    workflow.connect(step.id, "spikes", forward.id, "spikes")
    workflow.connect(step.id, "spikes", gate.id, "spikes")
    workflow.connect(steering.id, "value", smooth.id, "value")
    workflow.connect(smooth.id, "value", angle.id, "value")
    workflow.connect(angle.id, "value", servo.id, "angle")
    workflow.connect(gate.id, "open", servo.id, "when")
    workflow.connect(forward.id, "hz", speed.id, "value")
    workflow.connect(speed.id, "value", motor.id, "duty")
    workflow.connect(gate.id, "open", motor.id, "when")
    workflow.connect(smooth.id, "value", readout.id, "value")
    return workflow


@template(
    "avoid", "Keep away from obstacles", "Distance sensor in, motor pins out",
    "An ultrasonic sensor's reading is delivered to real mechanosensory cells,"
    " the network runs, and the descending readout decides whether to go"
    " forward or back off. The distance is also checked directly, so the robot"
    " stops even if the network does nothing - a network in the loop is not a"
    " substitute for a safety limit.",
    needs=("gpio",), tags=("robot", "hardware", "safety"),
)
def _avoid():
    workflow = Workflow("Keep away from obstacles")
    start = workflow.add("flow.start", 60, 320)
    goal = workflow.add("goal.define", 230, 60, params={
        "goal": "Drive forward without touching anything.",
        "success": "Nothing is closer than the stop distance at the end of a pass.",
        "failure": "Something comes closer than the stop distance.",
    })
    ping = workflow.add("input.ping", 230, 320, params={"trigger": 23, "echo": 24, "maximum": 400})
    smooth = workflow.add("logic.smooth", 440, 320, params={"window": 3})
    drive = workflow.add("encode.channel", 640, 320, params={
        "channel": "mechano.bristle", "low": 60.0, "high": 5.0, "curve": "saturating"})
    step = workflow.add("brain.step", 850, 320, params={"duration": 300.0, "learning": True})
    backward = workflow.add("decode.rate", 1060, 200, params={"channel": "motor.backward"})
    forward = workflow.add("decode.rate", 1060, 440, params={"channel": "motor.walk_forward"})
    too_close = workflow.add("logic.compare", 440, 520, params={"test": "lt", "threshold": 15.0})
    retreat = workflow.add("flow.branch", 1290, 320)
    stop_pin = workflow.add("output.gpio", 1520, 200, params={"pin": 17, "high": False})
    go_pin = workflow.add("output.gpio", 1520, 440, params={"pin": 17, "high": True})
    speed = workflow.add("logic.map", 1290, 560, params={
        "in_low": 0, "in_high": 40, "out_low": 0, "out_high": 60, "clamp": True})
    motor = workflow.add("output.pwm", 1520, 560, params={"pin": 18})
    show = workflow.add("output.display", 1520, 680, params={
        "label": "Distance", "style": "gauge", "minimum": 0, "maximum": 400, "unit": "cm"})
    workflow.connect(start.id, "next", goal.id)
    workflow.connect(goal.id, "next", drive.id)
    workflow.connect(drive.id, "next", step.id)
    workflow.connect(step.id, "next", retreat.id)
    workflow.connect(retreat.id, "yes", stop_pin.id)
    workflow.connect(retreat.id, "no", go_pin.id)
    workflow.connect(go_pin.id, "next", motor.id)
    workflow.connect(motor.id, "next", show.id)
    workflow.connect(ping.id, "distance", smooth.id, "value")
    workflow.connect(smooth.id, "value", drive.id, "value")
    workflow.connect(smooth.id, "value", too_close.id, "value")
    workflow.connect(smooth.id, "value", show.id, "value")
    workflow.connect(step.id, "spikes", backward.id, "spikes")
    workflow.connect(step.id, "spikes", forward.id, "spikes")
    workflow.connect(too_close.id, "result", retreat.id, "when")
    workflow.connect(forward.id, "hz", speed.id, "value")
    workflow.connect(speed.id, "value", motor.id, "duty")
    return workflow


@template(
    "train", "Two-choice training", "Show a picture, score the answer, deliver the teaching pulse",
    "A trial loop: reset activity, show an image, let the network run, read"
    " which side won, score it against the answer you expect, and send the"
    " matching dopaminergic pulse. This is the arrangement any claim about"
    " learning has to be made in - and the frozen-synapse control is one"
    " switch away on the network block.",
    tags=("training", "no hardware"),
)
def _train():
    workflow = Workflow("Two-choice training")
    start = workflow.add("flow.start", 60, 340)
    goal = workflow.add("goal.define", 220, 60, params={
        "goal": "Learn to answer 'left' when the left half of the picture is brighter.",
        "success": "The reported choice matches the brighter half.",
        "failure": "The reported choice is the other side.",
    })
    loop = workflow.add("loop.repeat", 220, 340, params={"times": 20})
    trial = workflow.add("train.trial", 430, 340, params={"reset": True, "keep_memory": True})
    image = workflow.add("vision.image", 430, 520)
    eye = workflow.add("encode.eye", 640, 340)
    step = workflow.add("brain.step", 840, 340, params={"duration": 500.0, "learning": True})
    choice = workflow.add("decode.choice", 1060, 340, params={
        "label1": "left", "channel1": "motor.steering@left",
        "label2": "right", "channel2": "motor.steering@right",
        "label3": "", "label4": "", "margin": 1.0, "undecided": "none"})
    expected = workflow.add("input.text", 1060, 640, params={"value": "left"})
    matches = workflow.add("logic.match", 1280, 560, params={"expected": "left", "test": "equals"})
    missed = workflow.add("logic.gate", 1280, 700, params={"operation": "and"})
    outcome = workflow.add("goal.outcome", 1280, 340, params={"stop_after": 0})
    teach = workflow.add("train.teach", 1520, 340, params={
        "correct_channel": "teach.pam11", "incorrect_channel": "teach.ppl101"})
    settle = workflow.add("brain.step", 1740, 340, params={"duration": 300.0, "learning": True})
    score = workflow.add("goal.score", 1960, 340)
    show = workflow.add("output.display", 2160, 340, params={
        "label": "Accuracy", "style": "bar", "minimum": 0, "maximum": 1})
    log = workflow.add("output.log", 2160, 480, params={"label": "trial"})
    workflow.connect(start.id, "next", goal.id)
    workflow.connect(goal.id, "next", loop.id)
    workflow.connect(loop.id, "body", trial.id)
    workflow.connect(trial.id, "next", eye.id)
    workflow.connect(eye.id, "next", step.id)
    workflow.connect(step.id, "next", outcome.id)
    workflow.connect(outcome.id, "correct", teach.id)
    workflow.connect(outcome.id, "incorrect", teach.id)
    workflow.connect(teach.id, "next", settle.id)
    workflow.connect(settle.id, "next", show.id)
    workflow.connect(show.id, "next", log.id)
    workflow.connect(image.id, "frame", eye.id, "frame")
    workflow.connect(step.id, "spikes", choice.id, "spikes")
    workflow.connect(choice.id, "choice", matches.id, "value")
    workflow.connect(expected.id, "value", matches.id, "against")
    workflow.connect(matches.id, "result", outcome.id, "correct")
    workflow.connect(matches.id, "opposite", missed.id, "a")
    workflow.connect(choice.id, "decided", missed.id, "b")
    workflow.connect(missed.id, "result", outcome.id, "incorrect")
    workflow.connect(outcome.id, "outcome", teach.id, "outcome")
    workflow.connect(score.id, "accuracy", show.id, "value")
    workflow.connect(choice.id, "details", log.id, "value")
    return workflow


@template(
    "screen", "Work a window", "Look at part of the screen, move the mouse, click",
    "Screen capture into the eyes, the steering pathway out to the cursor, and"
    " the commit gate deciding whether to click. Set the capture rectangle to"
    " the window you want the network to work with. Turn on device simulation"
    " first so you can watch what it would do before it touches your cursor.",
    needs=("screen", "pointer"), tags=("desktop",),
)
def _screen():
    workflow = Workflow("Work a window")
    start = workflow.add("flow.start", 60, 320)
    goal = workflow.add("goal.define", 230, 60, params={
        "goal": "Move the pointer toward whichever side of the captured region"
                " drives the steering pathway harder, and click when the"
                " commit cells fire.",
        "success": "The pointer ends up over the intended target.",
        "failure": "The pointer drifts to one edge and stays there.",
    })
    screen = workflow.add("vision.screen", 230, 320, params={
        "use_region": True, "left": 0, "top": 0, "region_width": 800, "region_height": 600})
    eye = workflow.add("encode.eye", 450, 320)
    step = workflow.add("brain.step", 650, 320, params={"duration": 400.0, "learning": False})
    steering = workflow.add("decode.difference", 870, 200, params={"channel": "motor.steering", "scale": 1.0})
    gate = workflow.add("decode.gate", 870, 440, params={"channel": "motor.gate", "minimum": 2})
    dx = workflow.add("logic.map", 1080, 200, params={
        "in_low": -20, "in_high": 20, "out_low": -60, "out_high": 60, "clamp": True})
    move = workflow.add("output.mouse", 1300, 200, params={"action": "move_by", "y": 0})
    click = workflow.add("output.mouse", 1300, 440, params={"action": "click", "button": "left"})
    pause = workflow.add("flow.wait", 1520, 320, params={"seconds": 0.25})
    workflow.connect(start.id, "next", goal.id)
    workflow.connect(goal.id, "next", eye.id)
    workflow.connect(eye.id, "next", step.id)
    workflow.connect(step.id, "next", move.id)
    workflow.connect(move.id, "next", click.id)
    workflow.connect(click.id, "next", pause.id)
    workflow.connect(screen.id, "frame", eye.id, "frame")
    workflow.connect(step.id, "spikes", steering.id, "spikes")
    workflow.connect(step.id, "spikes", gate.id, "spikes")
    workflow.connect(steering.id, "value", dx.id, "value")
    workflow.connect(dx.id, "value", move.id, "x")
    workflow.connect(gate.id, "open", click.id, "when")
    return workflow


@template(
    "listen", "Listen and react", "Microphone into Johnston's organ, serial command out",
    "Sound is split into six frequency bands and delivered to the six"
    " Johnston's organ subgroups - the fly's actual ear. The network's"
    " descending output becomes a command line on the serial port, which is"
    " the simplest way to reach an Arduino or motor controller.",
    needs=("audio", "serial"), tags=("sound", "hardware"),
)
def _listen():
    workflow = Workflow("Listen and react")
    start = workflow.add("flow.start", 60, 300)
    goal = workflow.add("goal.define", 230, 60, params={
        "goal": "React to sound arriving at the antennal ear.",
        "success": "Loud sound produces a different command from silence.",
        "failure": "The command never changes.",
    })
    mic = workflow.add("input.audio", 230, 300, params={"bands": 6, "seconds": 0.1})
    ear = workflow.add("encode.vector", 460, 300, params={
        "channel": "mechano.johnston", "by_subtype": True, "low": 0.0, "high": 1.0})
    step = workflow.add("brain.step", 680, 300, params={"duration": 400.0, "learning": False})
    choice = workflow.add("decode.choice", 900, 300, params={
        "label1": "forward", "channel1": "motor.walk_forward",
        "label2": "back", "channel2": "motor.backward",
        "label3": "stop", "channel3": "motor.stop",
        "label4": "", "margin": 0.5, "undecided": "hold"})
    serial = workflow.add("output.serial", 1140, 300, params={"message": "CMD {value}", "baud": 115200})
    show = workflow.add("output.display", 1140, 460, params={"label": "Command", "style": "text"})
    workflow.connect(start.id, "next", goal.id)
    workflow.connect(goal.id, "next", ear.id)
    workflow.connect(ear.id, "next", step.id)
    workflow.connect(step.id, "next", serial.id)
    workflow.connect(serial.id, "next", show.id)
    workflow.connect(mic.id, "bands", ear.id, "values")
    workflow.connect(step.id, "spikes", choice.id, "spikes")
    workflow.connect(choice.id, "choice", serial.id, "value")
    workflow.connect(choice.id, "choice", show.id, "value")
    return workflow


@template(
    "pins", "Pin in, pin out", "Wait for a switch, run the network, drive a pin",
    "The smallest useful hardware loop. A pin is watched for a change, the"
    " event is delivered to mechanosensory cells, the network runs, and an"
    " output pin follows the descending readout. Everything a workflow needs"
    " to sit inside a larger machine.",
    needs=("gpio",), tags=("hardware", "minimal"),
)
def _pins():
    workflow = Workflow("Pin in, pin out")
    start = workflow.add("flow.start", 60, 300)
    goal = workflow.add("goal.define", 220, 60, params={
        "goal": "Respond to a contact closure with a pin output.",
        "success": "The output pin follows the network's descending activity.",
        "failure": "The output never changes.",
    })
    pin_in = workflow.add("input.gpio", 220, 300, params={"pin": 4, "pull": "up", "invert": True})
    drive = workflow.add("encode.channel", 440, 300, params={
        "channel": "mechano.bristle", "low": 0.0, "high": 1.0, "curve": "linear"})
    step = workflow.add("brain.step", 650, 300, params={"duration": 200.0, "learning": True})
    rate = workflow.add("decode.rate", 860, 300, params={"channel": "motor.descending"})
    over = workflow.add("logic.compare", 1070, 300, params={"test": "gt", "threshold": 1.0})
    pin_out = workflow.add("output.gpio", 1280, 300, params={"pin": 17})
    every = workflow.add("flow.every", 1500, 300, params={"interval": 0.5})
    log = workflow.add("output.log", 1700, 300, params={"label": "pin"})
    workflow.connect(start.id, "next", goal.id)
    workflow.connect(goal.id, "next", drive.id)
    workflow.connect(drive.id, "next", step.id)
    workflow.connect(step.id, "next", pin_out.id)
    workflow.connect(pin_out.id, "next", every.id)
    workflow.connect(every.id, "ready", log.id)
    workflow.connect(pin_in.id, "number", drive.id, "value")
    workflow.connect(step.id, "spikes", rate.id, "spikes")
    workflow.connect(rate.id, "hz", over.id, "value")
    workflow.connect(over.id, "result", pin_out.id, "value")
    workflow.connect(rate.id, "hz", log.id, "value")
    return workflow
