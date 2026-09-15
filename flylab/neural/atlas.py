"""Catalogue of every input, output and modulatory channel in the release.

Each :class:`Channel` names a group of real cells by their published MaleCNS
type annotations and records three things the interface needs:

``name``     the technical term a neuroscientist would use;
``plain``    what the channel literally does, in ordinary language;
``detail``   what is actually modelled, and what is not.

Channels are resolved against the annotations of the loaded graph. A channel
that matches no cell in a release is reported as ``present: false`` with a
count of zero rather than being hidden, so the interface can never imply that
a capability exists when the data does not contain it.

Nothing here infers physiology from wiring. A channel says which cells exist
and how this program chooses to drive or read them. Whether that drive
resembles the biological stimulus is an open question for every channel, and
the ``fidelity`` field states the current honest answer.
"""

import re
from dataclasses import dataclass, field

import numpy as np

# Fidelity grades used throughout. They describe this implementation, not the
# quality of the underlying reconstruction.
FIDELITY = {
    "adapter": "Engineered adapter. Cells are real; the stimulus waveform is a"
    " modelling choice, not calibrated to measured responses.",
    "anatomical": "Cell identity comes from the release's annotations. No"
    " physiological calibration of any kind has been performed.",
    "readout": "A fixed, declared arithmetic readout of spike counts. It is an"
    " interface convention, not a discovered neural code.",
}


@dataclass(frozen=True)
class Channel:
    key: str
    name: str
    plain: str
    detail: str
    role: str  # input | output | modulatory | internal
    modality: str
    types: tuple = ()  # exact annotation type names
    patterns: tuple = ()  # regular expressions against the type name
    superclasses: tuple = ()  # regular expressions against the superclass
    subclasses: tuple = ()  # regular expressions against the subclass
    classes: tuple = ()  # regular expressions against the class column
    within: tuple = ()  # superclass regexes the final selection must satisfy
    units: str = "arbitrary"
    default_current: float = 20.0
    side_split: bool = True
    fidelity: str = "anatomical"
    evidence: tuple = ()
    notes: str = ""
    tags: tuple = ()


def _c(**kw):
    return Channel(**kw)


