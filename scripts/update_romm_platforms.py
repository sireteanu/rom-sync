"""Refresh src/lolroms_sync/static/romm-platforms.json from RomM's docs (CC0).

    git clone --depth 1 https://github.com/rommapp/docs /tmp/romm-docs
    python scripts/update_romm_platforms.py /tmp/romm-docs

The Saved as column offers these as suggestions: platform name -> RomM folder name.
"""
import datetime
import json
import os
import re
import sys

src = os.path.join(sys.argv[1], "docs", "resources", "snippets", "supported-platforms.md")
rows = []
for line in open(src, encoding="utf-8"):
    m = re.match(r"\|\s*(.+?)\s*\|\s*`([^`]+)`\s*\|", line)
    if m:
        rows.append([m.group(1), m.group(2)])
if not rows:
    sys.exit("no platforms found - did the docs layout change?")
out = os.path.join(os.path.dirname(__file__), "..", "src", "lolroms_sync", "static", "romm-platforms.json")
with open(out + ".tmp", "w", encoding="utf-8") as f:
    json.dump({"source": "RomM docs, Supported Platforms (https://docs.romm.app/latest/Platforms-and-Players/"
                         "Supported-Platforms/), CC0 1.0. Regenerate with scripts/update_romm_platforms.py.",
               "updated": datetime.date.today().isoformat(), "platforms": rows}, f, ensure_ascii=False, indent=0)
os.replace(out + ".tmp", out)
print(f"{len(rows)} platforms written to {os.path.normpath(out)}")
