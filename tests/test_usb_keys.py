#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real C protocol/ownership logic behind a fake transport; no MIDI device is opened."""
import ctypes
import contextlib
import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import digiusb as usb
from midimido import Port


class HostPort:
    def __init__(self, lib):
        self.lib = lib
        self.messages = []
        self.sent = []
        self.drop_request = self.drop_reply = None
        self.closed = False

    def close(self):
        self.closed = True

    def send(self, data):
        data = bytes(data)
        raw = usb.unpack7(data[6:-1])
        self.sent.append(raw)
        command = raw[2]
        if self.drop_request == command:
            self.drop_request = None
            return
        reply = ctypes.create_string_buffer(1100)
        count = self.lib.rk_receive(data[6:-1], len(data) - 7, reply)
        if self.drop_reply == command:
            self.drop_reply = None
        elif count:
            self.messages.append(usb.HEADER + usb.pack7(reply.raw[:count]) + b'\xf7')

    def receive(self, seconds=1, until=None):
        self.lib.host_advance(33)
        messages, self.messages = self.messages, []
        if until:
            until(messages)
        return messages


class USBKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        library = str(Path(cls.tmp.name) / 'keys.so')
        subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(ROOT / 'tests/fixtures/remote_keys_host.c'), '-o', library], check=True)
        cls.lib = ctypes.CDLL(library)
        cls.lib.rk_receive.argtypes = (ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p)

    def setUp(self):
        self.lib.host_reset()
        self.port = HostPort(self.lib)
        self.diag = usb.Diag(self.port)

    def wire(self, command, payload=b'', suffix=b''):
        data = usb.request(0xfedc, command, payload)
        self.port.send(data[:-1] + suffix + data[-1:])
        reply = usb.parse(self.port.messages.pop())
        self.assertEqual((reply['seq'], reply['cmd']), (0xfedc, command | 0x80))
        return reply

    def test_generic_gesture_and_capabilities(self):
        with usb.RemoteKeys(self.diag) as keys:
            self.assertEqual((keys.default_tap, keys.max_tap, keys.max_hold), (100, 250, 2000))
            keys.press('FUNC')
            self.assertEqual(self.lib.host_mask(), 2)
            keys.tap('SRC')
            self.assertEqual(self.lib.host_mask(), 2)
            keys.release('FUNC')
        self.assertEqual(self.lib.host_mask(), 0)
        self.assertEqual(self.lib.host_calls(), 4)

    def test_cli_selects_existing_windows_or_mido_transport(self):
        for platform, module in (('win32', 'winmidi'), ('darwin', 'midimido')):
            port = HostPort(self.lib)
            with patch.dict(sys.modules, {module: types.SimpleNamespace(Port=lambda: port)}), \
                    patch.object(usb.sys, 'platform', platform), contextlib.redirect_stdout(io.StringIO()):
                usb.main(['keys', 'FUNC(SRC)'])
            self.assertEqual(self.lib.host_mask(), 0)
            self.assertTrue(port.closed)

    def test_remote_keys_row_off_refuses_and_releases(self):
        self.lib.host_off(1)
        with self.assertRaisesRegex(ValueError, 'REMOTE KEYS'):
            usb.RemoteKeys(self.diag)
        self.lib.host_off(0)
        keys = usb.RemoteKeys(self.diag)
        keys.press('FUNC')
        self.assertEqual(self.lib.host_mask(), 2)
        self.lib.host_physical(1)
        self.lib.host_off(1)
        self.lib.host_advance(33)
        self.assertEqual(self.lib.host_mask(), 1)
        with self.assertRaisesRegex(ValueError, 'REMOTE KEYS'):
            keys.tap('SRC')
        self.lib.host_off(0)
        self.assertEqual(keys.status(0)['state'], 'cancelled')
        self.assertEqual(keys.status(0)['owned'], 0)

    def test_either_mapped_key_can_be_the_held_key(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.press('SRC')
            self.assertEqual(self.lib.host_mask(), 8)
            keys.tap('FUNC')
            self.assertEqual(self.lib.host_mask(), 8)
            keys.release('SRC')
        self.assertEqual(self.lib.host_mask(), 0)

    def test_cli_sequence_runs_in_order_and_releases_each_gesture(self):
        port = self.port
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=lambda: port)), \
                patch.object(usb.sys, 'platform', 'darwin'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            usb.main(['keys', 'FUNC(SRC),DOWN,DOWN,DOWN,YES', '--tap-ms', '50'])
        actions = [struct.unpack('>IIBBH', raw[3:]) for raw in port.sent if raw[2] == usb.ACTION]
        self.assertEqual([(a, key, ms) for _, _, a, key, ms in actions], [
            (usb.PRESS, usb.KEYS['FUNC'], 2000), (usb.TAP, usb.KEYS['SRC'], 50),
            (usb.RELEASE, usb.KEYS['FUNC'], 0),
            *[(usb.TAP, usb.KEYS['DOWN'], 50)] * 3, (usb.TAP, usb.KEYS['YES'], 50)])
        self.assertEqual([op for _, op, *_ in actions], list(range(1, 8)))
        self.assertEqual(sum(raw[2] == usb.OPEN for raw in port.sent), 1)
        self.assertEqual(self.lib.host_mask(), 0)
        self.assertTrue(port.closed)
        self.assertIn('key actions completed', output.getvalue())

    def test_cli_single_tap(self):
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=lambda: self.port)), \
                patch.object(usb.sys, 'platform', 'darwin'), \
                contextlib.redirect_stdout(io.StringIO()):
            usb.main(['keys', 'YES'])
        actions = [struct.unpack('>IIBBH', raw[3:]) for raw in self.port.sent if raw[2] == usb.ACTION]
        self.assertEqual([(a, key, ms) for _, _, a, key, ms in actions],
                         [(usb.TAP, usb.KEYS['YES'], 100)])
        self.assertEqual(self.lib.host_mask(), 0)
        self.assertTrue(self.port.closed)

    def test_sequence_accepts_whitespace_numbers_and_generic_holds(self):
        self.assertEqual(usb.key_sequence(' 1 , SRC ( FUNC ) , A_PUSH '),
                         [(None, '1'), ('SRC', 'FUNC'), (None, 'A_PUSH')])

    def test_invalid_sequences_do_not_open_midi(self):
        from unittest.mock import Mock
        factory = Mock(side_effect=AssertionError('must not open a port'))
        invalid = ('', ',YES', 'YES,', 'YES,,DOWN', 'FUNC()', 'FUNC(SRC,DOWN)',
                   'FUNC(SRC(DOWN))', 'FUNC(SRC)YES', 'UNKNOWN', 'FUNC(UNKNOWN)',
                   'UNKNOWN(SRC)', 'FUNC(FUNC)', 'down')
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=factory)), \
                patch.object(usb.sys, 'platform', 'darwin'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cases = [[text] for text in invalid]
            cases += [[], ['YES', 'DOWN'], ['--sequence', 'YES'],
                      ['--hold', 'FUNC', '--tap', 'SRC'], ['--tap', 'YES'],
                      ['YES', '--tap', '100'], ['YES', '--hold', '100'],
                      ['YES', '--tap-ms', '0'], ['YES', '--hold-ms', '2001']]
            for args in cases:
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    usb.main(['keys'] + args)
                self.assertEqual(error.exception.code, 2)
        factory.assert_not_called()

    def test_sequence_checks_all_device_keys_before_acting(self):
        with self.assertRaisesRegex(ValueError, 'does not have'), \
                contextlib.redirect_stdout(io.StringIO()):
            usb.cmd_keys(self.diag, usb.key_args(['YES,FUNC(T1)']))
        self.assertFalse(any(raw[2] == usb.ACTION for raw in self.port.sent))
        self.assertEqual(self.lib.host_mask(), 0)

    def test_sequence_failure_cancels_hold_and_skips_remaining_steps(self):
        self.lib.host_physical(8)  # SRC is physically held.
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                self.assertRaisesRegex(RuntimeError, 'overlap'):
            usb.cmd_keys(self.diag, usb.key_args(['FUNC(SRC),DOWN,YES']))
        actions = [struct.unpack('>IIBBH', raw[3:]) for raw in self.port.sent if raw[2] == usb.ACTION]
        self.assertEqual([(a, key) for _, _, a, key, _ in actions],
                         [(usb.PRESS, usb.KEYS['FUNC']), (usb.TAP, usb.KEYS['SRC'])])
        self.assertEqual(sum(raw[2] == usb.CANCEL for raw in self.port.sent), 1)
        self.assertEqual(self.lib.host_mask(), 8)
        self.assertNotIn('key actions completed', output.getvalue())

    def test_cli_help_and_invalid_keys_do_not_open_midi(self):
        from unittest.mock import Mock
        factory = Mock(side_effect=AssertionError('must not open a port'))
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=factory)), \
                patch.object(usb.sys, 'platform', 'darwin'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for args, status in ((['--help'], 0), (['SRC', '--tap-ms', '251'], 2)):
                with self.assertRaises(SystemExit) as error:
                    usb.main(['keys'] + args)
                self.assertEqual(error.exception.code, status)
        factory.assert_not_called()

    def test_reset_during_action_does_not_replay(self):
        keys = usb.RemoteKeys(self.diag)
        keys.press('FUNC')
        self.lib.host_reset()
        before = len(self.port.sent)
        with self.assertRaisesRegex(ValueError, 'stale'):
            keys.status()
        self.assertEqual([row[2] for row in self.port.sent[before:]], [usb.KEY_STATUS])

    def test_new_press_operation_cannot_renew_hold(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.press('FUNC', 100)
            keys.press('FUNC', 2000)
            self.lib.host_advance(100)
            self.assertEqual(keys.status()['state'], 'expired')
            self.assertEqual(self.lib.host_mask(), 0)

    def test_midi_dispatch_only_queues_and_duplicate_does_not_renew(self):
        self.wire(usb.OPEN, struct.pack('>I', 7))
        args = struct.pack('>IIBBH', 7, 1, usb.PRESS, 1, 100)
        self.assertEqual(self.wire(usb.ACTION, args)['status'], 0)
        self.assertEqual(self.lib.host_calls(), 0)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_mask(), 2)
        self.assertEqual(self.wire(usb.ACTION, args)['status'], 0)
        self.assertEqual(self.wire(usb.ACTION, args[:-1] + b'\x65')['status'], 1)
        self.assertEqual(self.wire(usb.OPEN, struct.pack('>I', 7))['status'], 0)
        self.assertEqual(self.wire(usb.OPEN, struct.pack('>I', 8))['status'], 3)
        self.lib.host_advance(100)
        self.assertEqual(self.lib.host_mask(), 0)
        self.wire(usb.ACTION, args)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_calls(), 2)

    def test_strict_requests_and_ranges(self):
        self.wire(usb.OPEN, struct.pack('>I', 7))
        args = struct.pack('>IIBBH', 7, 1, usb.TAP, 2, 100)
        for payload in (args[:-1], args + b'\0', args[:8] + bytes([9, 2, 0, 100]),
                        struct.pack('>IIBBH', 7, 1, usb.TAP, 2, 251),
                        struct.pack('>IIBBH', 7, 1, usb.PRESS, 1, 2001),
                        struct.pack('>IIBBH', 7, 1, usb.TAP, 58, 100),
                        struct.pack('>IIBBH', 7, 1, usb.RELEASE, 1, 1)):
            self.assertEqual(self.wire(usb.ACTION, payload)['status'], 1)
        data = bytearray(usb.request(3, usb.ACTION, args))
        for offset, bit in ((6, 128), (7, 128), (22, 1)):
            bad = data.copy(); bad[offset] |= bit
            self.port.send(bad)
            self.assertEqual(usb.parse(self.port.messages.pop())['status'], 1)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_calls(), 0)

    def test_lost_action_reply_queries_status_without_replay(self):
        with usb.RemoteKeys(self.diag) as keys:
            self.port.drop_reply = usb.ACTION
            keys.press('FUNC')
        self.assertEqual(sum(raw[2] == usb.ACTION for raw in self.port.sent), 1)
        self.assertEqual(self.lib.host_mask(), 0)

    def test_refused_action_does_not_consume_operation(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.press('FUNC')
            with self.assertRaisesRegex(ValueError, 'busy'):
                keys.press('SRC')
            self.assertEqual(keys.operation, 1)
            self.assertEqual(keys.status()['operation'], 1)
            keys.release('FUNC')
            self.assertEqual(self.lib.host_mask(), 0)
            keys.tap('SRC')
        actions = [struct.unpack('>IIBBH', raw[3:]) for raw in self.port.sent if raw[2] == usb.ACTION]
        self.assertEqual([(op, action, key) for _, op, action, key, _ in actions], [
            (1, usb.PRESS, usb.KEYS['FUNC']), (2, usb.PRESS, usb.KEYS['SRC']),
            (2, usb.RELEASE, usb.KEYS['FUNC']), (3, usb.TAP, usb.KEYS['SRC'])])

    def test_accepted_action_failure_still_consumes_operation(self):
        self.lib.host_physical(2)
        with usb.RemoteKeys(self.diag) as keys:
            with self.assertRaisesRegex(RuntimeError, 'overlap'):
                keys.press('FUNC')
            self.assertEqual(keys.operation, 1)
            self.assertEqual(keys.status()['operation'], 1)
            keys.tap('SRC')
            self.assertEqual(keys.operation, 2)
        self.assertEqual(self.lib.host_mask(), 2)

    def test_invalid_success_report_does_not_roll_back_operation(self):
        call = self.diag.call

        def truncated(builder, *args, **kwargs):
            payload = call(builder, *args, **kwargs)
            return payload[:-1] if builder == usb.req_action else payload

        with usb.RemoteKeys(self.diag) as keys:
            with patch.object(self.diag, 'call', side_effect=truncated), \
                    self.assertRaisesRegex(ValueError, 'invalid key status length'):
                keys.press('FUNC')
            self.assertEqual(keys.operation, 1)
            self.assertEqual(self.lib.host_mask(), 2)
            keys.release('FUNC')
            self.assertEqual(self.lib.host_mask(), 0)
        self.assertEqual(sum(raw[2] == usb.ACTION for raw in self.port.sent), 2)

    def test_lost_action_request_is_unknown_and_never_replayed(self):
        with usb.RemoteKeys(self.diag) as keys:
            self.port.drop_request = usb.ACTION
            with self.assertRaisesRegex(ValueError, 'stale'):
                keys.tap('SRC')
            self.assertEqual(keys.operation, 1)
        self.assertEqual(sum(raw[2] == usb.ACTION for raw in self.port.sent), 1)
        self.assertEqual(self.lib.host_calls(), 0)

    def test_cancel_lost_reply_and_busy_cleanup(self):
        keys = usb.RemoteKeys(self.diag)
        keys.press('FUNC')
        self.lib.host_busy(1)
        self.diag.call(usb.req_cancel, keys.session)
        self.assertEqual(self.lib.host_mask(), 2)
        self.assertEqual(keys.status()['cleanup'], 1)
        self.lib.host_busy(0)
        self.port.drop_reply = usb.CANCEL
        keys.cancel()
        self.assertEqual(self.lib.host_mask(), 0)

    def test_cancel_lost_request_is_not_reported_as_success(self):
        keys = usb.RemoteKeys(self.diag)
        keys.press('FUNC')
        self.port.drop_request = usb.CANCEL
        with self.assertRaisesRegex(TimeoutError, 'delivery unknown'):
            keys.cancel()
        self.lib.host_advance(2000)
        self.assertEqual(self.lib.host_mask(), 0)

    def test_physical_overlap_takeover_and_other_keys(self):
        self.lib.host_physical(2)
        with usb.RemoteKeys(self.diag) as keys:
            with self.assertRaisesRegex(RuntimeError, 'overlap'):
                keys.press('FUNC')
        self.assertEqual(self.lib.host_mask(), 2)
        self.lib.host_physical(1)
        with usb.RemoteKeys(self.diag) as keys:
            keys.press('FUNC')
            self.lib.host_physical(3)
        self.assertEqual(self.lib.host_mask(), 3)

    def test_pending_queue_timeout_and_clock_wrap(self):
        keys = usb.RemoteKeys(self.diag)
        self.lib.host_clock(0xff000000)
        keys.press('FUNC', 100)
        self.lib.host_advance(100)
        self.assertEqual(keys.status()['state'], 'expired')
        self.assertEqual(self.lib.host_mask(), 0)
        keys.cancel()
        keys = usb.RemoteKeys(self.diag)
        self.lib.host_busy(1)
        self.diag.call(usb.req_action, keys.session, 1, usb.PRESS, 1, 100)
        self.lib.host_advance(2000)
        self.lib.host_busy(0)
        self.lib.host_advance(1)
        self.assertEqual(keys.status(1)['state'], 'expired')
        self.assertEqual(self.lib.host_mask(), 0)

    def test_ui_stall_delays_cleanup_until_tick(self):
        keys = usb.RemoteKeys(self.diag)
        keys.press('FUNC', 100)
        self.lib.host_clock(1000 * 132000)
        self.assertEqual(self.lib.host_mask(), 2, 'clock advance alone must not execute UI work')
        self.lib.host_advance(0)
        self.assertEqual(self.lib.host_mask(), 0)
        self.assertEqual(keys.status()['state'], 'expired')

    def test_busy_pending_stale_operations_and_session_cleanup(self):
        self.wire(usb.OPEN, struct.pack('>I', 7))
        def action(number, a=usb.TAP, k=2, ms=100):
            return self.wire(usb.ACTION, struct.pack('>IIBBH', 7, number, a, k, ms))['status']
        self.assertEqual(action(2), 4)
        self.assertEqual(action(1), 0)
        self.assertEqual(action(2), 3)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_mask(), 8)
        self.assertEqual(action(1), 0)
        self.lib.host_advance(100)
        self.assertEqual(self.lib.host_mask(), 0)
        self.assertEqual(action(2, usb.PRESS, 1, 100), 0)
        self.lib.host_advance(1)
        self.assertEqual(action(1), 4)
        self.assertEqual(self.wire(usb.CANCEL, struct.pack('>I', 8))['status'], 4)
        self.assertEqual(self.wire(usb.CANCEL, struct.pack('>I', 7))['status'], 0)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_mask(), 0)
        self.assertEqual(action(3), 4)
        self.assertEqual(self.wire(usb.OPEN, struct.pack('>I', 7))['status'], 4)

    def test_unsupported_caps_refused_before_open(self):
        old_caps = struct.pack('>HBBBHHHH', 1, 1, 1, 53, 3, 100, 250, 2000)
        from unittest.mock import Mock
        old = Mock()
        old.call.return_value = old_caps
        with self.assertRaisesRegex(ValueError, 'invalid key capabilities'):
            usb.RemoteKeys(old)
        old.call.assert_called_once_with(usb.req_caps)
        for field, value in ((0, 1), (1, 3), (3, 55), (4, 0), (6, 251)):
            caps = [2, 1, 1, 53, usb.SUPPORTED[1], 100, 250, 2000]
            caps[field] = value
            diag = types.SimpleNamespace(call=lambda *_args: struct.pack('>HBBBQHHH', *caps))
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                usb.RemoteKeys(diag)

    def test_retries_are_reads_only(self):
        for builder, args, expected in ((usb.req_caps, (), 2), (usb.req_open, (7,), 1),
                                        (usb.req_action, (7, 1, 3, 1, 100), 1),
                                        (usb.req_cancel, (7,), 1)):
            self.port.sent.clear()
            with patch.object(self.port, 'receive', return_value=[]):
                with self.assertRaises(TimeoutError):
                    self.diag.call(builder, *args)
            self.assertEqual(len(self.port.sent), expected)
            self.port.messages.clear()

    def test_no_encoder_input_reports_none_and_refuses_turn(self):
        caps = self.diag.call(usb.req_caps)
        self.assertEqual((len(caps), caps[19]), (20, 0))
        self.wire(usb.OPEN, struct.pack('>I', 7))
        self.assertEqual(self.wire(usb.ACTION, struct.pack('>IIBBH', 7, 1, usb.TURN, 1, 0x0101))
                         ['status'], 1)
        with usb.RemoteKeys(self.diag) as keys:
            self.assertEqual(keys.encoders, {})
            with self.assertRaisesRegex(ValueError, 'unsupported encoder'):
                keys.turn('A', 1)

    def test_revision_2_caps_still_accepted(self):
        caps = struct.pack('>HBBBQHHH', 2, 1, 1, 53, usb.SUPPORTED[1], 100, 250, 2000)

        def call(builder, *args, **kwargs):
            if builder == usb.req_caps:
                return caps
            return self.diag.call(builder, *args, **kwargs)
        keys = usb.RemoteKeys(types.SimpleNamespace(call=call))
        self.assertEqual(keys.encoders, {})
        keys.cancel()


