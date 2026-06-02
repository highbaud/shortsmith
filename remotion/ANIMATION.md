# Animation guide — shortsmith motion language

How the Remotion layer (captions + VFX transitions + b-roll) should move. This
translates a general motion vocabulary into **rules for this project** so every
render feels intentional, and so we can prompt changes by name ("make the
word-pop a spring", "stagger the caption entrance", "add anticipation to the
hook slam").

Shortsmith's job: a viewer sees a short for ~40–60s, often watching many in a
row. So motion must be **purposeful, fast, and subtle** — it orients and
emphasizes, never decorates. Frequency of use is high → keep it short.

## Non-negotiables (apply to ALL motion here)

- **Purposeful only.** Every animation orients, gives feedback, or shows a
  relationship (which word is spoken, that a stat just landed). No motion for
  decoration.
- **GPU-only properties.** Animate **`transform` + `opacity`** only. Never
  animate `width/height/top/left` (layout thrash → jank). Use `transform: scale/
  translate/rotate`. This keeps 60fps and avoids dropped frames.
- **Ease-out is the default** for anything appearing or responding. Ease-in-out
  for elements already on screen moving A→B. **Never linear** except marquees/
  spinners. Avoid pure ease-in (feels sluggish).
- **Asymmetric easing / spring for "pop".** A symmetric curve feels dead; a quick
  accelerate + slow settle (or a real spring with a little overshoot) feels
  alive. Use for the active-word pop, stat reveals, hook slam.
- **Reduced motion.** Respect `prefers-reduced-motion`: drop pops/transitions to
  a plain fade (or none). In Remotion this is a prop/flag, not the media query —
  thread a `reducedMotion` prop so a render can disable VFX/pops.
- **Frequency → subtlety.** The caption word-pop fires on every word, so it must
  be small (scale ≤ ~1.12) and quick. The hook slam fires once, so it can be big.
- **Anticipation + follow-through** make hits feel weighted: a tiny wind-up
  before a slam, a small settle after it stops. Use springs to get follow-through
  for free.

## Per-layer application

### Captions (`src/Short.tsx`)
- **Entrance:** chunk **scale-in + fade** (currently 0.92→1 + opacity). Good —
  keep ease-out.
- **Active word = the emphasis.** Today it's a scale **pop** 1.0→1.14 + gold
  color. Upgrade target: make the pop a **spring/overshoot** (asymmetric easing)
  rather than a linear interpolate, and keep it ≤1.12 (high-frequency → subtle).
- **Stagger** the words within a chunk on entrance (small per-word delay, cascade)
  instead of all-at-once — reads as "being spoken."
- **Three states** (spoken / now / unspoken) is a good **continuity** cue — keep.
- Word spacing via per-span margins (NOT flex gap; see PROJECT_STATE bundle note).

### VFX transitions (`src/` Glare / ZoomPunch / Flash)
- These fire **in lockstep with the SFX** (hook-impact, ding, cash-register,
  wrong-answer) — that's good **orchestration** (sound + motion as one beat).
- **ZoomPunch** = a scale **pop** on the frame; give it **anticipation** (tiny
  scale-down ~30ms before the punch) + **follow-through** (spring settle) so it
  feels like an impact, not a bump.
- **Glare** = a light **sweep** (translate across) — keep it linear-ish and fast;
  it's an effect, not UI.
- **Flash** = ~90ms opacity tint — keep short; it's punctuation.
- Keep all three **≤ ~400ms** and only on high-impact beats (frequency rule).

### B-roll (`src/BRoll.tsx`)
- **Entrance:** scale-in + fade (Ken-Burns-style slow scale = subtle **parallax/
  float** so a static image feels alive). **Crossfade** out under the next.
- **Origin-aware:** logo badges should **scale from their corner**, not center
  (`transform-origin`), so they feel anchored.
- Slides yield/cross-under captions (already handled via yield windows).

### Hook / callouts (Hyperframes base, HTML+GSAP)
- The hook "slam" is the one **big** moment: **anticipation** (wind-up) →
  overshoot → **follow-through** settle. Callouts = **pop in** with slight
  overshoot, **stagger** if multiple, **origin-aware** from their anchor.

## Easing + spring defaults (use these names in code/prompts)
- UI/entrances → **ease-out** (`cubic-bezier(0.16, 1, 0.3, 1)` is a good snappy one).
- On-screen A→B → **ease-in-out**.
- Pops/impacts/settles → **spring** (snappy: higher stiffness/tension, low-ish
  damping for a touch of **bounce**; keep **perceptual duration** short).
- Glare sweep / marquee → **linear**.
- Springs are **interruptible** and carry **velocity/momentum** — prefer them when
  motion may be redirected mid-flight.

## Performance checklist
- transform/opacity only · no layout-animating properties · keep concurrent
  animations few · target 60fps (120 where available) · short durations beat long
  ones for perceived performance.

## Upgrade backlog (concrete, mapped to the vocab above)
Apply in `remotion/src`, then ALWAYS nuke the bundle cache before rendering
(`rm -rf "$TEMP"/remotion-* remotion/node_modules/.cache` — see PROJECT_STATE).
1. Active-word pop → **spring/asymmetric easing** (replace the linear interpolate),
   cap scale ≤1.12.
2. **Stagger** caption words on chunk entrance (per-word delay cascade).
3. **Anticipation + spring follow-through** on ZoomPunch.
4. `reducedMotion` prop → fade-only fallback for pops + VFX (accessibility).
5. B-roll **origin-aware** logo scale + gentle **float/parallax** on photos.
6. Verify all animated props are transform/opacity (no layout thrash).
