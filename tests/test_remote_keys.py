#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Input bridge checks; firmware checks need the emulator, Elekloader and stock.

ELEKLOADER_STOCK / ELEKLOADER_STOCK_154 select Digitakt 1.53 / 1.54.
ELEKLOADER_PATH selects the Elekloader source checkout.
ELEKLOADER_CROSS selects the cross compiler as for the other SDK tests.
REMOTE_KEYS_OUT optionally retains reports, screenshots and temporary builds.
"""
import contextlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RemoteKeys(unittest.TestCase):
    def test_state_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = str(Path(tmp) / 'keys')
            subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'tests/test_remote_keys.c'), '-o', exe], check=True)
            subprocess.run([exe], check=True)

    def test_firmware(self):
        emulator = os.environ.get('DIGIEMU_PATH')
        if not emulator:
            self.skipTest('missing DIGIEMU_PATH')
        elekloader = os.environ.get('ELEKLOADER_PATH')
        if not elekloader:
            self.skipTest('missing ELEKLOADER_PATH')
        sys.path.insert(0, elekloader)
        sys.path.insert(0, emulator)
        retained = os.environ.get('REMOTE_KEYS_OUT')
        with contextlib.ExitStack() as stack:
            if retained:
                Path(retained).mkdir(parents=True, exist_ok=True)
                out = Path(tempfile.mkdtemp(prefix='keys-', dir=retained))
            else:
                out = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            for version, variable in (('1.53', 'ELEKLOADER_STOCK'), ('1.54', 'ELEKLOADER_STOCK_154')):
                with self.subTest(os=version):
                    stock = os.environ.get(variable)
                    if not stock:
                        self.skipTest('missing ' + variable)
                    folder = out / version
                    folder.mkdir()
                    with (folder / 'run.log').open('w') as log, contextlib.redirect_stdout(log):
                        self.firmware(stock, version, folder, Path(elekloader))
                    print(version + ' firmware bridge checks passed: ' + str(folder))

    def firmware(self, stock, version, out, elekloader):
        from elekloader import lint, patch
        from elekloader.sdk.build import build
        from emu.bootstrap import first_run
        from emu.fwcheck import prepare
        from emu import panel
        from emu.session import Session
        from emu.uitrace import UiTrace
        from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_D0, UC_M68K_REG_SR

        mods = []
        for directory in (elekloader / 'mods/core', elekloader / 'mods/machine-pages',
                          ROOT / 'tests/fixtures/remote-keys-probe'):
            path, _mod = build(str(directory), stock, str(out / 'mods'))
            mods.append(path)
        self.assertEqual(lint.main([mods[-1], '--stock', stock, '--with', mods[0], '--json']), 0)
        outputs, manifest = patch.build(stock, mods, version='RK' + version[-2:], log=print)
        syx = out / 'probe.syx'
        syx.write_bytes(outputs['syx'])
        symbols = manifest['_map']
        paths, _device = prepare(str(syx), str(out / 'emulator'))
        os.environ.update(paths.env())
        snapshot = first_run(paths)
        session = Session(snapshot, paths.syx, hle=False)
        checks, trace, pending, sends, contexts = [], [], set(), [], []
        pressure = [False, None]

        def read(addr, count=4):
            return bytes(session.m.uc.mem_read(addr, count))

        def u32(addr):
            return int.from_bytes(read(addr), 'big')

        def write(addr, value):
            session.m.uc.mem_write(addr, struct.pack('>I', value))

        def wait(ms):
            session.run_ms(ms)
            self.assertIsNone(session.halted)

        def request(mask=0, clear=False, expected=0):
            mailbox = symbols['rk_probe']
            write(mailbox + 4, 2)
            write(mailbox + 8, mask)
            write(mailbox + 12, 0xffffffff)
            write(mailbox, 2 if clear else 1)
            for _ in range(20):
                wait(10)
                if u32(mailbox) == 0:
                    break
            self.assertEqual(u32(mailbox), 0, 'UI tick did not service mailbox')
            self.assertEqual(u32(mailbox + 12), expected)
            wait(80)

        def queue_send(uc, addr, size, data):
            sp = uc.reg_read(UC_M68K_REG_A7)
            queue, item = struct.unpack('>II', read(sp + 4, 8))
            if queue == session.profile.ui_queue and read(item, 1) == b'\0':
                self.assertNotIn(item, pending, 'overwrote a queued key record')
                pending.add(item)
                sends.append(item)

        def queue_pop(uc, addr, size, data):
            pending.discard(uc.reg_read(UC_M68K_REG_D0))

        def set_entry(uc, addr, size, data):
            contexts.append(uc.reg_read(UC_M68K_REG_SR))

        def lock(uc, addr, size, data):
            if pressure[0]:
                queue = symbols['fw_ui_queue']
                pressure[1] = u32(queue + 4)
                write(queue + 4, u32(queue + 16))

        def unlock(uc, addr, size, data):
            if pressure[1] is not None:
                write(symbols['fw_ui_queue'] + 4, pressure[1])
                pressure[1] = None

        session.at(session.profile.queue_send, queue_send)
        session.at(session.profile.mainloop + 0xc, queue_pop)
        session.at(symbols['core_keys_set'], set_entry)
        session.at(symbols['core_keys_lock'], lock)
        session.at(symbols['core_keys_unlock'], unlock)
        ui = UiTrace(session.m, session.at, session.profile, lambda: session.pits.now, out=trace.append)

        def held():
            return read(symbols['fw_key_state'] + 2, 1)[0]

        def machine_lists():
            return sum('activate MachineListView' in line for line in trace)

        try:
            wait(100)
            request(2)
            self.assertEqual(held(), 2)
            request(10)
            request(2)
            request(clear=True)
            self.assertEqual(held(), 0)
            self.assertEqual(machine_lists(), 1)
            self.assertTrue(session.frames)
            panel.write_png(session.frames[-1][1], str(out / 'machine-list.png'), scale=4)
            checks.append('generic hold/tap/release opens machine list from UI tick')
            session.tap(session.code('NO'), hold_ms=80, after_ms=120)

            write(symbols['rk_key_events'] + 12, 1)
            request(2)
            request(10)
            request(2)
            request(clear=True)
            self.assertEqual(machine_lists(), 1, 'core subscriber did not consume SRC')
            write(symbols['rk_key_events'] + 12, 0)
            checks.append('synthetic events retain core subscriber interception')

            request(2)
            session.press(session.code('FUNC'))
            wait(80)
            request(clear=True)
            self.assertEqual(held(), 2, 'cleanup released physical FUNC')
            session.release(session.code('FUNC'))
            wait(120)
            self.assertEqual(held(), 0)
            checks.append('physical takeover of synthetic FUNC survives cleanup')

            for key, mask in (('FUNC', 2), ('SRC', 8)):
                session.press(session.code(key))
                wait(80)
                request(mask, expected=3)
                self.assertEqual(held(), mask)
                request(clear=True)
                self.assertEqual(held(), mask)
                session.release(session.code(key))
                wait(120)
                self.assertEqual(held(), 0)
            checks.append('preheld physical FUNC and SRC refuse remote overlap')

            session.press(session.code('TRK'))
            wait(80)
            request(2)
            self.assertEqual(held(), 3)
            request(clear=True)
            self.assertEqual(held(), 1)
            session.release(session.code('TRK'))
            wait(120)
            self.assertEqual(held(), 0)
            checks.append('unrelated physical key in same group survives cleanup')

            pressure[0] = True
            request(2, expected=2)
            self.assertEqual(held(), 0)
            pressure[0] = False
            request(2)
            pressure[0] = True
            request(clear=True, expected=2)
            self.assertEqual(held(), 2)
            pressure[0] = False
            request(clear=True)
            self.assertEqual(held(), 0)
            checks.append('busy press leaves no hold; busy cleanup retries without losing ownership')

            self.assertTrue(contexts)
            self.assertTrue(all(sr & 0x0700 == 0 for sr in contexts), contexts)
            self.assertEqual(ui.missing, [])
            self.assertEqual(ui.errors, 0)
            self.assertGreater(len(sends), 0)
            self.assertFalse(pending, 'key records did not drain')
            checks.append('UI task calls bridge; stock queued records remain live until consumed')
            (out / 'report.json').write_text(json.dumps(dict(os=version, checks=checks,
                events=len(sends), ui_trace_errors=ui.errors, halted=session.halted), indent=2) + '\n')
            (out / 'trace.txt').write_text('\n'.join(trace) + '\n')
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main()