class USBTurns(unittest.TestCase):
    """TURN on a build with the encoder input: the Digitone mk1 OS 1.44 port's flags."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        library = str(Path(cls.tmp.name) / 'turns.so')
        subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-DRK_PRODUCT=2', '-DRK_OS_MINOR=44', '-DRK_ENCODERS=9',
                        str(ROOT / 'tests/fixtures/remote_keys_host.c'), '-o', library], check=True)
        cls.lib = ctypes.CDLL(library)
        cls.lib.rk_receive.argtypes = (ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p)

    def setUp(self):
        self.lib.host_reset()
        self.port = HostPort(self.lib)
        self.diag = usb.Diag(self.port)

    def wire(self, command, payload=b''):
        self.port.send(usb.request(0xfedc, command, payload))
        return usb.parse(self.port.messages.pop())

    def test_caps_report_revision_3_and_nine_encoders(self):
        caps = self.diag.call(usb.req_caps)
        self.assertEqual(len(caps), 20)
        self.assertEqual(struct.unpack_from('>HBBB', caps), (3, 2, 1, 44))
        self.assertEqual(caps[19], 9)
        with usb.RemoteKeys(self.diag) as keys:
            self.assertEqual(sorted(keys.encoders.values()), list(range(1, 10)))
            self.assertEqual(keys.encoders['LEVEL'], 9)

    def test_turn_sends_one_message_a_tick(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.turn('E', -12)
        self.assertEqual(self.lib.host_turned(4), -12)
        self.assertEqual(self.lib.host_turn_calls(), 12)
        self.assertEqual(sum(self.lib.host_turned(i) for i in range(9)), -12)

    def test_turn_steps_and_long_turns_split(self):
        with usb.RemoteKeys(self.diag) as keys:
            keys.turn('A', 12, step=5)
            keys.turn('LEVEL', 70)
        actions = [struct.unpack('>IIBBH', raw[3:]) for raw in self.port.sent if raw[2] == usb.ACTION]
        self.assertEqual([(a, k, ms) for _, _, a, k, ms in actions], [
            (usb.TURN, 1, 5 << 8 | 2), (usb.TURN, 1, 2 << 8 | 1),
            (usb.TURN, 9, 1 << 8 | 30), (usb.TURN, 9, 1 << 8 | 30), (usb.TURN, 9, 1 << 8 | 10)])
        self.assertEqual((self.lib.host_turned(0), self.lib.host_turned(8)), (12, 70))

    def test_turn_with_a_held_key(self):
        port = self.port
        with patch.dict(sys.modules, midimido=types.SimpleNamespace(Port=lambda: port)), \
                patch.object(usb.sys, 'platform', 'darwin'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            usb.main(['turn', 'b', '3', '--hold', 'FUNC'])
        self.assertIn('turn completed', output.getvalue())
        actions = [raw[11] for raw in port.sent if raw[2] == usb.ACTION]
        self.assertEqual(actions, [usb.PRESS, usb.TURN, usb.RELEASE])
        self.assertEqual(self.lib.host_turned(1), 3)
        self.assertEqual(self.lib.host_mask(), 0)

    def test_full_ui_queue_delays_a_turn_and_never_drops_it(self):
        self.wire(usb.OPEN, struct.pack('>I', 7))
        self.lib.host_busy(1)
        self.assertEqual(self.wire(usb.ACTION, struct.pack('>IIBBH', 7, 1, usb.TURN, 3, 2 << 8 | 2))
                         ['status'], 0)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_turn_calls(), 0)
        self.lib.host_busy(0)
        self.lib.host_advance(1)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_turned(2), 4)
        self.assertEqual(self.wire(usb.KEY_STATUS, struct.pack('>II', 7, 1))['payload'][8], 2)

    def test_strict_turn_arguments(self):
        self.wire(usb.OPEN, struct.pack('>I', 7))
        for k, ms in ((0, 0x0101), (10, 0x0101), (1, 0x0100), (1, 0x011f), (1, 0x0001),
                      (1, 0x1f01), (1, 0xe101)):
            self.assertEqual(self.wire(usb.ACTION, struct.pack('>IIBBH', 7, 1, usb.TURN, k, ms))
                             ['status'], 1, (k, hex(ms)))
        self.assertEqual(self.wire(usb.ACTION, struct.pack('>IIBBH', 7, 1, usb.TURN, 1, 0xe201))
                         ['status'], 0)
        self.lib.host_advance(1)
        self.assertEqual(self.lib.host_turned(0), -30)

    def test_client_refuses_bad_turns_before_sending(self):
        with usb.RemoteKeys(self.diag) as keys:
            for args in (('Z', 1), ('A', 0), ('A', 1, 0), ('A', 1, 31)):
                with self.assertRaises(ValueError):
                    keys.turn(*args)
        self.assertFalse(any(raw[2] == usb.ACTION for raw in self.port.sent))


class MidiTransport(unittest.TestCase):
    def test_digitone_mido_port_names(self):
        from unittest.mock import Mock
        midi = Mock()
        midi.get_input_names.return_value = ['Digitone MIDI']
        midi.get_output_names.return_value = ['Digitone MIDI']
        with patch.dict(sys.modules, mido=midi):
            port = Port()
            midi.open_input.assert_called_once_with('Digitone MIDI')
            midi.open_output.assert_called_once_with('Digitone MIDI')
            port.close()
            midi.get_input_names.return_value = ['Digitakt MIDI', 'Digitone MIDI']
            with self.assertRaisesRegex(OSError, 'Expected one'):
                Port()

    def test_windows_device_discovery_without_loading_winmm(self):
        import importlib.util
        from unittest.mock import Mock
        spec = importlib.util.spec_from_file_location('winmidi_test', ROOT / 'tools/winmidi.py')
        module = importlib.util.module_from_spec(spec)
        with patch.object(ctypes, 'WinDLL', return_value=Mock(), create=True):
            spec.loader.exec_module(module)
        for names, select, expected in ((['Digitakt MIDI'], None, (0, 0)),
                                        (['Digitone MIDI'], None, (0, 0)),
                                        (['Digitakt MIDI', 'Digitone MIDI'], 'Digitone', (1, 1))):
            with patch.object(module, '_names', side_effect=[names, names]):
                self.assertEqual(module.find(select), expected)
        for names in ([], ['Digitakt MIDI', 'Digitone MIDI']):
            with patch.object(module, '_names', side_effect=[names, names]):
                with self.assertRaisesRegex(OSError, 'expected one'):
                    module.find()

    def test_send_receive_and_failed_open_cleanup(self):
        from unittest.mock import Mock
        midi = Mock()
        midi.get_input_names.return_value = ['Digitakt MIDI']
        midi.get_output_names.return_value = ['Digitakt MIDI']
        msg = Mock(type='sysex'); msg.bytes.return_value = [240, 1, 247]
        midi.open_input.return_value.iter_pending.return_value = [msg]
        with patch.dict(sys.modules, mido=midi):
            port = Port()
            port.send(b'\xf0\x01\xf7')
            midi.Message.from_bytes.assert_called_once_with(b'\xf0\x01\xf7')
            self.assertEqual(port.receive(until=bool), [b'\xf0\x01\xf7'])
            port.close()
            midi.open_output.side_effect = OSError('busy')
            with self.assertRaises(OSError):
                Port()
            self.assertEqual(midi.open_input.return_value.close.call_count, 2)
            midi.get_input_names.return_value = ['Digitakt A', 'Digitakt B']
            with self.assertRaisesRegex(OSError, 'Expected one'):
                Port()


if __name__ == '__main__':
    unittest.main()
