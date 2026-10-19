#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete maps/ownership without MIDI. Stock checks use ELEKLOADER_PATH,
ELEKLOADER_STOCK (1.53), ELEKLOADER_STOCK_154 and ELEKLOADER_DN_SYX (1.44).
"""
import ctypes
import contextlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import test_usb_keys as host
usb, ROOT = host.usb, host.ROOT


class AllKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.libs = {}
        for product, minor in ((1, 53), (2, 44)):
            path = str(Path(cls.tmp.name) / ('keys%d.so' % product))
            subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                            '-DRK_PRODUCT=%d' % product, '-DRK_OS_MINOR=%d' % minor,
                            str(ROOT / 'tests/fixtures/remote_keys_host.c'), '-o', path], check=True)
            lib = ctypes.CDLL(path)
            lib.rk_receive.argtypes = (ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p)
            cls.libs[product] = lib

    def connect(self, product):
        lib = self.libs[product]
        lib.host_reset()
        port = host.HostPort(lib)
        return lib, port, usb.Diag(port)

    def test_every_button_and_unsupported_names(self):
        self.assertEqual((len(usb.KEYS), len(usb.MAPS[1]), len(usb.MAPS[2])), (57, 48, 54))
        self.assertEqual((usb.KEYS['FUNC'], usb.KEYS['SRC']), (1, 2))
        for product in (1, 2):
            for name, (_code, group, mask) in usb.MAPS[product].items():
                with self.subTest(product=product, button=name):
                    lib, port, diag = self.connect(product)
                    with usb.RemoteKeys(diag) as keys:
                        keys.press(name)
                        self.assertEqual([lib.host_group(g) for g in range(7)],
                                         [mask if g == group else 0 for g in range(7)])
                        keys.release(name)
                        keys.tap(name)
                    self.assertFalse(any(lib.host_group(g) for g in range(7)))
            lib, port, diag = self.connect(product)
            with usb.RemoteKeys(diag) as keys:
                for name in usb.KEYS.keys() - usb.MAPS[product].keys():
                    before = len(port.sent)
                    with self.assertRaises(ValueError):
                        keys.press(name)
                    self.assertEqual(len(port.sent), before)
                    with self.assertRaisesRegex(ValueError, 'refused'):
                        diag.call(usb.req_action, keys.session, 1, usb.PRESS, usb.KEYS[name], 100)

    def test_cross_group_tap_and_physical_takeover(self):
        for product in (1, 2):
            lib, port, diag = self.connect(product)
            _code, group, mask = usb.MAPS[product]['FUNC']
            _code, other_group, other_mask = usb.MAPS[product]['YES']
            self.assertNotEqual(group, other_group)
            with usb.RemoteKeys(diag) as keys:
                keys.press('FUNC')
                keys.tap('YES')
                self.assertEqual(lib.host_group(group), mask)
                self.assertEqual(lib.host_group(other_group), 0)
                lib.host_physical_group(group, mask)
            self.assertEqual(lib.host_group(group), mask)

    def test_sequences_on_both_devices(self):
        for product in (1, 2):
            with self.subTest(product=product):
                lib, port, diag = self.connect(product)
                with contextlib.redirect_stdout(io.StringIO()):
                    usb.cmd_keys(diag, usb.key_args(['FUNC(YES),DOWN,1,A_PUSH']))
                self.assertEqual(sum(raw[2] == usb.ACTION for raw in port.sent), 6)
                self.assertFalse(any(lib.host_group(g) for g in range(7)))
                lib, port, diag = self.connect(product)
                absent = 'T1' if product == 1 else 'SRC'
                with self.assertRaisesRegex(ValueError, 'does not have'):
                    usb.cmd_keys(diag, usb.key_args(['YES,' + absent]))
                self.assertFalse(any(raw[2] == usb.ACTION for raw in port.sent))

    def test_partial_cleanup_reports_remaining_ownership_and_retries(self):
        for product in (1, 2):
            lib, port, diag = self.connect(product)
            keys = usb.RemoteKeys(diag)
            keys.press('FUNC')
            diag.call(usb.req_action, keys.session, 2, usb.TAP, usb.KEYS['YES'], 250)
            lib.host_fill_after(usb.MAPS[product]['FUNC'][1])
            diag.call(usb.req_cancel, keys.session)
            status = keys.status(0)
            self.assertEqual((status['cleanup'], status['owned']), (1, 1 << (usb.KEYS['YES'] - 1)))
            self.assertEqual(lib.host_group(usb.MAPS[product]['FUNC'][1]), 0)
            lib.host_busy(0)
            keys.cancel()
            self.assertFalse(any(lib.host_group(g) for g in range(7)))

    def test_digitone_keys_variant_is_refused_before_open(self):
        lib, port, diag = self.connect(2)
        lib.host_variant(0x80000)
        with self.assertRaisesRegex(ValueError, 'refused'):
            usb.RemoteKeys(diag)
        self.assertEqual([row[2] for row in port.sent], [usb.CAPS])

    def test_stock_runtime_tables(self):
        elekloader = os.environ.get('ELEKLOADER_PATH')
        if not elekloader:
            self.skipTest('missing ELEKLOADER_PATH')
        sys.path.insert(0, elekloader)
        from elekloader import syx, devices
        for product, variable, address in ((1, 'ELEKLOADER_STOCK', 0x4018e89c),
                                            (1, 'ELEKLOADER_STOCK_154', 0x4018eb9c),
                                            (2, 'ELEKLOADER_DN_SYX', 0x401a9084)):
            stock = os.environ.get(variable)
            if not stock:
                self.skipTest('missing ' + variable)
            image = syx.Syx.load(stock)
            dev, _rel = devices.identify(image.sha256)
            main = image.section(dev.main_section)
            positions = set()
            for name, (code, group, mask) in usb.MAPS[product].items():
                self.assertEqual(mask.bit_count(), 1)
                pos = group * 8 + mask.bit_length() - 1
                self.assertNotIn(pos, positions)
                positions.add(pos)
                self.assertEqual(struct.unpack_from('>I', main, address - dev.main_load + pos * 4)[0], code,
                                 (product, name))


if __name__ == '__main__':
    unittest.main()
