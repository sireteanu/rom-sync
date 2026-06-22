#!/usr/bin/env python3
"""Run the command-line tool without installing it:  python dir-crawler.py "https://lolroms.com/?dir=Atari"

(After `pip install -e .` the same thing is available as the `dir-crawler` command.)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dir_crawler.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
