# Running FLYLAB

FLYLAB is a local program. It runs on your machine, keeps everything in a folder you control, and contacts nothing on your behalf except the connectome download you ask for.

## Installation

Python 3.11 or newer and a C++17 compiler (`clang++`/`c++` on macOS, GCC or Clang on Linux and Raspberry Pi OS). The compiler is needed once, to build the simulation kernel.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 startup.py --check     # report what is present and what is missing
python3 startup.py             # start the interface
```

`startup.py --install` installs the required packages first. Hardware packages are optional and listed separately: install only what your machine actually has. A block that needs a missing one names the package rather than failing obscurely.

## The dataset

`python3 startup.py --fixture` builds a small synthetic graph so you can learn the interface immediately. It is randomly wired and tells you nothing about flies; the interface says so permanently while it is loaded, and every file it produces is stamped `synthetic_fixture`.

For the real thing, open **Housekeeping → Download and prepare**, or run `python3 -m flylab prepare`. About 1.1 GB is downloaded, each file is checked against the SHA-256 lock committed here, the annotations are normalised and the graph is compiled. Allow around 4 GB of free space in total and several minutes; a Raspberry Pi will take considerably longer.

`python3 -m flylab verify` re-checks a prepared dataset at any time: source checksums, every compiled array against its lock, transmitter annotations, and neuron ordering. A mismatch stops with the name of the file that failed. Nothing is installed from a download that does not match.

Datasets live in `~/.flylab/datasets/` by default. `--workspace` moves the whole workspace; `FLYLAB_DATA` points at a dataset directory; `FLYLAB_CACHE` moves the compiled kernel.

## Loading a network

Open **Status** and press *Load* beside a dataset. Loading reads the graph, compiles the kernel if its source has changed, resolves every channel against the annotations, and builds the viewport layout. On the full release this takes a minute and a few gigabytes of memory; 16 GB is a comfortable amount.

One process runs one dataset at a time. Switching datasets means unloading and loading again, which is deliberate: a fixture and a real release can never be mixed inside one run.

## Running a workflow

Press **Run** on the Workflow page. The run settings decide how many iterations happen, whether there is a time limit, how long to wait between iterations, whether synapses may change, whether missing hardware is simulated, and whether the first error ends the run.

A run stops for exactly one reason and always says which: the iteration count finished, a Stop block ran, the goal was reached, the time limit was hit, someone pressed Stop, a block failed, or the workflow had errors that prevented it starting.

Everything a run produces goes into `~/.flylab/projects/<project>/runs/<run>/`: a summary, the log as JSONL, any files the workflow wrote, and `provenance.json` — the exact configuration, compartment selection, graph checksums and source-file hashes the run used.

Stop with the Stop button, or by closing the program. Neural state stays in memory until the network is unloaded; save a model if you want to keep it.

## Hardware

Raspberry Pi pins are **3.3 V**. A 5 V signal on an input can destroy the board, and so can drawing motor current from a pin. Use a driver board for anything with a motor in it, and a divider or level shifter on an HC-SR04 echo line.

Pins are addressed by BCM number — the numbers in a pinout diagram — and the interface shows the physical header position beside each. Pins that are not general-purpose on a 40-pin header are refused.

Before connecting a servo to a real mechanism, set its travel limits on the block. A servo will stall itself against a hard stop indefinitely.

**Turn on device simulation first.** A simulated device keeps state in memory, returns plausible values and records every write, so a workflow can be built and stepped through before it touches anything. Simulated values are labelled everywhere they appear, including in the saved run log, so a simulated result can never be mistaken for a real one.

The mouse, keyboard and shell blocks act on the machine hosting FLYLAB. Shell output is disabled until a block explicitly enables it, because a workflow that can run programs can do anything your user account can.

## Reaching it from another device

FLYLAB binds `0.0.0.0` by default — every interface — so it is reachable at this machine's own address as well as at `127.0.0.1`. The start-up banner prints the address to use:

```
====================================================================
  Open from any device on this network:
      http://192.168.1.42:8765/
  On this machine:  http://127.0.0.1:8765/
====================================================================
  Listening on every interface (0.0.0.0:8765).
  No access key is required. Anyone who can reach this machine on
  the network can drive its GPIO pins, move its pointer and start
  programs on it. Use --local-only, or Settings, to close it down.
```

That default exists because the machine FLYLAB usually runs on has no screen: a Raspberry Pi wired to a robot, opened from a laptop or a phone on the same network. The address is also on the Settings page with a button that copies it.

**The exposure is real and unauthenticated.** This interface drives GPIO pins, moves the pointer and starts programs on the host. Treat the network it sits on as the security boundary, because with the shipped defaults that is exactly what it is.

Three ways to narrow it:

```sh
python3 startup.py --local-only          # loopback only; every other client refused
python3 startup.py --key                 # stay shared, require a key in the address
python3 startup.py --port 9000 --no-browser
```

Both halves are enforced. The listening address follows the setting when FLYLAB starts, and every request and every socket is checked against the *current* setting as it arrives — so switching to local-only in Settings locks out remote devices at once, without a restart, even though the socket stays bound until the next start.

When a key is required it appears on the Settings page and in the terminal, and the shared address carries it as `?key=...`; the browser keeps it in a cookie afterwards. Connections from the machine FLYLAB is running on never need it. Generating a new key locks out every device using the old address immediately.

A single shared key over plain HTTP is not authentication in any serious sense. It is appropriate for a workshop network and nothing more. Do not put this on a network you do not control, and do not forward the port to the internet.

Your choice is remembered. Setting the toggles by hand — in Settings, or with `--lan`, `--local-only`, `--key` or `--no-key` — records that you chose them, and a later change to the shipped default will leave them alone.

## The workspace

```
~/.flylab/
  settings.json             every setting, as plain JSON
  datasets/                 downloaded and compiled connectome data
  models/                   the model library, with checkpoints under files/
  projects/<id>/
    project.json            name, notes, run settings, model it resumes from
    workflow.json           the canvas
    history.json            every run this project has done
    runs/<run>/             summary, log, provenance, anything the workflow wrote
```

Everything is plain JSON beside its files. It stays readable without this program and it is safe to back up, move, or put under version control.

## Recovery

A model checkpoint records the exact graph, compartment selection and parameters it came from. Loading it into a network that differs in any of those is refused with the mismatch named, not approximated.

If the interface will not start, `python3 startup.py --check` reports what is missing. If a dataset behaves strangely, `python3 -m flylab verify` re-checks it against the locks. If a device stops responding, **Bench → Re-check** re-imports every driver and re-lists serial ports.

If a run leaves hardware in an unwanted state, that is the workflow's responsibility: a network in the loop is not a substitute for a limit switch, a fuse, or a stop block that runs before anything moves.
