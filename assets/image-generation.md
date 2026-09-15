# FLYLAB artwork

`assets/flylab.png` is drawn entirely from code by `assets/make_hero.py`:

```sh
python3 assets/make_hero.py
```

A 480x240 canvas is composed with Pillow and scaled up with nearest-neighbour
sampling, so the image is made of real pixel blocks rather than a resized
photograph. No external assets, no photography and no model-generated imagery
are involved, which is why the script rather than a prompt is the record of how
it was made.

What it shows, left to right: a hexagonal lattice standing for the compound
eye's ommatidia with a few columns lit; the pathway through lamina, medulla,
mushroom body and the dopaminergic cells that write to it; and the outputs the
platform can drive. The neuron and connection counts are the retained MaleCNS
v1.0 figures this repository actually loads.
