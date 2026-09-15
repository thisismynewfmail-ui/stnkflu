# Validation status

Recorded on 2026-09-15 on Linux x86_64, Python 3.11.15, 4 cores. Every number below was measured against the **real MaleCNS v1.0 release**, downloaded, checksum-verified and compiled by this repository's own pipeline: 166,700 neurons, 25,582,938 directed connections, 124,177,617 synaptic contacts.

**Automated suite: 114 tests passed.** The suite runs against a synthetic fixture rather than the release, so it takes seconds and needs no download. The fixture is a randomly wired graph in the same on-disk layout, exercising the same loader, the same compiled kernel and the same channel resolution.

## What was checked, and what it does not establish

| Check | Observed | What it does not establish |
| --- | --- | --- |
| Rebuild from checksum-verified released files | 166,700 neurons, 25,582,938 connections, 124,177,617 contacts; source annotations, every compiled array, transmitter values and neuron ordering all match their committed locks | Completeness or physiological accuracy of the reconstruction |
| Channel resolution on the real release | 54 of 56 channels resolve; 163,090 of 166,700 cells fall in at least one channel; 3,335 mapped R1–R6 and 811 mapped R8 photoreceptors | That driving or reading a channel resembles what the animal does with it |
| Absent channels reported, not invented | `taste.bitter` (this release annotates taste neurons by sensillum and body part, not by response class) and `nociception.md` resolve to zero cells and are marked absent | That a future release would not contain them |
| Load time and footprint | 9.3 s to read the graph, compile the kernel and index every channel | Performance on a Raspberry Pi, where it will be considerably slower |
| Whole-network step | 500 ms of neural time in 1.2–2.1 s of wall time depending on activity | Real-time operation of any kind |
| Checkpoint round trip | 7.0 MB written in 1.7 s, restored in 1.5 s, every plastic efficacy bit-identical | That a checkpoint is meaningful beyond this exact configuration |
| Provenance refusal | A checkpoint from a different compartment preset is refused by name, not approximated | — |
| Reading before stepping | Refused with an explicit error rather than returning stale counts | — |
| End-to-end workflow | The "Bench check" template ran 3 iterations in 8.8 s, driving three live readouts | Any task being accomplished |

## Sensory channels reaching the network

Each row is one 200 ms step (500 ms for the image) from a reset network, full-strength drive, learning off. Rates are means over the cells in each group.

| Input | Cells driven | Total spikes | Kenyon cells | MBONs | Descending | Leg motor |
| --- | --- | --- | --- | --- | --- | --- |
| Nothing (spontaneous) | 0 | 135,249 | 0.00 Hz | 0.00 Hz | 0.05 Hz | 0.00 Hz |
| White image | 4,146 receptors | 391,822 | **0.008 Hz** | 0.60 Hz | 1.52 Hz | 0.81 Hz |
| Odour, all ORNs | 2,635 | 229,588 | **8.01 Hz** | 32.78 Hz | 7.57 Hz | 4.96 Hz |
| Odour, left antenna only | 883 | 181,818 | 5.71 Hz | 25.26 Hz | 7.50 Hz | 4.09 Hz |
| Touch, all bristles | 4,455 | 235,111 | 3.38 Hz | 19.95 Hz | 12.81 Hz | 11.63 Hz |
| Sugar taste | 23 | 75,227 | 0.00 Hz | 1.19 Hz | 5.47 Hz | 3.85 Hz |
| Sound, Johnston's organ | 672 | 79,516 | 0.00 Hz | 1.34 Hz | 7.16 Hz | 3.69 Hz |
| Leg position, FeCO | 599 | 92,142 | 0.00 Hz | 2.73 Hz | 5.95 Hz | 3.37 Hz |

Three things are worth stating plainly.

**The network is spontaneously active.** 135,249 spikes arrive in 500 ms with nothing driven at all. Any change attributed to an input has to be larger than that.

**Olfaction reaches the mushroom body; the visual adapter barely does.** The same network gives Kenyon cells 8.01 Hz from odour and 0.008 Hz from a full-white image — a thousandfold difference. That is not a discovery about flies: the mushroom body is predominantly an olfactory structure, and the platform reaches it through its natural input. It does mean that a workflow built around vision and expecting mushroom-body plasticity is building on almost nothing, and should use the olfactory or mechanosensory channels instead. The earlier build of this project drove the mushroom body visually, and this measurement is the clearest evidence yet that doing so was a weak basis.

**Driving one side produces an asymmetric response.** Odour into the left antenna alone gives 5.71 Hz at the Kenyon cells against 8.01 Hz for both — the pathway is lateralised as the anatomy implies, without anything in this program arranging it.

## Plasticity

One 500 ms step from a reset network, odour drive throughout, learning on, 7,835 plastic KC→MBON07/MBON11 edges.

| Condition | Kenyon spikes | Teaching cell spikes | Edges changed | Mean efficacy |
| --- | --- | --- | --- | --- |
| Odour + PAM11 pulse | 11,865 | 291 across 15 cells | 4,957 | 0.9888 |
| Odour alone (unpaired control) | — | — | 4,974 | 0.9811 |
| Odour + PPL101 pulse | — | 159 across 2 cells | 4,955 | — |
| Odour + PAM11, synapses frozen | — | — | **0** | 1.0000 exactly |

The identified dopaminergic cells fire when a pulse is delivered, eligible synapses change, and freezing stops every write exactly. Those are the mechanism checks passing.

**The unpaired control changed slightly more edges than the paired condition**, and left them slightly more depressed. Endogenous dopaminergic activity drives this rule on its own: the network's own PAM and PPL1 cells fire during an odour presentation whether or not anything is injected, and the injected pulse is a small perturbation on top of that. The paired and unpaired weights do differ, so the pulse has an effect — but it is not the dominant term, and the direction is not the one a naive reading of "reward strengthens" would predict.

Anyone reporting a result from this platform has to run the unpaired control. Without it, almost any pairing protocol will appear to "work", because the synapses were going to move anyway.

An earlier probe with a white image instead of odour changed 5 edges in every condition including the unpaired control, and the paired and unpaired weights were **bit-identical**. With that input the injected pulse made no difference whatsoever.

## What was not checked

No behavioural task was learned. No held-out evaluation, no shuffled-reinforcement control, no independent repeats, no memory-reset comparison, and no retention test were performed. No hardware was attached: GPIO, serial, camera, microphone and pointer paths were exercised only through their simulated backends, which is why every simulated value carries a label.

**No useful learning, task performance, or biological replication has been demonstrated by this repository.** What exists is a functioning experimental loop with honest instrumentation, and one clear negative result about where the visual adapter reaches.

## Reproduce

```sh
python3 -m pytest -q                       # 114 tests, synthetic fixture, seconds
python3 -m flylab fixture data-fixture     # build the fixture yourself
python3 -m flylab prepare                  # download and compile the real release
python3 -m flylab verify                   # re-check it against the committed locks
python3 startup.py                         # then Bench, to repeat the probes above
```

The sensory table is the Bench page: pick a channel, set the reading to full scale, set the step to 200 ms, tick the readouts and press Probe. The plasticity table is the same page with a teaching pulse selected and "Allow synapses to change" turned on, run once with the pulse and once without.

Numbers will differ between machines only in wall time. The simulation is deterministic given the same graph, configuration and inputs; the run's `provenance.json` records the checksums that make that checkable.
