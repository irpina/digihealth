# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared logical names and device maps from the firmware's X-macro table."""
import csv
from pathlib import Path

KEYS = {}
MAPS = {1: {}, 2: {}}
for line in (Path(__file__).resolve().parents[1] / 'keys.def').read_text().splitlines():
    if not line.startswith('KEY('):
        continue
    fields = next(csv.reader([line[4:-1]], skipinitialspace=True))
    number, name = int(fields[0]), fields[1]
    if number != len(KEYS) + 1 or name in KEYS or len(fields) != 8:
        raise ValueError('invalid shared key definition')
    KEYS[name] = number
    for product, offset in ((1, 2), (2, 5)):
        code, group, mask = (int(value, 0) for value in fields[offset:offset + 3])
        if code:
            MAPS[product][name] = (code, group, mask)

SUPPORTED = {product: sum(1 << (KEYS[name] - 1) for name in mapping)
             for product, mapping in MAPS.items()}