# --------------------------------------------------------------------------
# Sensory input channels: cells that carry information into the nervous system.
# --------------------------------------------------------------------------
INPUT_CHANNELS = [
    _c(
        key="vision.r1_r6",
        name="R1-R6 photoreceptors",
        plain="Main eye: brightness and motion, colour-blind",
        detail="Six outer photoreceptors per ommatidium sharing one broadband"
        " rhodopsin (Rh1). They are the fly's motion and luminance channel and"
        " feed the lamina monopolar cells L1/L2/L3. Driven here from linear"
        " sRGB luminance of the incoming image, sampled at each cell's inferred"
        " retinotopic column.",
        role="input",
        modality="vision",
        types=("R1-R6",),
        units="relative luminance 0-1",
        default_current=30.0,
        fidelity="adapter",
        evidence=("https://doi.org/10.7554/eLife.71858",),
        notes="Real photoreceptors are graded, not spiking. This uses a"
        " leaky integrate-and-fire proxy with saturating current.",
        tags=("vision", "image"),
    ),
    _c(
        key="vision.r7",
        name="R7 photoreceptors",
        plain="Ultraviolet channel of the eye",
        detail="Inner photoreceptor R7, pale (Rh3, short UV) or yellow (Rh4,"
        " long UV) depending on ommatidium subtype. Most cameras and screens"
        " emit no ultraviolet, so this channel receives no invented optical"
        " drive unless you explicitly assign it a source.",
        role="input",
        modality="vision",
        patterns=(r"^R7",),
        units="relative intensity 0-1",
        default_current=30.0,
        fidelity="adapter",
        notes="Left unstimulated by the image encoders by design. Assign it"
        " a dedicated source (for example a UV sensor) if you have one.",
        tags=("vision", "uv"),
    ),
    _c(
        key="vision.r8",
        name="R8 photoreceptors",
        plain="Blue/green colour channel of the eye",
        detail="Inner photoreceptor R8, pale (Rh5, blue) or yellow (Rh6, green)."
        " The image encoder feeds linear sRGB blue to R8p and green to R8y at"
        " each cell's inferred column.",
        role="input",
        modality="vision",
        patterns=(r"^R8",),
        units="relative intensity 0-1",
        default_current=30.0,
        fidelity="adapter",
        evidence=("https://doi.org/10.1038/s41586-023-06681-6",),
        notes="Channel-to-opsin assignment is a display convention, not a"
        " spectral sensitivity measurement.",
        tags=("vision", "colour"),
    ),
    _c(
        key="vision.dra",
        name="Dorsal rim area photoreceptors",
        plain="Sky-polarisation detector along the top edge of the eye",
        detail="Specialised R7/R8 of the dorsal rim, used by flies for"
        " polarised-skylight compass orientation. No polarisation model is"
        " implemented; the cells are listed so they can be addressed directly.",
        role="input",
        modality="vision",
        types=("R7d", "R8d"),
        patterns=(r"DRA",),
        units="relative intensity 0-1",
        fidelity="anatomical",
        tags=("vision", "compass"),
    ),
    _c(
        key="vision.ocelli",
        name="Ocellar pathway",
        plain="Three simple eyes on top of the head: fast light-level changes",
        detail="Ocellar photoreceptors and the ocellar ganglion cells (OCG)"
        " that relay them. Flies use these for rapid horizon and light-level"
        " stabilisation rather than image formation.",
        role="input",
        modality="vision",
        types=("HBeyelet",),
        patterns=(r"^OCG", r"^OC\d", r"^OLP", r"ocellar"),
        units="relative luminance 0-1",
        fidelity="anatomical",
        tags=("vision", "stabilisation"),
    ),
    _c(
        key="olfaction.orn",
        name="Olfactory receptor neurons (ORNs)",
        plain="Smell: antennal and palp receptor neurons",
        detail="Each ORN class expresses one odorant receptor and converges on"
        " a single antennal-lobe glomerulus. Driving a named glomerulus is the"
        " closest this platform gets to presenting an odour: real odours"
        " activate many glomeruli in a concentration-dependent pattern.",
        role="input",
        modality="olfaction",
        patterns=(r"^ORN", r"^OSN"),
        units="normalised activation 0-1",
        default_current=20.0,
        fidelity="adapter",
        notes="Glomerulus names are exposed individually so a workflow can"
        " address, for example, the CO2-sensitive or pheromone channels.",
        tags=("smell", "chemical"),
    ),
    _c(
        key="olfaction.hrn",
        name="Hygrosensory receptor neurons",
        plain="Humidity sensing in the antenna",
        detail="Moist- and dry-cell receptor neurons of the sacculus, feeding"
        " humidity-coding glomeruli.",
        role="input",
        modality="hygrosensation",
        patterns=(r"^HRN",),
        classes=(r"^hygrosensory$",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("humidity",),
    ),
    _c(
        key="thermo.trn",
        name="Thermosensory receptor neurons",
        plain="Temperature sensing in the antenna and head",
        detail="Cool- and warm-responsive antennal receptor neurons, plus"
        " anterior-cell thermoreceptors. Useful for driving a temperature"
        " reading from a real sensor into the channel biology already uses.",
        role="input",
        modality="thermosensation",
        patterns=(r"^TRN", r"^AC_", r"^HC_", r"^CC_"),
        classes=(r"^thermosensory$",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("temperature",),
    ),
    _c(
        key="taste.sugar",
        name="Sugar gustatory receptor neurons",
        plain="Taste: sweet, on the proboscis",
        detail="Labellar bristle gustatory neurons of the sweet class. Sugar"
        " taste is the canonical natural appetitive reinforcer in Drosophila"
        " learning experiments, reaching the mushroom body through PAM"
        " dopaminergic cells.",
        role="input",
        modality="gustation",
        types=("LB3c",),
        patterns=(r"^Gr64", r"^Gr5a", r"sugar"),
        units="normalised activation 0-1",
        fidelity="adapter",
        notes="LB3c is the sweet-class assignment this project has used since"
        " the original build; it is an annotation-based assignment, not a"
        " recording from these cells.",
        tags=("taste", "reward"),
    ),
    _c(
        key="taste.bitter",
        name="Bitter gustatory receptor neurons",
        plain="Taste: bitter/aversive, on the proboscis",
        detail="Labellar bristle gustatory neurons of the bitter class, the"
        " canonical natural aversive taste input. MaleCNS v1.0 annotates taste"
        " neurons by sensillum and body part but not by response class, so this"
        " channel resolves to nothing there and is reported absent rather than"
        " guessed at. Use 'All taste neurons' to reach the population.",
        role="input",
        modality="gustation",
        patterns=(r"^Gr66", r"^Gr33", r"bitter"),
        units="normalised activation 0-1",
        fidelity="anatomical",
        notes="Absent from MaleCNS v1.0: that release does not label which"
        " gustatory neurons are bitter-responsive.",
        tags=("taste", "punishment"),
    ),
    _c(
        key="taste.gustatory",
        name="All gustatory neurons",
        plain="Every taste neuron the release identifies",
        detail="The complete gustatory class: labellar and taste-peg bristles,"
        " pharyngeal sensilla, and the taste bristles of the legs and wings,"
        " which is where a fly first tastes what it stands on.",
        role="input",
        modality="gustation",
        classes=(r"^gustatory$",),
        subclasses=(r"taste",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("taste",),
    ),
    _c(
        key="taste.labellar",
        name="Labellar and pharyngeal sensory neurons",
        plain="All taste and touch neurons of the mouthparts",
        detail="The complete labellar bristle (LB), taste peg (TP) and"
        " pharyngeal sensory population, including water and mechanosensory"
        " classes that are not sweet or bitter.",
        role="input",
        modality="gustation",
        patterns=(r"^LB\d", r"^TP\d", r"^Ph\b", r"^PhG"),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("taste", "mouthparts"),
    ),
    _c(
        key="mechano.johnston",
        name="Johnston's organ (JO-A to JO-F)",
        plain="Ear and wind/gravity sensor in the antenna",
        detail="The antennal chordotonal organ. JO-A and JO-B are tuned to"
        " antennal vibration, so they carry near-field sound including"
        " courtship song; JO-C, JO-D, JO-E and JO-F respond to sustained"
        " deflection, which is how the fly senses wind and gravity.",
        role="input",
        modality="mechanosensation",
        patterns=(r"^JO-", r"^JO_"),
        subclasses=(r"^auditory$",),
        units="normalised activation 0-1",
        default_current=20.0,
        fidelity="adapter",
        notes="Sound is delivered as band energy per subgroup. No model of"
        " antennal mechanics, phase locking or frequency tuning is included.",
        tags=("sound", "wind", "gravity"),
    ),
    _c(
        key="mechano.bristle",
        name="Bristle mechanosensory neurons",
        plain="Touch: each body bristle reports being bent",
        detail="Mechanosensory neurons innervating bristles across head,"
        " thorax, legs and wings. A single bristle firing is the fly's"
        " equivalent of a contact switch closing.",
        role="input",
        modality="mechanosensation",
        patterns=(r"^BM_", r"^BM\d", r"bristle"),
        subclasses=(r"bristle",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("touch", "contact"),
    ),
    _c(
        key="mechano.wind",
        name="Wind-sensitive projection neurons",
        plain="Air-current direction relay",
        detail="Antennal mechanosensory projection neurons carrying wind"
        " direction from the antennal mechanosensory and motor centre toward"
        " the central complex.",
        role="input",
        modality="mechanosensation",
        patterns=(r"^WPN", r"^APN\d", r"^aPN"),
        subclasses=(r"wind",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("wind",),
    ),
    _c(
        key="proprio.feco",
        name="Femoral chordotonal organ (FeCO)",
        plain="Joint-angle sensor in each leg",
        detail="Club, claw and hook neurons reporting femur-tibia joint angle,"
        " movement and vibration. This is the fly's proprioceptive position"
        " feedback, the natural target for a servo or encoder reading.",
        role="input",
        modality="proprioception",
        patterns=(r"^FeCO", r"^club", r"^claw", r"^hook", r"^SNch"),
        subclasses=(r"chordotonal",),
        units="normalised joint angle 0-1",
        fidelity="adapter",
        tags=("proprioception", "joint"),
    ),
    _c(
        key="proprio.campaniform",
        name="Campaniform sensilla",
        plain="Strain/load sensors in the cuticle",
        detail="Campaniform sensilla report mechanical load on legs, wings and"
        " halteres. They are the fly's force feedback.",
        role="input",
        modality="proprioception",
        patterns=(r"^CS_", r"campaniform"),
        subclasses=(r"campaniform",),
        units="normalised load 0-1",
        fidelity="anatomical",
        tags=("force", "load"),
    ),
    _c(
        key="proprio.hairplate",
        name="Hair plates",
        plain="Joint end-stop sensors",
        detail="Clusters of short mechanosensory hairs at joints that report"
        " extreme joint positions.",
        role="input",
        modality="proprioception",
        patterns=(r"^HP_", r"hair_plate", r"hairplate"),
        subclasses=(r"hair plate",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("proprioception",),
    ),
    _c(
        key="nociception.md",
        name="Multidendritic nociceptors",
        plain="Damage-sensing neurons of the body wall",
        detail="Class IV multidendritic neurons respond to noxious heat and"
        " mechanical damage in larvae and persist in the adult. They are"
        " listed so their anatomy can be addressed; stimulating them is an"
        " engineered current injection and models neither pain nor any"
        " subjective state.",
        role="input",
        modality="nociception",
        patterns=(r"^MD\b", r"^md-", r"^ddaC", r"nociceptor"),
        units="normalised activation 0-1",
        fidelity="anatomical",
        notes="No pain, suffering or subjective experience is modelled,"
        " claimed or measurable here.",
        tags=("nociception",),
    ),
    _c(
        key="ascending.vnc",
        name="Ascending neurons",
        plain="Body-to-brain feedback from the nerve cord",
        detail="Neurons whose somata lie in the ventral nerve cord and whose"
        " axons ascend to the brain, carrying limb state, walking status and"
        " reafferent signals.",
        role="input",
        modality="interoception",
        patterns=(r"^AN\b", r"^AN_", r"^AN\d"),
        superclasses=(r"ascending",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("feedback", "body"),
    ),
    _c(
        key="sensory.all",
        name="All annotated sensory neurons",
        plain="Every cell the release marks as sensory",
        detail="The complete sensory superclass. Use it to check coverage or"
        " to drive a broad, deliberately unselective stimulus during a"
        " plumbing test.",
        role="input",
        modality="all",
        superclasses=(r"sensory",),
        units="normalised activation 0-1",
        fidelity="anatomical",
        tags=("diagnostic",),
    ),
]

# --------------------------------------------------------------------------
# Motor and output channels: cells whose activity this platform reads out.
# --------------------------------------------------------------------------
OUTPUT_CHANNELS = [
    _c(
        key="motor.descending",
        name="Descending neurons (all)",
        plain="Brain-to-body commands: the fly's complete output bus",
        detail="Every cell the release marks as descending. These carry the"
        " brain's behavioural commands to the ventral nerve cord, which"
        " generates the actual leg and wing patterns.",
        role="output",
        modality="locomotion",
        superclasses=(r"descending",),
        patterns=(r"^DN", r"^MDN"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "command"),
    ),
    _c(
        key="motor.steering",
        name="DNa02 steering pair",
        plain="Turn left / turn right",
        detail="DNa02 is a bilateral pair whose asymmetric activity correlates"
        " with turning in walking flies: more activity on one side"
        " accompanies a turn toward that side. Reading right minus left as a"
        " steering value is this platform's declared convention.",
        role="output",
        modality="locomotion",
        types=("DNa02",),
        units="spikes/second (right minus left)",
        fidelity="readout",
        evidence=("https://doi.org/10.1016/j.cub.2021.09.010",),
        tags=("motor", "steering", "differential"),
    ),
    _c(
        key="motor.walk_forward",
        name="DNa01 / DNb06 forward-walking neurons",
        plain="Walk forward / change forward speed",
        detail="Descending neurons associated with forward walking and course"
        " control in published recordings. Read as a forward-speed command.",
        role="output",
        modality="locomotion",
        types=("DNa01", "DNb06", "DNa03"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "forward"),
    ),
    _c(
        key="motor.backward",
        name="Moonwalker descending neurons (MDN)",
        plain="Walk backward",
        detail="MDN activation drives backward walking in freely moving flies;"
        " it is one of the best-characterised command-like descending cell"
        " types in the adult.",
        role="output",
        modality="locomotion",
        patterns=(r"^MDN",),
        units="spikes/second",
        fidelity="readout",
        evidence=("https://doi.org/10.1126/science.1254933",),
        tags=("motor", "reverse"),
    ),
    _c(
        key="motor.stop",
        name="DNp09 stopping neurons",
        plain="Stop / freeze",
        detail="DNp09 activation produces freezing and locomotor arrest.",
        role="output",
        modality="locomotion",
        types=("DNp09",),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "stop"),
    ),
    _c(
        key="motor.escape",
        name="Giant fibre and escape descending neurons",
        plain="Jump / escape takeoff",
        detail="The giant fibre (DNp01) and associated escape pathway cells"
        " trigger the fast takeoff response.",
        role="output",
        modality="escape",
        types=("DNp01", "DNp02", "DNp03", "DNp04", "DNp11"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "escape"),
    ),
    _c(
        key="motor.gate",
        name="DNpe017",
        plain="Commit gate: only act when this fires",
        detail="A descending cell used by this platform as an explicit"
        " gate: downstream blocks can require at least one spike here before"
        " an action is allowed. That is an interface convention chosen for"
        " determinism, not a described biological function of the cell.",
        role="output",
        modality="locomotion",
        types=("DNpe017",),
        units="spikes per window",
        fidelity="readout",
        tags=("gate", "commit"),
    ),
    _c(
        key="motor.dnp20",
        name="DNp20 pair",
        plain="Bilateral descending pair usable as a two-sided readout",
        detail="A left/right descending pair. The earlier build of this"
        " project decoded right-minus-left DNp20 rate as a binary choice; it"
        " remains available as a general differential readout.",
        role="output",
        modality="locomotion",
        types=("DNp20",),
        units="spikes/second (right minus left)",
        fidelity="readout",
        tags=("differential",),
    ),
    _c(
        key="motor.leg",
        name="Leg motor neurons",
        plain="Individual leg muscles",
        detail="Ventral-nerve-cord motor neurons innervating leg muscles."
        " In the animal these are driven by nerve-cord circuits, not directly"
        " by the brain; reading them gives the finest-grained motor output the"
        " reconstruction supports.",
        role="output",
        modality="locomotion",
        subclasses=(r"^(fl|ml|hl)$",),
        patterns=(r"(?:Fe|Ti|Ta|Tr|Co|ti|tr|ta) .*MN", r"^MNfl", r"^MNml", r"^MNhl"),
        within=(r"motor", r"efferent"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "leg"),
    ),
    _c(
        key="motor.wing",
        name="Wing motor neurons",
        plain="Flight and song muscles",
        detail="Direct and indirect flight muscle motor neurons (b1, b2, i1,"
        " i2, iii1, iii3, hg1-hg4, ps1, tp1, tp2). The same muscles produce"
        " courtship song.",
        role="output",
        modality="flight",
        patterns=(r"^MNwm", r"^(b1|b2|i1|i2|iii1|iii3|hg[1-4]|ps1|tp[12])$", r"^DVMn", r"^DLMn"),
        subclasses=(r"^wm$",),
        within=(r"motor", r"efferent"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "wing", "song"),
    ),
    _c(
        key="motor.neck",
        name="Neck motor neurons",
        plain="Head movement / gaze stabilisation",
        detail="Motor neurons of the neck muscles, which stabilise the head"
        " against body rotation.",
        role="output",
        modality="locomotion",
        patterns=(r"^MNnm", r"neck", r"^CvN\d"),
        subclasses=(r"^nm$",),
        within=(r"motor", r"efferent"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "neck"),
    ),
    _c(
        key="motor.proboscis",
        name="Proboscis motor neurons",
        plain="Extend the proboscis / feed",
        detail="MN9 and related motor neurons extend the proboscis. Proboscis"
        " extension is the standard behavioural readout in fly appetitive"
        " conditioning experiments.",
        role="output",
        modality="feeding",
        patterns=(r"^MN(9|1[0-3])", r"^MNpm"),
        subclasses=(r"^pm$",),
        within=(r"motor", r"efferent"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor", "feeding"),
    ),
    _c(
        key="motor.haltere",
        name="Haltere motor neurons",
        plain="Gyroscope control muscles",
        detail="Motor neurons of the haltere, the fly's mechanical rate"
        " gyroscope.",
        role="output",
        modality="flight",
        patterns=(r"haltere", r"^MNhm"),
        subclasses=(r"^hm$",),
        within=(r"motor", r"efferent"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor",),
    ),
    _c(
        key="motor.abdomen",
        name="Abdominal motor neurons",
        plain="Abdomen and reproductive muscles",
        detail="Motor neurons of the abdominal segments. In the male these"
        " include the muscles of copulation, which is a large part of why this"
        " release exists.",
        role="output",
        modality="locomotion",
        subclasses=(r"^ad$",),
        patterns=(r"^MNad",),
        within=(r"motor", r"efferent"),
        units="spikes/second",
        fidelity="readout",
        tags=("motor",),
    ),
    _c(
        key="motor.all",
        name="All annotated motor neurons",
        plain="Every cell the release marks as motor",
        detail="The complete motor superclass, for coverage checks and broad"
        " readouts.",
        role="output",
        modality="all",
        superclasses=(r"motor", r"efferent"),
        units="spikes/second",
        fidelity="readout",
        tags=("diagnostic",),
    ),
    _c(
        key="memory.mbon",
        name="Mushroom body output neurons (MBONs)",
        plain="Learned value: what the fly has learned about what it senses",
        detail="MBONs are the read-out of mushroom-body memory. Their relative"
        " activity is the closest thing the fly has to a learned approach/avoid"
        " signal, and it is exactly what the plasticity rule in this platform"
        " modifies.",
        role="output",
        modality="memory",
        patterns=(r"^MBON\d+",),
        classes=(r"^MBON$",),
        units="spikes/second",
        fidelity="readout",
        evidence=("https://doi.org/10.7554/eLife.04577",),
        tags=("memory", "valence"),
    ),
    _c(
        key="compass.epg",
        name="EPG compass neurons",
        plain="Internal heading: which way the fly thinks it is facing",
        detail="Ellipsoid-body EPG cells carry a single activity bump that"
        " tracks heading like a compass needle. Reading the bump's angular"
        " position gives an allocentric heading estimate.",
        role="output",
        modality="navigation",
        patterns=(r"^EPG",),
        units="bump angle, degrees",
        fidelity="readout",
        evidence=("https://doi.org/10.1038/nature14446",),
        tags=("navigation", "heading"),
    ),
    _c(
        key="compass.pfl3",
        name="PFL3 steering output",
        plain="Goal-directed steering command",
        detail="PFL3 cells compare the heading bump with a goal direction and"
        " project to descending steering pathways. They are the central"
        " complex's turn command.",
        role="output",
        modality="navigation",
        patterns=(r"^PFL3",),
        units="spikes/second (right minus left)",
        fidelity="readout",
        evidence=("https://doi.org/10.1038/s41586-024-07006-x",),
        tags=("navigation", "steering"),
    ),
    _c(
        key="compass.fc2",
        name="FC2 goal-direction neurons",
        plain="Desired heading: where the fly wants to go",
        detail="Fan-shaped body FC2 cells carry the goal-direction bump that"
        " PFL3 compares against current heading.",
        role="output",
        modality="navigation",
        patterns=(r"^FC2",),
        units="bump angle, degrees",
        fidelity="readout",
        tags=("navigation", "goal"),
    ),
    _c(
        key="endocrine.all",
        name="Endocrine and neurosecretory cells",
        plain="Hormone release: hunger, stress, growth state",
        detail="Neurosecretory cells including the insulin-producing cells."
        " Their outputs are hormonal and act on timescales this simulation"
        " does not model; they are exposed for anatomical completeness.",
        role="output",
        modality="endocrine",
        superclasses=(r"endocrine",),
        patterns=(r"^IPC", r"^DH\d+", r"^Hugin", r"^CAPA", r"^Crz", r"^AstA", r"^ITP$", r"^LK$", r"^DMS$"),
        units="spikes/second",
        fidelity="anatomical",
        notes="No hormone diffusion, receptor binding or metabolic state is"
        " simulated.",
        tags=("hormone",),
    ),
]

# --------------------------------------------------------------------------
# Modulatory channels: the cells a training signal is delivered to.
# --------------------------------------------------------------------------
MODULATORY_CHANNELS = [
    _c(
        key="teach.pam",
        name="PAM dopaminergic cluster",
        plain="Reward teaching signal (the 'that was good' input)",
        detail="Roughly a hundred protocerebral anterior medial dopaminergic"
        " cells innervating the mushroom-body medial lobe. In behavioural"
        " experiments these carry appetitive reinforcement such as sugar."
        " Injecting current here is an engineered teaching signal: the network"
        " is not being given a reward and experiences nothing.",
        role="modulatory",
        modality="reinforcement",
        patterns=(r"^PAM\d+",),
        units="millivolt-equivalent current",
        default_current=20.0,
        fidelity="adapter",
        evidence=("https://elifesciences.org/articles/10719",),
        tags=("training", "reward"),
    ),
    _c(
        key="teach.pam11",
        name="PAM11 (alpha1 compartment)",
        plain="Reward teaching signal, single compartment",
        detail="The fifteen PAM11 cells innervating the alpha1 compartment and"
        " its output neuron MBON07. This is the narrow, validated reward"
        " target used for every recorded result in this project.",
        role="modulatory",
        modality="reinforcement",
        types=("PAM11",),
        units="millivolt-equivalent current",
        default_current=20.0,
        fidelity="adapter",
        tags=("training", "reward", "validated"),
    ),
    _c(
        key="teach.ppl1",
        name="PPL1 dopaminergic cluster",
        plain="Punishment teaching signal (the 'that was wrong' input)",
        detail="Protocerebral posterior lateral dopaminergic cells innervating"
        " the vertical lobe and heel. In behavioural experiments these carry"
        " aversive reinforcement such as electric shock or bitter taste."
        " Injecting current here is an engineered teaching signal and models"
        " neither pain nor any subjective state.",
        role="modulatory",
        modality="reinforcement",
        patterns=(r"^PPL1\d+",),
        units="millivolt-equivalent current",
        default_current=20.0,
        fidelity="adapter",
        tags=("training", "punishment"),
    ),
    _c(
        key="teach.ppl101",
        name="PPL101 (gamma1pedc compartment)",
        plain="Punishment teaching signal, single compartment",
        detail="The two PPL101 cells innervating gamma1pedc and its output"
        " neuron MBON11. The narrow, validated punishment target used for"
        " every recorded result in this project.",
        role="modulatory",
        modality="reinforcement",
        types=("PPL101",),
        units="millivolt-equivalent current",
        default_current=20.0,
        fidelity="adapter",
        tags=("training", "punishment", "validated"),
    ),
    _c(
        key="teach.octopamine",
        name="Octopaminergic cells (VUM/VPM)",
        plain="Arousal and appetitive state",
        detail="Octopamine is the invertebrate counterpart of noradrenaline."
        " VUMa and VPM cells modulate arousal, flight and appetitive"
        " reinforcement.",
        role="modulatory",
        modality="neuromodulation",
        patterns=(r"^OA-", r"^VUM", r"^VPM\d"),
        units="millivolt-equivalent current",
        fidelity="anatomical",
        tags=("arousal",),
    ),
    _c(
        key="teach.serotonin",
        name="Serotonergic cells",
        plain="Global state modulation",
        detail="Serotonergic neurons including CSDn, which broadly modulates"
        " olfactory processing with internal state.",
        role="modulatory",
        modality="neuromodulation",
        patterns=(r"^CSD", r"^5-HT", r"^SER-"),
        units="millivolt-equivalent current",
        fidelity="anatomical",
        tags=("state",),
    ),
    _c(
        key="clock.circadian",
        name="Circadian clock neurons",
        plain="Time of day",
        detail="Lateral and dorsal clock neurons (s-LNv, l-LNv, LNd, DN1-DN3)."
        " Their molecular oscillator is not simulated; only the cells and their"
        " connectivity are present.",
        role="modulatory",
        modality="circadian",
        patterns=(r"^l-LNv", r"^s-LNv", r"^LNd", r"^DN[123]$", r"^LPN$"),
        units="millivolt-equivalent current",
        fidelity="anatomical",
        tags=("time",),
    ),
]

# --------------------------------------------------------------------------
# Internal populations, exposed for the connectome viewport and for probing.
# --------------------------------------------------------------------------
INTERNAL_CHANNELS = [
    _c(
        key="internal.lamina",
        name="Lamina monopolar cells (L1-L5)",
        plain="First visual relay: contrast and edges",
        detail="L1 and L2 are the direct postsynaptic partners of R1-R6 and"
        " split the signal into ON and OFF motion pathways; L3 carries"
        " sustained contrast. They receive a tonic bias current in this"
        " implementation, which is a display adapter, not measured physiology.",
        role="internal",
        modality="vision",
        types=("L1", "L2", "L3", "L4", "L5"),
        fidelity="adapter",
        tags=("vision",),
    ),
    _c(
        key="internal.motion",
        name="T4 / T5 motion detectors",
        plain="Direction-selective motion cells",
        detail="T4 (ON) and T5 (OFF) cells are the fly's elementary motion"
        " detectors, each subtype tuned to one of four cardinal directions.",
        role="internal",
        modality="vision",
        patterns=(r"^T4[a-d]?$", r"^T5[a-d]?$", r"^Tm", r"^Mi\d"),
        fidelity="anatomical",
        tags=("vision", "motion"),
    ),
    _c(
        key="internal.lobula_lc",
        name="Lobula columnar cells (LC)",
        plain="Visual feature detectors: looming, small objects, bars",
        detail="Lobula columnar types project visual features to the central"
        " brain. LC4 and LPLC2 are looming-sensitive and drive escape; LC11"
        " responds to small moving objects.",
        role="internal",
        modality="vision",
        patterns=(r"^LC\d+", r"^LPLC\d+", r"^LLPC\d+", r"^LPC\d+"),
        fidelity="anatomical",
        tags=("vision", "features"),
    ),
    _c(
        key="internal.kenyon",
        name="Kenyon cells (KC)",
        plain="Memory cells: the sparse code that gets modified",
        detail="Mushroom-body intrinsic neurons. Their sparse, high-dimensional"
        " activity is the substrate the plasticity rule writes to.",
        role="internal",
        modality="memory",
        patterns=(r"^KC",),
        classes=(r"^Kenyon_Cell$",),
        fidelity="anatomical",
        tags=("memory",),
    ),
    _c(
        key="internal.antennal_lobe",
        name="Antennal lobe projection neurons",
        plain="Smell relay to memory and innate centres",
        detail="Projection neurons carrying glomerular odour signals to the"
        " mushroom body calyx and lateral horn.",
        role="internal",
        modality="olfaction",
        patterns=(r"^M_", r"^lPN", r"^adPN", r"^vPN", r"^ilPN"),
        classes=(r"^AL(PN|LN|IN|ON)$",),
        fidelity="anatomical",
        tags=("smell",),
    ),
    _c(
        key="internal.central_complex",
        name="Central complex",
        plain="Navigation and action-selection core",
        detail="Ellipsoid body, fan-shaped body, protocerebral bridge and"
        " noduli. Holds the heading representation and goal comparison.",
        role="internal",
        modality="navigation",
        patterns=(
            r"^EPG", r"^PEG", r"^PEN", r"^Delta7", r"^ER\d", r"^EL\b",
            r"^FC\d", r"^FS\d", r"^PFL\d", r"^PFR", r"^hDelta", r"^vDelta",
            r"^FB\d", r"^ExR\d", r"^LNO", r"^P[EF]N",
        ),
        classes=(r"^CX$",),
        fidelity="anatomical",
        tags=("navigation",),
    ),
    _c(
        key="internal.lateral_horn",
        name="Lateral horn",
        plain="Innate odour valence",
        detail="Lateral-horn neurons implement the fly's genetically specified"
        " responses to odours, in parallel with learned mushroom-body output.",
        role="internal",
        modality="olfaction",
        patterns=(r"^LH[A-Z]{2}",),
        fidelity="anatomical",
        tags=("smell", "innate"),
    ),
    _c(
        key="internal.all",
        name="Central brain intrinsic neurons",
        plain="Everything between input and output",
        detail="The intrinsic superclass: the bulk of the network, where the"
        " 166,700-cell graph does its integration.",
        role="internal",
        modality="all",
        superclasses=(r"intrinsic",),
        fidelity="anatomical",
        tags=("diagnostic",),
    ),
]

CHANNELS = INPUT_CHANNELS + OUTPUT_CHANNELS + MODULATORY_CHANNELS + INTERNAL_CHANNELS
BY_KEY = {c.key: c for c in CHANNELS}

MODALITIES = {
    "vision": "Sight",
    "olfaction": "Smell",
    "gustation": "Taste",
    "mechanosensation": "Touch, sound, wind",
    "proprioception": "Body position and load",
    "thermosensation": "Temperature",
    "hygrosensation": "Humidity",
    "nociception": "Tissue damage signalling",
    "interoception": "Internal body state",
    "locomotion": "Walking and turning",
    "flight": "Flight and wing muscles",
    "feeding": "Proboscis and feeding",
    "escape": "Escape and takeoff",
    "navigation": "Heading and goal direction",
    "memory": "Learning and memory",
    "reinforcement": "Teaching signals",
    "neuromodulation": "Global state",
    "circadian": "Time of day",
    "endocrine": "Hormonal output",
    "all": "Whole-population diagnostic",
}


@dataclass
class ResolvedChannel:
    channel: Channel
    index: np.ndarray
    left: np.ndarray
    right: np.ndarray
    middle: np.ndarray
    subtypes: dict = field(default_factory=dict)

    @property
    def present(self):
        return bool(len(self.index))

    def summary(self):
        c = self.channel
        return {
            "key": c.key,
            "name": c.name,
            "plain": c.plain,
            "detail": c.detail,
            "role": c.role,
            "modality": c.modality,
            "modality_plain": MODALITIES.get(c.modality, c.modality),
            "units": c.units,
            "default_current": c.default_current,
            "fidelity": c.fidelity,
            "fidelity_note": FIDELITY.get(c.fidelity, ""),
            "evidence": list(c.evidence),
            "notes": c.notes,
            "tags": list(c.tags),
            "present": self.present,
            "cells": int(len(self.index)),
            "cells_left": int(len(self.left)),
            "cells_right": int(len(self.right)),
            "cells_unsided": int(len(self.middle)),
            "subtypes": {k: int(len(v)) for k, v in sorted(self.subtypes.items())},
        }


def _compile(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns)) if patterns else None


def _matches(values, patterns):
    rx = _compile(patterns)
    if rx is None:
        return None
    return np.asarray([bool(rx.search(v)) for v in values], dtype=bool)


def resolve_channel(channel, types, superclasses, sides, subclasses=None, classes=None):
    """Return the cell indices a channel refers to in this release.

    A channel can name cells two ways: by type name, and by the grouping
    columns the release publishes alongside it (superclass, subclass, class).
    The grouping columns are an extra way in, so a release that leaves a type
    name blank is still covered. ``within`` then narrows the result to one
    superclass, which is how a leg motor neuron is separated from a descending
    neuron that happens to carry the same body-part subclass.
    """
    count = len(types)
    subclasses = np.full(count, "") if subclasses is None else subclasses
    classes = np.full(count, "") if classes is None else classes
    by_name = np.zeros(count, dtype=bool)
    if channel.types:
        by_name |= np.isin(types, list(channel.types))
    named = _matches(types, channel.patterns)
    if named is not None:
        by_name |= named
    by_group = None
    for values, patterns in (
        (superclasses, channel.superclasses),
        (subclasses, channel.subclasses),
        (classes, channel.classes),
    ):
        found = _matches(values, patterns)
        if found is not None:
            by_group = found if by_group is None else (by_group | found)
    if by_group is None:
        selected = by_name
    elif not (channel.types or channel.patterns):
        selected = by_group
    else:
        selected = by_name | by_group
    # ``within`` is the narrowing pass: a leg motor neuron and a descending
    # neuron that steers a leg can share a subclass, and only one of them is a
    # motor neuron.
    limit = _matches(superclasses, channel.within)
    if limit is not None:
        selected = selected & limit
    index = np.flatnonzero(selected).astype(np.int32)
    side = sides[index]
    left = index[side == "L"]
    right = index[side == "R"]
    middle = index[(side != "L") & (side != "R")]
    subtypes = {}
    for i in index:
        subtypes.setdefault(str(types[i]), []).append(int(i))
    return ResolvedChannel(
        channel=channel,
        index=index,
        left=left.astype(np.int32),
        right=right.astype(np.int32),
        middle=middle.astype(np.int32),
        subtypes={k: np.asarray(v, dtype=np.int32) for k, v in subtypes.items()},
    )


class Atlas:
    """Every channel resolved against one loaded graph."""

    def __init__(self, types, superclasses, sides, subclasses=None, classes=None,
                 channels=CHANNELS):
        def text(values):
            return np.asarray([("" if v is None else str(v)) for v in values])

        self.types = text(types)
        self.superclasses = text(superclasses)
        self.sides = text(sides)
        blank = [""] * len(self.types)
        self.subclasses = text(blank if subclasses is None else subclasses)
        self.classes = text(blank if classes is None else classes)
        self.resolved = {
            c.key: resolve_channel(c, self.types, self.superclasses, self.sides,
                                   self.subclasses, self.classes)
            for c in channels
        }

    @classmethod
    def from_annotations(cls, a, superclass=None, channels=CHANNELS):
        def column(name, fallback=None):
            if name in a:
                return a[name].fillna("").astype(str).to_numpy()
            return fallback if fallback is not None else [""] * len(a)

        types = column("type")
        superclasses = column(
            "superclass",
            superclass if superclass is not None else [""] * len(a),
        )
        # Sensory neurons have no soma inside the CNS, so somaSide is blank
        # for them and rootSide - which nerve they enter through - is the only
        # laterality the release gives. Prefer the soma, fall back per cell.
        soma = column("somaSide")
        root = column("rootSide")
        sides = [s if s else r for s, r in zip(soma, root)]
        return cls(types, superclasses, sides, column("subclass"), column("class"),
                   channels)

    def __getitem__(self, key):
        if key not in self.resolved:
            raise KeyError(f"Unknown channel: {key}")
        return self.resolved[key]

    def indices(self, key, side="both"):
        r = self[key]
        return {"both": r.index, "left": r.left, "right": r.right, "middle": r.middle}[side]

    def present(self, role=None):
        return [
            r
            for r in self.resolved.values()
            if r.present and (role is None or r.channel.role == role)
        ]

    def catalogue(self):
        """Full machine-readable catalogue for the interface."""
        return {
            "modalities": MODALITIES,
            "fidelity": FIDELITY,
            "channels": [r.summary() for r in self.resolved.values()],
            "present": sum(1 for r in self.resolved.values() if r.present),
            "total": len(self.resolved),
            "annotated_types": int(len({t for t in self.types if t})),
        }

    def coverage(self):
        """How much of the graph any channel accounts for."""
        touched = np.zeros(len(self.types), dtype=bool)
        for r in self.resolved.values():
            touched[r.index] = True
        return {
            "neurons": int(len(self.types)),
            "in_at_least_one_channel": int(touched.sum()),
            "unassigned": int((~touched).sum()),
            "note": "Unassigned cells still integrate normally. Channels are"
            " addressing labels, not a partition of the network.",
        }
