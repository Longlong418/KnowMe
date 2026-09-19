"""knowme-agent — a minimal, transparent, local-first KnowMe.

Four pillars, one module each:
  harness  → knowme/runtime + knowme/gateway  (scaffolding around the raw LLM)
  loop     → knowme/loop                      (observe → reason → act → repeat)
             knowme/graph                     (opt-in structure around the loop — extends this pillar)
  memory   → knowme/memory                    (procedural / semantic / episodic)
  ops      → knowme/ops + evals/              (trace → eval → gate → release)
"""

__version__ = "0.1.0"
