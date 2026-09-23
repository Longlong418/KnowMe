"""Entrypoints — installed as the `knowme` command (and `python -m knowme`):

  knowme                       chat in the terminal (default)
  knowme web                   the local Web client → localhost:8888 on Windows
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
    elif args[0] == "web":
        from knowme.ops.web import main as web_main

        web_main()
    elif args[0] == "connections":
        from knowme.integrations import cli_main

        sys.exit(cli_main())
    elif args[0] == "brief":
        from knowme.ops.brief import main as brief_main

        brief_main()
    elif args[0] == "gather":
        from knowme.ops.gather import main as gather_main

        gather_main()
    elif args[0] == "deep_research":
        from knowme.ops.deep_research import main as research_main

        # The topic is every remaining word, so quoting is optional but allowed:
        # python -m knowme deep_research 固态电池的产业化进度
        research_main(" ".join(args[1:]))
    elif args[0] == "skill" and len(args) >= 3 and args[1] == "install":
        from knowme.memory.procedural.installer import install

        install(args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
