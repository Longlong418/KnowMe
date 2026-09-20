"""Agent Core — the runtime every agent in this repo shares.

    loop.py       THE loop: observe → reason → act → repeat
    models.py     provider adapters: one Anthropic-shaped dialect over two wire formats
    tools.py      Tool + ToolRegistry
    session.py    one conversation's working memory and the prompt recipe
    context/      the compressors that keep a request inside the window
    events.py     the observer protocol shared by the loop, graph and tracer

Nothing in here may import knowme.ops, knowme.graph or knowme.gateway: those
are consumers of the core, and a core that reaches back into its consumers is
not a core. evals/deterministic/test_core_layout.py holds that line.
"""
