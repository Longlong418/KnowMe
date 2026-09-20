"""Context management — everything that decides WHAT goes to the model.

    tool_entries    the text shape of tool activity inside a history message
    tool_budget     per-turn bound on tool output (pointer + excerpt)
    snip_compact    per-conversation bound on message count (archive + marker)
    micro_compact   the emergency valve: older tool results become bare pointers
    state_summary   the last resort: the conversation becomes a summary of itself
"""
