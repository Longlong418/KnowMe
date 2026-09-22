---
name: schedule-meeting
description: Schedule a meeting when the user asks to arrange, move, or coordinate a time with one or more people.
---

## Instructions

1. Extract the participants, purpose, preferred date or time window, duration,
   timezone, and meeting location or link.
2. If a required detail is missing, ask one concise clarification question.
3. Check the calendar before proposing a time when calendar tools are available.
4. Offer at most three available options and state the timezone explicitly.
5. Only create the event after the user confirms a specific option.
6. Include the final title, participants, time, timezone, duration, and location
   in the confirmation.

## Edge cases

- Never assume a timezone from a vague date; ask when it matters.
- Do not invite people or send notifications before confirmation.
- If no shared slot is available, explain the conflict and ask which constraint
  can move.
