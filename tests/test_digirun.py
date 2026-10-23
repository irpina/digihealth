#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""digirun.py and elkrpc.py against a fake unit that answers Elektron's RPC
(Ping, SoftwareVersion, Screenshot, the data store): no MIDI device is opened,
and no key session (that is test_usb_keys.py)."""
import contextlib
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import elkrpc
import digirun
from digiusb import pack7, unpack7


class FakeUnit:
    """Replies to each request when the port is next read."""

    def __init__(self, block=0x2000):
        self.pending, self.short, self.seq = [], [], 0x4000
        self.store = {'/projects/1': bytes(range(256)) * 50}
        self.names = {'/projects/1': 'MY SONG'}
        self.jobs = {}
        self.block = block
        self.closed = False
        w, h = 128, 64
        cols = [(1 << 63) | (x & 1) for x in range(w)]     # row 0 = LSB, row 63 = top bit
        self.pixels = b''.join(c.to_bytes(8, 'big') for c in cols)

    # --- the port interface ---------------------------------------------------
    def send(self, data):
        data = bytes(data)
        r = elkrpc.parse(data)
        if r is None:
            return
        msg = unpack7(data[6:-1])
        seq, _, cmd = struct.unpack_from('>HHB', msg)
        args = self.answer(cmd, msg[5:])
        self.seq += 1
        self.pending.append(elkrpc.HEADER + pack7(struct.pack('>HHB', self.seq, seq, cmd | 0x80)
                                                  + args) + b'\xF7')

    def send_short(self, *data):
        self.short.append(data)

    def receive(self, seconds=1.0, until=None):
        msgs, self.pending = self.pending, []
        if until:
            until(msgs)
        return msgs

    def close(self):
        self.closed = True

    # --- the device -----------------------------------------------------------
    def answer(self, cmd, a):
        if cmd == elkrpc.PING:
            return bytes([0x14, 2, 0x01, 0x04]) + b'Digitone\0'
        if cmd == elkrpc.VERSION:
            return b'0144\x001.44\x00'
        if cmd == elkrpc.SCREENSHOT:
            return struct.pack('>BHHI', 1, 128, 64, len(self.pixels)) + self.pixels
        if cmd == elkrpc.DATA_LIST:
            path = a[:a.index(0)].decode()
            out = b'\x01' + bytes(12)
            for name, data in sorted(self.store.items()):
                if name.startswith(path + '/'):
                    out += (self.names.get(name, 'NEW').encode() + b'\0' + bytes([0, 2])
                            + struct.pack('>iIHBB', int(name.rsplit('/', 1)[1]), len(data), 7, 1, 0))
            return out
        if cmd == elkrpc.DATA_READ_OPEN:
            path = a[:a.index(0)].decode()
            if path not in self.store:
                return b'\x00no such object\0'
            job = len(self.jobs) + 1
            self.jobs[job] = path
            return b'\x01' + struct.pack('>IIB', job, self.block, 1)
        if cmd == elkrpc.DATA_READ_PARTIAL:
            job, seq = struct.unpack('>II', a)
            data = self.store[self.jobs[job]]
            part = data[seq * self.block:(seq + 1) * self.block]
            last = (seq + 1) * self.block >= len(data)
            return b'\x01' + struct.pack('>IIIBII', job, seq, 1000 * min(len(data), (seq + 1) * self.block)
                                         // len(data), last, 0, len(part)) + part
        if cmd == elkrpc.DATA_READ_CLOSE:
            job, = struct.unpack('>I', a)
            return b'\x01' + struct.pack('>II', job, 0)
        return b''


class RpcTest(unittest.TestCase):
    def setUp(self):
        self.unit = FakeUnit()
        self.rpc = elkrpc.Rpc(self.unit)

    def test_identity(self):
        self.assertEqual(self.rpc.ping(), (0x14, 'Digitone'))
        self.assertEqual(self.rpc.version(), '1.44')

    def test_screenshot_orientation_and_png(self):
        s = self.rpc.screenshot()
        rows = s.rows()
        self.assertEqual((s.w, s.h), (128, 64))
        self.assertEqual(rows[0][:4], [0, 1, 0, 1])          # LSB of each column is row 0
        self.assertEqual(rows[63], [1] * 128)                # the top bit is the last row
        self.assertEqual(s.lit(), 128 + 64)
        png = s.png(2)
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>II', png[16:24]), (256, 128))

    def test_data_store_list_and_read(self):
        self.assertEqual([e['id'] for e in self.rpc.data_list('/projects')], [1])
        self.assertEqual(self.rpc.data_read('/projects/1'), self.unit.store['/projects/1'])
        self.unit.block = 100                               # several partial reads
        self.assertEqual(self.rpc.data_read('/projects/1'), self.unit.store['/projects/1'])
        with self.assertRaises(elkrpc.RpcError):
            self.rpc.data_read('/projects/9')

    def test_ignores_other_traffic(self):
        self.unit.pending.append(bytes([0xF0, 0x00, 0x20, 0x3C, 0x0D, 0x00, 0x53, 0xF7]))
        self.assertEqual(self.rpc.version(), '1.44')


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.unit = FakeUnit()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = digirun.Session(self.unit, self.tmp.name)

    def test_steps_without_keys(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.s.run('# a comment; with a semicolon\ncc 10 74 64; nrpn 1 1 2 300\n'
                       'note 2 36 100 1  # kick\nshot a.png; shot a.bin\nexpect-screen a.bin; say ok')
        self.assertEqual(self.unit.short[:5], [(0xB9, 74, 64), (0xB0, 99, 1), (0xB0, 98, 2),
                                               (0xB0, 6, 2), (0xB0, 38, 44)])
        self.assertEqual(self.unit.short[5:], [(0x91, 36, 100), (0x81, 36, 0)])
        self.assertTrue(Path(self.tmp.name, 'a.png').read_bytes().startswith(b'\x89PNG'))
        self.unit.pixels = bytes(len(self.unit.pixels))
        with self.assertRaisesRegex(AssertionError, '192 pixels differ'):
            self.s.step('expect-screen a')
        self.assertTrue(Path(self.tmp.name, 'a-got.png').exists())
        self.assertTrue(Path(self.tmp.name, 'a-diff.png').exists())

    def test_screenshot_inside_a_hold(self):
        events = []
        unit = self.unit

        class Keys:
            def press(self, key, ms=None):
                events.append(('press', key, ms))

            def release(self, key):
                events.append(('release', key))

            def tap(self, key, ms=None):
                events.append(('tap', key))

            def turn(self, encoder, delta, step=1):
                events.append(('turn', encoder, delta, step))

            def cancel(self):
                events.append(('cancel',))
        send = unit.send
        unit.send = lambda d: (events.append(('rpc',)) if elkrpc.parse(d) else None, send(d))
        self.s._keys = Keys()
        with contextlib.redirect_stdout(io.StringIO()):
            self.s.run('hold FUNC 1500; shot held.png; release FUNC; keys FUNC(YES); turn a -12 4')
        self.s.close()
        self.assertEqual(events, [('press', 'FUNC', 1500), ('rpc',), ('release', 'FUNC'),
                                  ('press', 'FUNC', None), ('tap', 'YES'), ('release', 'FUNC'),
                                  ('turn', 'A', -12, 4), ('cancel',)])

    def test_unknown_step(self):
        with self.assertRaises(ValueError):
            self.s.step('jump 3')

    def test_screen_diff_text(self):
        w, h = 128, 64
        blank = bytes(w * 8)
        cols = [0] * w
        cols[5] = 1 << 3                       # row 3 lit in column 5
        lit = b''.join(c.to_bytes(8, 'big') for c in cols)
        diff, text = digirun.screen_diff(lit, blank, w, h)
        self.assertEqual(diff, [(3, 5)])
        self.assertTrue(text.startswith('1 pixels differ in rows 3-3, columns 5-5'))
        self.assertIn('\n    +', text)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.unit = unit = FakeUnit()
        self.names = []
        names = self.names

        def open_port(name=None):
            names.append(name)
            return unit
        saved, digirun.open_port = digirun.open_port, open_port
        self.addCleanup(setattr, digirun, 'open_port', saved)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = digirun.main(list(argv))
        return rc, out.getvalue()

    def test_info_shot_and_data(self):
        self.assertEqual(self.main('info'), (0, 'Digitone (product id 0x14), OS 1.44\n'))
        png = Path(self.tmp.name, 's.png')
        rc, out = self.main('--port', 'Digitone', 'shot', str(png), '--scale', '1')
        self.assertEqual(rc, 0)
        self.assertEqual(self.names[-1], 'Digitone')
        self.assertIn('128x64, 192 lit pixels', out)
        cwd = os.getcwd()                              # a relative name lands in the current folder
        os.chdir(self.tmp.name)
        try:
            self.assertEqual(self.main('shot', 'rel.png')[0], 0)
        finally:
            os.chdir(cwd)
        self.assertTrue(Path(self.tmp.name, 'rel.png').exists())
        rc, out = self.main('data', 'ls', '/projects')
        self.assertIn('MY SONG', out)
        got = Path(self.tmp.name, 'p.bin')
        self.assertEqual(self.main('data', 'get', '/projects/1', str(got))[0], 0)
        self.assertEqual(got.read_bytes(), self.unit.store['/projects/1'])
        self.assertTrue(self.unit.closed)

    def test_do_writes_into_out(self):
        rc, out = self.main('do', 'shot x.bin; say hi', '--out', self.tmp.name)
        self.assertEqual(rc, 0)
        self.assertTrue(Path(self.tmp.name, 'x.bin').exists())
        self.assertTrue(out.endswith('hi\ndone\n'))

    def test_bad_arguments_open_no_port(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            digirun.main(['data', 'get', '/projects/1'])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            digirun.main(['data', 'put', '/projects/1', 'x'])
        self.assertEqual(self.names, [])


if __name__ == '__main__':
    unittest.main()
