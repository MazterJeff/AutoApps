# Process Trace: wing venation × pigment diffusion

A live, self-contained sketch (`butterfly.html`, no dependencies) that grows an abstracted
swallowtail wing pattern from two simple processes, after a photo of a black butterfly with pale
rays between its veins. Open the file in a browser.

**Rules**

1. **Veins: space colonisation.** Attractor points sit just outside the wing margin. Each one pulls
   only its nearest vein node, and a node pulled by several grows toward their average direction,
   with a slight bias away from the wing base. When those attractors drift apart, the vein forks.
   A tip stops when it reaches the margin. Growth starts from the discal cell, a closed loop near the root.
2. **Pigment: diffusion and decay.** A morphogen `u` is held at 1 on the veins, diffuses, and
   decays (`du/dt = ∇²u − k·u`). The margin is no-flux. Each scale reads `u` through a band-pass rule:
   high `u` (next to a vein) gives a dark scale, middle `u` gives a pale one, and low `u` (far from
   every vein) gives dark again, which is the interveinal fold line. Near the root the veins are
   crowded and `u` never falls far, so the wing stays black. Toward the margin the veins spread
   apart, so the pale rays open up and split down the middle.

**Where randomness enters:** attractor placement, wobble at vein tips, streaky noise added to `u`
before it is read, and the mutation of a small genome each generation (vein counts, the decay
length λ, the band thresholds, discal-cell jitter).

**Controls:** click or `n` for the next generation · `space` pause · `v` toggle the process view
(the raw field `u`, veins and attractors) · `f` wing flutter · `i` invert · `s` save PNG.
