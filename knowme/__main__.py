"""Entrypoints — installed as the `knowme` command (and `python -m knowme`):

  knowme                       chat in the terminal (default)
  knowme dashboard             the browser cockpit → localhost:8888 on Windows
  knowme connections           list configured integrations and their health
  knowme brief                 morning briefing (calendar + mail + memory) — as a LOOP
  knowme gather                same job as a GRAPH: github, web, calendar and
                             memory fetched together, then one digest
  knowme skill install <url>   install a community skill
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        from knowme.gateway.cli import main as cli_main

        cli_main()
    elif args[0] == "dashboard":
        from knowme.ops.dashboard import main as dash_main

        dash_main()
    elif args[0] == "connections":
        from knowme.integrations import cli_main

        sys.exit(cli_main())
    elif args[0] == "brief":
        from knowme.ops.brief import main as brief_main

        brief_main()
    elif args[0] == "gather":
        from knowme.ops.gather import main as gather_main

        gather_main()
    elif args[0] == "skill" and len(args) >= 3 and args[1] == "install":
        from knowme.memory.procedural.installer import install

        install(args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
