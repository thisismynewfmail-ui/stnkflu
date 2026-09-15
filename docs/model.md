# What is actually modelled

This is a wiring-constrained spiking-network platform. The connectome supplies anatomy: which cells exist, what they are called, and what contacts what. It does not supply dynamics, receptor identity, most neuromodulation, or behaviour. Everything between anatomy and a working workflow is a modelling choice made here, and this document states each one.

No language model is in the loop. Nothing scripted is presented as neural output. No result is selected for being more flattering than another.

## Anatomy

The [MaleCNS v1.0 release](https://male-cns.janelia.org/download/) provides the male brain and ventral nerve cord. Import retains every entry with an assigned neuronal superclass, including uncertain classes, while excluding explicit glia and unresolved segmentation objects. It retains all released edges between those entries, including weak edges and self-connections: **166,700 nodes, 25,582,938 directed connections, 124,177,617 synaptic contacts**. "Full retained" describes this inclusion policy; it does not mean every biological synapse was reconstructed.

The importer verifies SHA-256 source files and every compiled graph array against committed locks. Transmitter annotations and cell order are checked too. Neuron IDs remain integers and are never rounded through floating point. Source files are downloaded separately under their upstream licence.

## Dynamics

The native kernel integrates approximate leaky integrate-and-fire cells at **0.1 ms**. It uses 20 ms membrane and 5 ms synaptic time constants, a −45 mV threshold, 1.8 ms transmission delay and 2.2 ms refractory period. Contact count times 0.275 sets initial synaptic magnitude. Acetylcholine is assigned excitation; GABA, glutamate and histamine inhibition, with an explicit positive fallback for unresolved signs. **This is a coarse sign proxy, not receptor-specific physiology.** Kenyon-cell rest is −60 mV with an 8 mV adaptation increment decaying over 200 ms; other cells rest at −52 mV.

Pure dopamine, serotonin and octopamine annotations deliver a modulatory trace along their retained edges instead of generic fast excitation. Only the specified memory rule consumes selected dopamine activity; **most modulatory effects are unmodelled**. Cotransmission and receptors remain incomplete. Keeping an edge in the graph does not establish that all its biological effects are reproduced.

The event-driven kernel avoids unnecessary subthreshold updates. It does not prune the graph or enlarge the integration timestep: it accelerates model execution, not biological time.

**Neural time is decoupled from real time.** A block asking for 500 ms of neural time advances the simulation by 500 ms regardless of how long the computation takes, and regardless of how much wall time passed since the previous step. Eligibility and decay operate in neural seconds. Nothing here claims real-time fly physiology.

## Channels: how the world reaches real cells

A *channel* is a named group of cells, resolved from the release's own type annotations at load time, plus a declared way of driving or reading them. `flylab/neural/atlas.py` is the complete list; the Network page shows how many cells each one has in the loaded dataset. A channel that matches nothing is reported as absent rather than hidden, and a block addressing it fails rather than inventing an input.

Inputs cover vision (R1–R6, R7, R8, dorsal rim, ocelli), olfaction, gustation, Johnston's organ, bristle mechanoreceptors, wind projection neurons, femoral chordotonal organs, campaniform sensilla, hair plates, thermo- and hygrosensors, nociceptors and ascending neurons. Outputs cover descending neurons as a whole and by published function, leg, wing, neck, haltere and proboscis motor neurons, mushroom-body output neurons, the central complex's heading, goal and steering populations, and neurosecretory cells. Modulatory channels cover the PAM and PPL1 dopaminergic clusters, octopaminergic and serotonergic cells, and the circadian clock neurons.

Scalar readings are normalised against a declared range, then shaped by a saturating curve `30·x/(0.02+x)` — the same nonlinearity the photoreceptor adapter uses — or by a straight line, and injected as current. **The choice of which physical quantity maps onto which cell type is yours, and the platform does not pretend the mapping is biological.** Driving a distance sensor into bristle mechanoreceptors is a deliberate engineering decision; the cells are real, the stimulus is not what a fly would receive.

## Vision

Images reach the eye through the retinotopic projection inherited from the original build. 3,335 mapped R1–R6 cells receive linear-sRGB luminance and 811 mapped R8 cells receive blue and green proxies, each sampled at that cell's inferred column. Sample locations come from contacts with column-annotated visual cells, using overlapping left and right viewports. Unmapped receptors get no invented optical input, and R7 receives none at all because a screen emits no ultraviolet.

Existing R8→aMe12 connections use a net excitatory sign motivated by [Xiao et al., 2023](https://doi.org/10.1038/s41586-023-06681-6); transferring that result to these reconstructed cells and contact-count magnitudes remains an assumption. Photoreceptors and lamina cells are graded in real flies. Using spikes, RGB channels, saturating current and a 12 mV-equivalent lamina bias is an explicit display adapter, **not validated retinal physiology**. Display sensitivity remains a major confound: a dark image barely activates Kenyon cells, a light one activates more, and that is a property of the adapter rather than a discovery.

## Readouts

Every readout is a fixed, declared arithmetic operation on spike counts over the last step. Mean rates, not totals, so a large population does not outweigh a small one merely by being larger.

| Readout | What it computes |
| --- | --- |
| Rate | Mean spikes per second across a channel, or one side of it |
| Left-right difference | Right-side mean minus left-side mean |
| Commit gate | Whether a channel produced at least N spikes |
| Choice | Which of up to four labelled channels has the highest rate, given a required lead |
| Heading | Circular mean of activity over cells placed evenly around a circle **by index order, which is a stated convention and not their anatomical wedge identity** |
| Memory state | How many plastic synapses differ from baseline, and by how much |

These are interface conventions, not discovered neural codes. Calling a channel "turn left" does not make the cells mean that. A persistent circuit bias becomes persistently turning one way; do not read that as a decision.

## Learning

The candidate memory rule acts on existing KC→MBON edges, selected by a compartment preset:

- **alpha1 + gamma1pedc** — PAM11 → MBON07 and PPL101 → MBON11. The pair used for every result recorded in [validation](validation.md), and the default.
- **Whole mushroom body** — every MBON in the release that receives both KC input and direct DAN input. A much larger and entirely unvalidated model.

The rule adapts a baseline-centered anti-Hebbian rate rule from [Huang, Luo et al., 2024](https://doi.org/10.1038/s41586-024-07819-w): recent Kenyon-cell activity followed by dopamine tends to depress eligible connections; the reverse timing can potentiate them. Actual whole-network spike counts supply the rates, in bins of at most 10 ms. **No value from the workflow ever edits a synaptic weight directly.**

The 1-second eligibility traces, 1,800-second memory decay, 50 ms efficacy filter, gain 0.001 and efficacy bounds of 0.1–2× baseline are declared model choices. Anatomy-derived DAN-to-MBON contact fractions distribute modulation within each compartment; they are not measured dopamine concentrations or receptor kinetics. Saturation is reported, not hidden.

The PAM11/MBON07 compartment is motivated by [Ichinose et al., 2015](https://elifesciences.org/articles/10719). Applying one rule to every compartment in this male reconstruction is **our unvalidated extension**, not a replication of either paper. Real fly dopamine has context-dependent effects.

### Teaching pulses

A teaching pulse is a fixed-length, fixed-amplitude current injected into identified dopaminergic cells during a network step. Delivering it after a correct answer and a different one after a wrong answer is how a workflow pairs an outcome with plasticity.

**It is not a reward and not a punishment.** Nothing is experienced, nothing is felt, and no pain, pleasure or subjective state is modelled or measurable here. "Reward-linked" and "punishment-linked" describe what these cell types do in published behavioural experiments on flies, not what happens in this program.

## What would count as learning

This platform can show three things honestly: that an input reaches the memory cells, that identified dopaminergic cells fire when a pulse is delivered, and that pairing the two changes eligible synapses. Those are mechanism checks.

Even when weights change, useful credit assignment through a fixed decoder is unproven. Synapses move under this rule whenever dopamine arrives while cue activity is still eligible — including when that dopamine has nothing to do with the task.

To claim learned behaviour requires held-out material the network has not seen, independent starts, frozen-weight and shuffled-reinforcement controls, equal opportunity for every condition, evidence that a benefit persists, and evidence that it disappears when the learned weights are reset. A rising score alone is not evidence: a workflow that always answers the same way scores well on a task where that answer is usually right.

**No profitable, useful or general learning has been demonstrated by this repository's tests.** See [validation](validation.md) for the narrower checks actually performed.
