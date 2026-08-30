# -*- coding: utf-8 -*-
"""Прожиг титула по индексу ITEMS finalize_vietnam: burn_item.py <idx> <in> <out>"""
import sys, subprocess
sys.path.insert(0, "scripts")
from finalize_vietnam import ITEMS
it = ITEMS[int(sys.argv[1])]
r = subprocess.run([sys.executable, "scripts/burn_captions.py", sys.argv[2], sys.argv[3],
                    "--location", it["loc"], "--country", it["country"],
                    "--location-text", it["loctext"], "--quote", it["quote"],
                    "--author", it["author"], "--quote-ru", it["quote_ru"], "--tags", it["tags"]],
                   capture_output=True)
sys.exit(r.returncode)
