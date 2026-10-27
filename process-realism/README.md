# Process Trace: wing texture

A live, self-contained sketch (`butterfly.html`, no dependencies). It takes only the texture of a
swallowtail wing, the pale rays running between dark veins, and lets it grow across the whole
screen in two flat tones. Open the file in a browser.

**Rules**

1. **Veins: space colonisation.** Attractor points are scattered over the surface. Each one pulls
   only its nearest vein node within reach. A node grows one step toward the average pull, keeps
   heading away from where its vein started, and forks when the pulls on it diverge. Attractors
   close to a vein are consumed, which keeps the veins evenly spaced.
2. **Pigment: diffusion and decay.** A morphogen `u` is held at 1 on the veins, diffuses, and
   decays (`du/dt = ∇²u − u/λ²`). Each cell reads `u` through a band-pass rule: high `u` is dark,
   middle `u` is pale, and low `u` is dark again. So every vein is a dark line inside a pale ray, and
   where two veins drift apart the ray splits along a dark fold before the veins fork.

The thresholds drift with a slow travelling wave, so the rays keep opening and closing.

**Mouse**
- **Click:** wounds the surface. Veins and pigment inside a disc are wiped, and a seed grows there
  while the surrounding veins grow back in.
- **Drag:** draws a vein, which sprouts side veins.
