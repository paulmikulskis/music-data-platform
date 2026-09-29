"""Run the sources-export generator against an isolated fixture project copy."""

import sys
from pathlib import Path

from mdp_functions.exporter import export_sources
from mdp_functions.registry import REGISTRY, discover
from mdp_functions.settings import REPO, Settings

root = Path(sys.argv[1]).resolve()
if root == REPO.resolve():
    raise SystemExit("Fixture export requires an isolated project copy")
discover()
REGISTRY.pop('fixture_enrichment', None)
for path in export_sources(Settings(), root):
    print(path.relative_to(root))
