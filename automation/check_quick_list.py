#!/usr/bin/env python3
"""Warn when a MacroQuest Plugin Quick List plugin has no page here.

Every plugin on the quick list (docs/projects/macroquest/main/plugin-quick-list.md, from the
macroquest fork) should be a slug in sources.yml, unless it is listed in NO_DOCS below. Just a
warning, the build never fails.

Run after fetch_sources.py, which fetches the quick list.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sources.yml"
QUICK_LIST = ROOT / "docs" / "projects" / "macroquest" / "main" / "plugin-quick-list.md"

# Repos on the quick list that publish under a different slug.
ALIASES = {
    "mq2dan": "mq2dannet",
}

# Repos on the quick list that RedGuides has decided not to document here. Lowercase repo name.
NO_DOCS = {
    "mq2autolootsort",
    "mq2headshot",
    "mq2meshmanager",
    "mqgammaless",
    "mqsearchitem",
    "mqxworks",
}

CLONE_LINE = re.compile(r"^\s*git clone\b.*?(\S+?)(?:\.git)?\s+plugins/(\S+)\s*$")


def quick_list_repos() -> list[tuple[str, str]]:
    """(repo name, clone url) for every git clone line on the quick list."""
    found: list[tuple[str, str]] = []
    for line in QUICK_LIST.read_text(encoding="utf-8").splitlines():
        if m := CLONE_LINE.match(line):
            found.append((m.group(2), m.group(1)))
    return found


def main() -> int:
    if not QUICK_LIST.exists():
        print(f"::warning::{QUICK_LIST.relative_to(ROOT)} not found; run fetch_sources.py first")
        return 0
    with MANIFEST.open(encoding="utf-8") as fh:
        slugs = {s["slug"] for s in yaml.safe_load(fh)["sources"]}

    repos = quick_list_repos()
    skipped = [(name, url) for name, url in repos if name.lower() in NO_DOCS]
    missing = [(name, url) for name, url in repos
               if name.lower() not in NO_DOCS
               and ALIASES.get(name.lower(), name.lower()) not in slugs]

    print(f"Plugin Quick List: {len(repos)} plugins, {len(repos) - len(missing) - len(skipped)} "
          f"documented here, {len(skipped)} not documented on purpose (NO_DOCS)")
    for name, url in missing:
        print(f"::warning title=Plugin without docs::{name} ({url}) is on the Plugin Quick List "
              f"but has no entry in sources.yml. Add one, or add the repo name to NO_DOCS in "
              f"automation/check_quick_list.py if it should not be documented here.")
    if missing:
        print(f"{len(missing)} plugin(s) on the quick list have no page on this site.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
