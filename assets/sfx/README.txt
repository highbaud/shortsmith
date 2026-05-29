Drop your sound-effect files here. Use these exact names (wav or mp3 both fine;
just match the base name). Any slot you leave empty is simply skipped.

STRUCTURAL (UI / text motion)
  swipe-in.wav      played when a callout/text element appears
  swipe-out.wav     played when a callout/text element leaves
  hook-impact.wav   played on the opening hook slam (t=0)

SEMANTIC (content-triggered, used sparingly)
  cash-register.wav   on a money mention ($ / million / cash / rich / wealth ...)
  ding.wav            on a big-stat number reveal (bigstat callouts)
  whoosh.wav          generic transition (fallback for seams/reorders)

Tips:
- Short, punchy one-shots work best (0.2-0.8s). Trim silence off the front so
  the hit lands exactly on the beat.
- Keep them peaking around -6 to -3 dBFS; the mixer ducks them under speech and
  normalizes the final mix, but a clean source helps.
