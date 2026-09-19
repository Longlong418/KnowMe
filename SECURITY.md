# Security

knowme runs on your own machine, with your own API keys, and reads your own
calendar, notes and messages. That makes a few things worth stating plainly.

## Reporting a vulnerability

Please **don't** open a public issue for a security problem. Use the repository's
private vulnerability reporting channel once configured, or contact the repository
owner directly.

Expect a first response within 48 hours. If it's a real issue we'll agree a
disclosure timeline with you and credit you in the fix, unless you'd rather stay
anonymous.

## What we consider in scope

- Anything that exfiltrates keys, `.env`, memory (`state.db`), traces, or
  message contents off the machine.
- Code executing at install time, or a dependency doing so.
- A local HTTP endpoint accepting instructions without the expected boundary
  checks or exposing the dashboard beyond localhost.
- Prompt injection that leads to a real side effect (a tool call, a file write,
  a message sent) rather than just a strange reply.

## What isn't a vulnerability

- **The agent can run tools that touch your stuff.** That's the product. Tools
  are listed in the dashboard and gated behind extras and flags.
- **`KNOWME_EXPERIMENTAL=1`** enables sub-agent delegation, which runs another
  coding agent locally. It's off by default and documented as experimental.
- **Your own API keys in your own `.env`.** knowme never sends them anywhere but
  the provider you configured.

## Running it safely

- Keep `.env` out of git — it's gitignored, and so are `credentials.json` and
  `*token*.json`.
- Keep the dashboard bound to `127.0.0.1` unless authentication and transport
  security are added deliberately.
- Review a community skill or extension before installing it. A `SKILL.md` is
  instructions to a model that can call tools — read it like code.
