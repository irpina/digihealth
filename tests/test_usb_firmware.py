#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled USB-hook/UI tests. Uses the same environment as test_remote_keys.py.

ELEKLOADER_DN_SYX additionally selects the desktop Digitone OS 1.44 stock file.

The emulator has no USB endpoint model: invoke the patched router hook with
its real register ABI and capture the stock SysEx sender's arguments.
"""
import json
import contextlib
import copy
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import test_remote_keys as bridge
ROOT = bridge.ROOT
sys.path.insert(0, str(ROOT / 'tools'))
import digiusb as usb


class Firmware(unittest.TestCase):
    def test_firmware(self):
        emulator, elekloader = os.environ.get('DIGIEMU_PATH'), os.environ.get('ELEKLOADER_PATH')
        if not emulator or not elekloader:
            self.skipTest('missing DIGIEMU_PATH or ELEKLOADER_PATH')
        sys.path[:0] = [emulator, elekloader]
        retained = os.environ.get('REMOTE_KEYS_OUT')
        with contextlib.ExitStack() as stack:
            if retained:
                Path(retained).mkdir(parents=True, exist_ok=True)
                out = Path(tempfile.mkdtemp(prefix='all-keys-', dir=retained))
            else:
                out = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            for version, variable in (('1.53', 'ELEKLOADER_STOCK'), ('1.54', 'ELEKLOADER_STOCK_154'),
                                      ('1.44', 'ELEKLOADER_DN_SYX')):
                with self.subTest(os=version):
                    stock = os.environ.get(variable)
                    if not stock:
                        self.skipTest('missing ' + variable)
                    folder = out / version
                    folder.mkdir()
                    with (folder / 'run.log').open('w') as log, contextlib.redirect_stdout(log):
                        self.firmware(stock, version, folder, Path(elekloader))
                    print(version + ' complete key checks passed: ' + str(folder), flush=True)

    def firmware(self, stock, version, out, elekloader):
        from elekloader import lint, patch, syx, devices
        from elekloader.sdk.build import build
        from emu.bootstrap import first_run
        from emu.fwcheck import prepare
        from emu.harness import call
        from emu import panel
        from emu.session import Session
        from emu.uitrace import UiTrace
        from unicorn.m68k_const import (UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A7,
                                       UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_PC)
        sys.path.insert(0, str(ROOT))
        import build as health

        dev, _rel = devices.identify(syx.Syx.load(stock).sha256)
        product = 1 if dev.key == 'digitakt-mk1' else 2
        mapping = usb.MAPS[product]
        func_group, func_mask = mapping['FUNC'][1:]

        mods = []
        directories = [elekloader / ('mods/core' if product == 1 else 'mods/core-dn1')]
        if product == 1:
            directories.append(elekloader / 'mods/machine-pages')
        directories.append(ROOT / 'tests/fixtures/key-observer')
        for directory in directories:
            path, _mod = build(str(directory), stock, str(out / 'mods'))
            mods.append(path)
        self.assertEqual(health.main(['--stock', stock, '--out', str(out / 'mods')]), 0)
        mods.append(str(next((out / 'mods').glob('digihealth-*.elemod'))))
        self.assertEqual(lint.main([mods[-1], '--stock', stock, '--with', mods[0], '--json']), 0)
        outputs, manifest = patch.build(stock, mods, version='UK' + version[-2:], log=print)
        syx = out / 'helper.syx'
        syx.write_bytes(outputs['syx'])
        symbols = manifest['_map']
        paths, _device = prepare(str(syx), str(out / 'emulator'))
        os.environ.update(paths.env())
        snapshot = first_run(paths)
        session = Session(snapshot, paths.syx, hle=False)
        trace, checks = [], []
        trace_profile = session.profile
        if product == 2:
            # The emulator lacks this DN signature; verify the core's actual call site.
            self.assertEqual(bytes(session.m.uc.mem_read(0x40019d9c, 6)),
                             b'\x4e\xb9' + struct.pack('>I', symbols['core_dn_key']))
            trace_profile = copy.copy(session.profile)
            trace_profile.ui_key_dispatch = 0x40019d9c
        ui = UiTrace(session.m, session.at, trace_profile, lambda: session.pits.now, out=trace.append)
        self.assertEqual(ui.missing, [])
        received, sent = [], []
        pressure, saved_counts = [False], []
        sender = {'1.53': 0x400d6e1e, '1.54': 0x400d7046, '1.44': 0x400ee5c8}[version]
        buffer = 0x11010000
        session.m.ensure(buffer)
        session.m.ensure(0x11020000)

        def read(addr, count):
            return bytes(session.m.uc.mem_read(addr, count))

        def wait(ms):
            session.run_ms(ms)
            self.assertIsNone(session.halted)

        def held():
            return read(symbols['fw_key_state'] + func_group, 1)[0]

        def lock_queue(uc, addr, size, data):
            if pressure[0]:
                queue = symbols['fw_ui_queue']
                saved_counts.append(read(queue + 4, 4))
                uc.mem_write(queue + 4, read(queue + 16, 4))

        def unlock_queue(uc, addr, size, data):
            if pressure[0]:
                uc.mem_write(symbols['fw_ui_queue'] + 4, saved_counts.pop())

        session.at(symbols['core_keys_lock'], lock_queue)
        session.at(symbols['core_keys_unlock'], unlock_queue)

        def capture(uc, addr, size, data):
            sp = uc.reg_read(UC_M68K_REG_A7)
            ret, pointer, length, abort, port = struct.unpack('>IIIII', read(sp, 20))
            self.assertEqual((abort, port), (0, 2))
            received.append(read(pointer, length))
            uc.reg_write(UC_M68K_REG_A7, sp + 4)
            uc.reg_write(UC_M68K_REG_PC, ret)

        session.at(sender, capture)

        def inject(message, source=2):
            uc = session.m.uc
            context = uc.context_save()
            before = read(symbols['fw_key_state'], 7)
            try:
                uc.mem_write(buffer, message)
                uc.reg_write(UC_M68K_REG_A0, buffer)
                uc.reg_write(UC_M68K_REG_A1, buffer)
                uc.reg_write(UC_M68K_REG_D1, len(message))
                uc.reg_write(UC_M68K_REG_D2, source)
                session.m._fast_stepper_obj.left = 100000
                call(session.m, symbols['hook_sysex'], [], stack_top=0x11020100)
                self.assertEqual(uc.reg_read(UC_M68K_REG_A0), buffer)
                self.assertEqual(uc.reg_read(UC_M68K_REG_A1), buffer)
                self.assertEqual(uc.reg_read(UC_M68K_REG_D1), len(message))
                self.assertEqual(uc.reg_read(UC_M68K_REG_D2), source)
                self.assertEqual(read(symbols['fw_key_state'], 7), before, 'MIDI hook executed a UI action')
            finally:
                uc.context_restore(context)

        class EmulatorPort:
            drop = None

            def send(self, data):
                sent.append(usb.unpack7(data[6:-1]))
                inject(data)
                if self.drop == sent[-1][2]:
                    received.clear()
                    self.drop = None

            def receive(self, seconds=1, until=None):
                wait(40)
                messages = received[:]
                received.clear()
                if until:
                    until(messages)
                return messages

        port = EmulatorPort()
        diag = usb.Diag(port)
        try:
            wait(100)
            for source in (8, 16, 0):
                inject(usb.req_caps(1), source)
                self.assertFalse(received, 'non-USB request accepted')
            for source in (2, 4):
                inject(usb.req_caps(1), source)
                self.assertEqual(len(usb.parse(received.pop())['payload']), 19)
            checks.append('compiled router hook preserves ABI, accepts USB 2/4 and refuses DIN 8/16')

            hello = usb.decode_hello(diag.call(usb.req_hello))
            self.assertEqual((hello['proto'], hello['name']),
                             (1, 'digihealth 1.0' if product == 1 else 'digihealth 1.1'))
            self.assertEqual(len(diag.call(usb.req_stats)), 96)
            expected = read(0x40000000, 16)
            self.assertEqual(usb.peek(diag, 0x40000000, 16), expected)
            checks.append('legacy HELLO, STATS and PEEK work through the same compiled hook')

            keys = usb.RemoteKeys(diag)
            before_actions = sum(row[2] == usb.ACTION for row in sent)
            port.drop = usb.ACTION
            keys.press('FUNC')
            self.assertEqual(sum(row[2] == usb.ACTION for row in sent), before_actions + 1)
            self.assertEqual(held(), func_mask)
            keys.tap('SRC' if product == 1 else 'LEVEL_PUSH')
            keys.release('FUNC')
            keys.cancel()
            wait(100)
            self.assertEqual(held(), 0)
            view = 'MachineListView' if product == 1 else 'SoundBrowser'
            self.assertEqual(sum('activate ' + view in line for line in trace), 1)
            self.assertTrue(session.frames)
            panel.write_png(session.frames[-1][1], str(out / 'gesture.png'), scale=4)
            checks.append('client hold/tap/release opens ' + view + '; lost ACTION reply is not replayed')
            session.tap(session.code('NO'), hold_ms=80, after_ms=120)

            with usb.RemoteKeys(diag) as keys:
                payload = struct.pack('>IIBBH', keys.session, 1, usb.PRESS, 1, 100)
                bad = usb.request(77, usb.ACTION, payload + b'\0')
                inject(bad)
                self.assertEqual(usb.parse(received.pop())['status'], 1)
                wait(100)
                self.assertEqual(held(), 0)
            checks.append('oversized action rejected by compiled hook without a transition')

            with usb.RemoteKeys(diag) as keys:
                keys.press('FUNC', 100)
                wait(180)
                self.assertEqual(keys.status()['state'], 'expired')
                self.assertEqual(held(), 0)
            checks.append('abandoned hold expires on firmware DTIM0 clock')

            session.press(session.code('FUNC'))
            wait(80)
            with usb.RemoteKeys(diag) as keys:
                with self.assertRaisesRegex(RuntimeError, 'overlap'):
                    keys.press('FUNC')
            self.assertEqual(held(), func_mask)
            session.release(session.code('FUNC'))
            wait(80)
            with usb.RemoteKeys(diag) as keys:
                keys.press('FUNC')
                session.press(session.code('FUNC'))
                wait(80)
            self.assertEqual(held(), func_mask)
            session.release(session.code('FUNC'))
            wait(100)
            self.assertEqual(held(), 0)
            checks.append('physical overlap refused and physical takeover survives cancellation')

            with usb.RemoteKeys(diag) as keys:
                pressure[0] = True
                diag.call(usb.req_action, keys.session, 1, usb.PRESS, usb.KEYS['FUNC'], 2000)
                self.assertEqual(held(), 0, 'busy queue accepted a synthetic press')
                pressure[0] = False
                keys.operation = 1
                keys._wait(keys.status())
                self.assertEqual(held(), func_mask)
                pressure[0] = True
                diag.call(usb.req_cancel, keys.session)
                self.assertEqual(keys.status()['cleanup'], 1)
                self.assertEqual(held(), func_mask, 'busy cleanup lost ownership')
                pressure[0] = False
                keys.cancel()
                self.assertEqual(held(), 0)
                self.assertFalse(saved_counts)
            checks.append('native queue backpressure postpones press/cleanup without losing ownership')

            with usb.RemoteKeys(diag) as keys:
                keys.tap('SETTINGS')
                wait(80)
                panel.write_png(session.frames[-1][1], str(out / 'settings.png'), scale=4)
                keys.tap('YES')
                keys.tap('NO')
                keys.tap('NO')
            checks.append('shared Settings/Yes/No names navigate through the native UI')

            def word(addr):
                return int.from_bytes(read(addr, 4), 'big')

            session.m.uc.mem_write(symbols['key_consume'], struct.pack('>I', 1))
            with usb.RemoteKeys(diag) as keys:
                for name, (code, group, mask) in mapping.items():
                    before = word(symbols['key_seen'] + code * 4)
                    keys.press(name)
                    self.assertEqual(read(symbols['fw_key_state'] + group, 1)[0] & mask, mask, name)
                    wait(40)
                    self.assertGreater(word(symbols['key_seen'] + code * 4), before, name)
                    keys.release(name)
                    self.assertEqual(read(symbols['fw_key_state'] + group, 1)[0] & mask, 0, name)
            self.assertEqual(read(symbols['fw_key_state'], 7), bytes(7))
            checks.append('all %d buttons emit their mapped firmware key IDs through the native driver/core' % len(mapping))

            self.assertEqual(ui.errors, 0)
            self.assertEqual(ui.missing, [])
            (out / 'report.json').write_text(json.dumps(dict(os=version, product=product, checks=checks,
                halted=session.halted, ui_trace_errors=ui.errors, hello=hello), indent=2) + '\n')
        finally:
            (out / 'trace.txt').write_text('\n'.join(trace) + '\n')
            if session.frames:
                panel.write_png(session.frames[-1][1], str(out / 'last-screen.png'), scale=4)
            session.close()


if __name__ == '__main__':
    unittest.main()
