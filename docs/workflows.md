# Building a workflow

A workflow is a drawing of what happens: what the network senses, how long it runs, what its activity means, and what that drives. This page explains the language. Every block also describes itself on hover in the interface, so this is the overview rather than the reference.

## The two kinds of wire

**Run-order wires** (white, arrow-shaped pins) say which block happens next. The run starts at **Start** and follows them. A branch has two outlets; a loop has one for its body and one for afterwards.

**Value wires** (coloured, round pins) say where a block's input comes from. They are pulled on demand: when a block runs, it asks its upstream blocks for their values first.

That separation is why loops, waits and branches are visible on the canvas instead of implied by the order you happened to place things in.

A block whose value is asked for before it has run reports whatever it last produced. That is what lets you feed a result back to an earlier block — through **Hold a value** — without the editor deadlocking. A value loop that does not pass through one is reported as an error, with that fix named.

## The shape of most workflows

```
   source  →  encoder  →  [ Let the network run ]  →  readout  →  logic  →  output
```

Sources produce numbers or pictures. Encoders turn them into current on named cells. The network block integrates everything queued since the last one. Readouts turn spike counts back into numbers. Logic decides. Outputs act.

Encoders queue their drive; nothing reaches the cells until **Let the network run** executes. Readouts report on the most recent step. That ordering is the one thing worth internalising.

## The block groups

**Run order** — Start, Wait, If / then, Route by label, Repeat N times, Repeat until, At most every, Stop the run, Leave the loop, Note.

**Inputs** — fixed numbers and text, random numbers (for control conditions), the run clock, files, web endpoints, serial lines, GPIO pins, ultrasonic distance sensors, microphones.

**Vision inputs** — screen regions, cameras, image files and folders, video files, headless web pages, and a moving test pattern for checking the eye works before wiring anything up.

**Into the network** — Show it to the eyes (the retinotopic path), Drive a sense (one reading onto one channel), Drive a sense across subtypes (a list of readings onto a channel's named subgroups), and Teaching pulse.

**Network** — Let the network run, Save the network, Freeze or unfreeze learning, Reset the network.

**Out of the network** — Read a cell group, Left-right difference, Commit gate, Choice, Heading, Read every subtype, Memory state.

**Decide** — Compare, Arithmetic, Rescale, Smooth, Ignore small values, Hold a value, Count, Expression, Combine yes/no, Pick one of two, Match text.

**Outputs** — Set a pin, Motor speed, Servo angle, Send serial, Mouse, Keyboard, Webhook, UDP, MQTT, Record, Show on the dashboard, Append to a file, Run a program.

**Goal and training** — Goal, Outcome, Scoreboard, Start a trial, Teach from the outcome.

## Patterns worth knowing

**Gate everything that moves.** Put a **Commit gate** on the descending cells and wire it into the *Only when* input of every output block. Nothing moves unless the network actually fired, rather than on whatever noise the readout happened to see.

**Rescale once, at the edge.** Spike rates are in Hz; servos want degrees; motors want percent. One **Rescale** block with *Keep inside the range* on, immediately before the output, is where an out-of-range value should be caught — not after it has commanded a servo past its travel.

**Smooth noisy sensors, not decisions.** A distance sensor and a firing rate are both noisy. Smooth them. Do not smooth the output of a Choice block; average a decision and you get a decision nobody made.

**Use Repeat until with its safety limit.** The limit is not a formality: a condition that never becomes true will otherwise run until you stop it, with the hardware doing whatever it was doing.

**Score what you can check.** The **Outcome** block wants two conditions, not one. Anything that is neither right nor wrong is neutral and is not scored, which is what you want when the network was undecided — a tie counted as a mistake will quietly poison a training run.

## A training loop

```
Start → Goal → Repeat N times ─┬─ body: Start a trial → Show it to the eyes
                               │                       → Let the network run
                               │                       → Outcome ─┬─ right → Teach from the outcome
                               │                                  └─ wrong ┘        ↓
                               │                                          Let the network run (settle)
                               └─ finished: Scoreboard → Record
```

The second network run after the teaching pulse matters. The pulse is delivered *during* a step, and the plasticity rule needs the dopamine to arrive while the cue's eligibility trace is still up. A run that ends immediately after the outcome never gives it the chance.

Then run the whole thing again with **Allow synapses to change** turned off, and compare. If the scores are the same, nothing was learned. The Training page lists the other controls worth running.

## Errors and stopping

A run stops for exactly one reason and always says which: the iterations finished, a **Stop the run** block ran, the goal was reached, the time limit was hit, someone pressed Stop, a block failed with *stop on error* on, or the workflow had errors that prevented it starting.

**Check** before running finds missing connections, impossible wires, loops with no exit, blocks that nothing reaches, and hardware this machine does not have. Warnings are worth reading; errors prevent a run.

While a run is going, each block flashes as it executes and shows how many milliseconds it took. A block that fails gets a red badge with the message on hover. The log underneath records everything, and is written to the run's folder as JSONL.

## Where a workflow's files go

`~/.flylab/projects/<project>/runs/<run>/` holds the summary, the log, anything **Append to a file** wrote with a relative path, models any **Save the network** block wrote, and `provenance.json` — the exact configuration, compartment selection and source checksums the run used. That last file is what makes a result checkable later.
