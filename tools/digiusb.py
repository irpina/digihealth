# SPDX-License-Identifier: GPL-2.0-or-later
"""Read digihealth diagnostics or operate bounded Digitakt/Digitone mk1 keys over USB MIDI.

    python digiusb.py cfw                  which mod answers, and its uptime
    python digiusb.py stats [N]            CPU, DSP, clock, heap: N readings, one a second
    python digiusb.py peek ADDR [LEN]      hex dump of DDR or the SRAM (0x8000xxxx)
    python digiusb.py dump ADDR LEN OUT    DDR to a file
    python digiusb.py selftest             check the encoder and decoder, no device
    python digiusb.py keys YES
    python digiusb.py keys 'FUNC(SRC)'
    python digiusb.py keys 'FUNC(SRC),DOWN,DOWN,DOWN,YES'
    python digiusb.py turn E -12 [--step N] [--hold FUNC]   encoder E 12 counts left

digihealth answers its own SysEx channel: Elektron's manufacturer header
with a device byte (0x7D) no Elektron machine uses, so a stock OS drops the
messages and talking to it does nothing. Diagnostics only read. The optional
key helper supports bounded individual key actions after capability detection.

    request  F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd, payload> F7
    reply    F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd | 0x80, u8 status,
                                payload> F7

Integers are big-endian. status 0 is ok, 1 an argument the mod refuses, 2 an
unknown command. Windows uses winmidi.py (ctypes: no packages); macOS/Linux
use Mido + python-rtmidi (midimido.py).
Windows lets one program at a time open a MIDI port: close Elektron
Transfer first.
"""
import argparse
import re
import secrets
import struct
import sys
import time
from keymap import KEYS, MAPS, SUPPORTED

HEADER = bytes([0xF0, 0x00, 0x20, 0x3C, 0x7D, 0x00])
HELLO, STATS, PEEK = 0x01, 0x02, 0x03
STATUS = {0: 'ok', 1: 'refused', 2: 'unknown command', 3: 'busy',
          4: 'stale session or operation', 5: 'physical overlap',
          6: 'remote keys are off (SETTINGS > REMOTE KEYS)'}
CAPS, OPEN, ACTION, KEY_STATUS, CANCEL = range(0x10, 0x15)
PRESS, RELEASE, TAP, TURN = 1, 2, 3, 4
MAX_TURNS, MAX_STEP = 30, 30            # per TURN action: UI ticks, counts per tick
# Encoders by the number TURN names them with; CAPS says how many a build turns
# (revision 3; a revision 2 build has none).
ENCODER_NAMES = ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'LEVEL')
KEY_STATES = ('none', 'accepted', 'completed', 'cancelled', 'expired', 'failed')
PEEK_MAX = 1024
# PEEK reads DDR (cached view) and the 64 KB SRAM, and never the peripheral
# space (a read there can clear a status bit).
PEEK_RANGES = ((0x40000000, 0x48000000), (0x80000000, 0x80010000))
ORDERS = 24
RENDERS_PER_S = 1500          # the audio render: 48 kHz in blocks of 32


def pack7(data):
    """8-bit -> 7-bit: a header byte holding the high bits of up to 7 bytes."""
    data = bytes(data)
    out = bytearray()
    for i in range(0, len(data), 7):
        grp = data[i:i + 7]
        hdr = 0
        for k, b in enumerate(grp):
            if b & 0x80:
                hdr |= 1 << (6 - k)
        out.append(hdr)
        out.extend(b & 0x7F for b in grp)
    return bytes(out)


def unpack7(data):
    """7-bit -> 8-bit (a partial last group is allowed)."""
    data = bytes(data)
    out = bytearray()
    i = 0
    while i < len(data):
        hdr = data[i]
        i += 1
        for k in range(1, 8):
            if i >= len(data):
                break
            out.append(data[i] | ((hdr << k) & 0x80))
            i += 1
    return bytes(out)


def request(seq, cmd, payload=b''):
    return HEADER + pack7(struct.pack('>HB', seq & 0xFFFF, cmd) + payload) + b'\xF7'


def req_hello(seq):
    return request(seq, HELLO)


def req_stats(seq):
    return request(seq, STATS)


def req_caps(seq):
    return request(seq, CAPS)


def req_open(seq, session):
    return request(seq, OPEN, struct.pack('>I', session))


def req_action(seq, session, operation, action, key, duration):
    return request(seq, ACTION, struct.pack('>IIBBH', session, operation, action, key, duration))


def req_key_status(seq, session, operation):
    return request(seq, KEY_STATUS, struct.pack('>II', session, operation))


def req_cancel(seq, session):
    return request(seq, CANCEL, struct.pack('>I', session))


def req_peek(seq, addr, length):
    if not 0 < length <= PEEK_MAX:
        raise ValueError('length must be 1..%d' % PEEK_MAX)
    if not any(lo <= addr and addr + length <= hi for lo, hi in PEEK_RANGES):
        raise ValueError('0x%08x+%d is outside DDR (0x40000000-0x47ffffff) and '
                         'SRAM (0x80000000-0x8000ffff)' % (addr, length))
    return request(seq, PEEK, struct.pack('>IH', addr, length))


def parse(sx):
    """A reply -> {'seq', 'cmd', 'status', 'payload'}, or None if it is not one."""
    sx = bytes(sx)
    if not sx.startswith(HEADER) or not sx.endswith(b'\xF7'):
        return None
    msg = unpack7(sx[len(HEADER):-1])
    if len(msg) < 4:
        return None
    seq, cmd, status = struct.unpack_from('>HBB', msg)
    return {'seq': seq, 'cmd': cmd, 'status': status, 'payload': msg[4:]}


def decode_hello(p):
    proto_ver, uptime = struct.unpack_from('>HI', p)
    name = p[6:].split(b'\0')[0].decode('ascii', 'replace')
    return {'proto': proto_ver, 'uptime': uptime, 'name': name}


# The mod's snapshot, byte for byte (sysinfo.s, S_*): refreshed once a second.
STATS_FIELDS = ('layout', 'flags', 'seconds', 'window', 'render_ticks',
                'render_max', 'renders', 'idle_ticks', 'idle_render', 'heap_free',
                'heap_faults', 'heap_top_order', 'sample_free')
STATS_FMT = '>HHIIIIIIIIIII'


def decode_stats(p):
    d = dict(zip(STATS_FIELDS, struct.unpack_from(STATS_FMT, p)))
    off = struct.calcsize(STATS_FMT)
    d['free_blocks'] = list(struct.unpack_from('>%dH' % ORDERS, p, off))
    d['valid'] = bool(d['flags'] & 1)
    d['overlay'] = bool(d['flags'] & 2)
    d['fast'] = bool(d['flags'] & 4)            # FAST AUDIO: SRAM copies run
    d['fast_fault'] = bool(d['flags'] & 8)      # refused, or undone
    w, n = d['window'], d['renders']
    if w and n:
        period = w / n                               # counter ticks per render
        d['counter_hz'] = period * RENDERS_PER_S
        d['dsp'] = d['render_ticks'] / w
        d['dsp_peak'] = d['render_max'] / period
        d['cpu'] = 1 - max(0, d['idle_ticks'] - d['idle_render']) / w
    return d


def decode_peek(p):
    addr, = struct.unpack_from('>I', p)
    return {'addr': addr, 'data': p[4:]}


class _RequestRefused(ValueError):
    pass


class Diag:
    """The channel, over a send/receive Port. A stock OS never answers."""

    def __init__(self, port):
        self.port = port
        self.seq = 0x200

    def call(self, builder, *args, timeout=2.0, tries=2):
        """Only known reads may be retried; actions need explicit status recovery."""
        if builder not in (req_hello, req_stats, req_peek, req_caps, req_key_status):
            tries = 1
        if tries < 1:
            raise ValueError('tries must be positive')
        for attempt in range(tries):
            try:
                return self._call(builder, *args, timeout=timeout)
            except TimeoutError:
                if attempt == tries - 1:
                    raise

    def _call(self, builder, *args, timeout=2.0):
        self.seq = (self.seq + 1) & 0xFFFF or 1
        seq = self.seq
        req = builder(seq, *args)
        cmd = unpack7(req[len(HEADER):-1])[2]
        self.port.send(req)
        found = []

        def answered(msgs):
            for m in msgs[len(found):]:
                r = parse(m)
                if CAPS <= cmd <= CANCEL and bytes(m) != HEADER + pack7(unpack7(bytes(m)[6:-1])) + b'\xf7':
                    r = None
                found.append(r if r and r['seq'] == seq and r['cmd'] == cmd | 0x80 else None)
            return any(found)
        self.port.receive(timeout, until=answered)
        for r in found:
            if r:
                if r['status']:
                    raise _RequestRefused('the mod refused 0x%02x: %s'
                                          % (cmd, STATUS.get(r['status'], r['status'])))
                return r['payload']
        raise TimeoutError('no answer: is a firmware with digihealth installed?')


class RemoteKeys:
    """One bounded session. A timeout queries status and never replays an action."""

    def __init__(self, diag):
        self.diag = diag
        payload = diag.call(req_caps)
        if len(payload) not in (19, 20):
            raise ValueError('invalid key capabilities')
        revision, product, major, minor, keys, default, tap, hold = struct.unpack_from('>HBBBQHHH', payload)
        encoders = payload[19] if len(payload) == 20 else 0
        if (len(payload) != (20 if revision == 3 else 19) or revision not in (2, 3)
                or (product, major, minor) not in ((1, 1, 53), (1, 1, 54), (2, 1, 44))
                or keys != SUPPORTED.get(product) or not 1 <= default <= tap <= 250
                or not 1 <= hold <= 2000 or encoders > len(ENCODER_NAMES)):
            raise ValueError('unsupported key helper, device, OS or limits')
        self.default_tap, self.max_tap, self.max_hold = default, tap, hold
        self.product, self.supported = product, keys
        self.encoders = {name: i + 1 for i, name in enumerate(ENCODER_NAMES[:encoders])}
        self.session = secrets.randbits(32) or 1
        self.operation = 0
        self._report(diag.call(req_open, self.session, timeout=0.25), 0)

    def _report(self, payload, operation):
        if len(payload) != 19:
            raise ValueError('invalid key status length')
        owner, number, state, reason, owned, cleanup = struct.unpack('>IIBBQB', payload)
        if (owner != self.session or (operation and number != operation)
                or state >= len(KEY_STATES) or reason not in STATUS
                or owned & ~self.supported or cleanup > 1):
            raise ValueError('invalid or mismatched key status')
        return dict(operation=number, state=KEY_STATES[state], reason=STATUS[reason],
                    owned=owned, cleanup=cleanup)

    def status(self, operation=None):
        operation = self.operation if operation is None else operation
        return self._report(self.diag.call(req_key_status, self.session, operation,
                                          timeout=0.25), operation)

    def _wait(self, report, cancelling=False):
        deadline = time.monotonic() + 3
        while report['state'] == 'accepted' or report['cleanup']:
            if time.monotonic() >= deadline:
                raise TimeoutError('key cleanup or action still pending; not replayed')
            time.sleep(0.01)
            report = self.status(0 if cancelling else None)
        if cancelling:
            if report['owned']:
                raise TimeoutError('cleanup unconfirmed; firmware hold lease remains in force')
        elif report['state'] != 'completed':
            raise RuntimeError('key action %s: %s' % (report['state'], report['reason']))
        return report

    def action(self, action, key, duration=0):
        if action == TURN:
            ticks, step = duration & 0xFF, (duration >> 8) - (duration >> 7 & 0x100)
            if (key not in self.encoders or not 1 <= ticks <= MAX_TURNS or not step
                    or abs(step) > MAX_STEP):
                raise ValueError('unsupported encoder or turn')
            code = self.encoders[key]
        else:
            if key not in MAPS[self.product] or action not in (PRESS, RELEASE, TAP):
                raise ValueError('unsupported key or action')
            limit = self.max_hold if action == PRESS else self.max_tap
            if action == RELEASE and duration != 0 or action != RELEASE and not 1 <= duration <= limit:
                raise ValueError('invalid key duration')
            code = KEYS[key]
        self.operation += 1
        try:
            payload = self.diag.call(req_action, self.session, self.operation, action,
                                     code, duration, timeout=0.25)
            report = self._report(payload, self.operation)
        except _RequestRefused:
            self.operation -= 1
            raise
        except TimeoutError:
            report = self.status()
        return self._wait(report)

    def press(self, key, duration=None):
        return self.action(PRESS, key, self.max_hold if duration is None else duration)

    def release(self, key):
        return self.action(RELEASE, key)

    def tap(self, key, duration=None):
        return self.action(TAP, key, self.default_tap if duration is None else duration)

    def turn(self, encoder, delta, step=1):
        """Turn `encoder` (A-H, LEVEL) by `delta` counts, `step` counts per UI tick,
        as TURN actions of up to MAX_TURNS ticks each."""
        if not delta or not 1 <= step <= MAX_STEP:
            raise ValueError('use a nonzero delta and a step of 1..%d' % MAX_STEP)
        sign, left = (1 if delta > 0 else -1), abs(delta)
        while left:
            size = min(step, left)
            ticks = min(MAX_TURNS, left // size)
            self.action(TURN, encoder, ((sign * size) & 0xFF) << 8 | ticks)
            left -= size * ticks

    def cancel(self):
        try:
            report = self._report(self.diag.call(req_cancel, self.session, timeout=0.25), 0)
        except TimeoutError:
            report = self.status(0)
            if report['state'] not in ('cancelled', 'expired') and not report['cleanup']:
                raise TimeoutError('cancel delivery unknown; not replayed, hold lease remains in force')
        return self._wait(report, cancelling=True)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cancel()


def key_sequence(text):
    steps = []
    for token in text.split(','):
        match = re.fullmatch(r'([A-Z0-9_]+)(?:\s*\(\s*([A-Z0-9_]+)\s*\))?', token.strip())
        if not match:
            raise argparse.ArgumentTypeError('use KEY or HOLD(TAP), separated by commas')
        outer, inner = match.groups()
        hold, tap = (outer, inner) if inner else (None, outer)
        if tap not in KEYS or hold is not None and hold not in KEYS:
            raise argparse.ArgumentTypeError('unknown button in %r' % token.strip())
        if hold == tap:
            raise argparse.ArgumentTypeError('held and tapped buttons must differ')
        steps.append((hold, tap))
    return steps


def key_args(args):
    parser = argparse.ArgumentParser(
        description='INPUT is KEY, HOLD(TAP), or a comma-separated list of either.',
        allow_abbrev=False)
    parser.add_argument('steps', type=key_sequence, metavar='INPUT')
    parser.add_argument('--tap-ms', type=int, default=100)
    parser.add_argument('--hold-ms', type=int, default=2000)
    args = parser.parse_args(args)
    if not 1 <= args.tap_ms <= 250 or not 1 <= args.hold_ms <= 2000:
        parser.error('use tap 1..250 ms and hold 1..2000 ms')
    return args


def turn_args(args):
    parser = argparse.ArgumentParser(
        prog='digiusb.py turn',
        description='Turn an encoder as the panel does: ENCODER is A-H or LEVEL; DELTA the '
                    'counts (negative turns left).', allow_abbrev=False)
    parser.add_argument('encoder', type=str.upper, choices=ENCODER_NAMES, metavar='ENCODER')
    parser.add_argument('delta', type=int)
    parser.add_argument('--step', type=int, default=1,
                        help='counts per UI tick, 1..%d (default 1; larger is faster and '
                             'meets the OS acceleration)' % MAX_STEP)
    parser.add_argument('--hold', choices=sorted(KEYS), metavar='KEY',
                        help='hold this key during the turn (e.g. FUNC)')
    args = parser.parse_args(args)
    if not args.delta or not 1 <= args.step <= MAX_STEP:
        parser.error('use a nonzero DELTA and --step 1..%d' % MAX_STEP)
    if args.hold and -(-abs(args.delta) // args.step) > MAX_TURNS:
        parser.error('with --hold, a turn must fit one action (%d ticks): raise --step'
                     % MAX_TURNS)
    return args


def cmd_turn(diag, args):
    with RemoteKeys(diag) as keys:
        if args.encoder not in keys.encoders or args.hold and args.hold not in MAPS[keys.product]:
            raise ValueError('the connected device does not turn that encoder or have that button')
        if args.hold:
            keys.press(args.hold)
        keys.turn(args.encoder, args.delta, args.step)
        if args.hold:
            keys.release(args.hold)
    print('turn completed')


def cmd_keys(diag, args):
    with RemoteKeys(diag) as keys:
        if any(tap not in MAPS[keys.product] or hold is not None and hold not in MAPS[keys.product]
               for hold, tap in args.steps):
            raise ValueError('the connected device does not have the requested button')
        for hold, tap in args.steps:
            if hold:
                keys.press(hold, args.hold_ms)
            keys.tap(tap, args.tap_ms)
            if hold:
                keys.release(hold)
    print('key actions completed; synthetic keys released')


def cmd_cfw(d, _args):
    h = decode_hello(d.call(req_hello))
    print('%s  (channel v%d, up %d s)' % (h['name'], h['proto'], h['uptime']))


def show_stats(s):
    print('second %d%s%s' % (s['seconds'], '' if s['overlay'] else '  (SYSTEM INFO off)',
                             '  FAST AUDIO on' if s['fast'] else '')
          + ('  (FAST AUDIO refused or undone)' if s['fast_fault'] else ''))
    if 'dsp' in s:
        print('  CPU %5.1f %%   DSP %5.1f %%   DSP peak %5.1f %%'
              % (100 * s['cpu'], 100 * s['dsp'], 100 * s['dsp_peak']))
        print('  %d renders in %d counter ticks: counter %.3f MHz'
              % (s['renders'], s['window'], s['counter_hz'] / 1e6))
    else:
        print('  no renders this second (audio stopped, or the counter is not running)')
    blocks = ', '.join('%d:%d' % (o, n) for o, n in enumerate(s['free_blocks']) if n)
    print('  heap free %.2f MB (%d bytes), faults %d, free blocks by order: %s'
          % (s['heap_free'] / 1048576, s['heap_free'], s['heap_faults'], blocks or '-'))
    print('  sample pool free %.2f MB' % (s['sample_free'] / 1048576))


def cmd_stats(d, args):
    last = None
    for _ in range(int(args[0]) if args else 1):
        while True:
            s = decode_stats(d.call(req_stats))
            if s['seconds'] != last:
                break
            time.sleep(0.1)
        last = s['seconds']
        show_stats(s)


def _range(args):
    addr = int(args[0], 0)
    length = int(args[1], 0) if len(args) > 1 else 256
    return addr, length


def peek(d, addr, length):
    out = b''
    while len(out) < length:
        n = min(PEEK_MAX, length - len(out))
        r = decode_peek(d.call(req_peek, addr + len(out), n))
        if r['addr'] != addr + len(out) or len(r['data']) != n:
            raise ValueError('short or misplaced PEEK reply at 0x%08x' % r['addr'])
        out += r['data']
    return out


def cmd_peek(d, args):
    addr, length = _range(args)
    data = peek(d, addr, length)
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        print('%08x  %-48s %s' % (addr + i, row.hex(' '),
                                   ''.join(chr(b) if 32 <= b < 127 else '.' for b in row)))


def cmd_dump(d, args):
    addr, length = _range(args[:2])
    data = peek(d, addr, length)
    with open(args[2], 'wb') as fh:
        fh.write(data)
    print('saved %s (%d bytes from 0x%08x)' % (args[2], len(data), addr))


def selftest():
    sx = req_peek(7, 0x4020bb14, 20)
    assert sx[:6] == HEADER and all(b < 0x80 for b in sx[1:-1])
    assert unpack7(sx[6:-1]) == struct.pack('>HBIH', 7, PEEK, 0x4020bb14, 20)
    reply = HEADER + pack7(struct.pack('>HBBI', 7, PEEK | 0x80, 0, 0x4020bb14)
                           + bytes(range(20))) + b'\xF7'
    r = parse(reply)
    assert r['status'] == 0 and decode_peek(r['payload'])['data'] == bytes(range(20))
    req_peek(1, 0x80000000, 16)
    req_peek(1, 0x8000FFF0, 16)
    for bad in ((0xFC07000C, 4), (0x47FFFFFF, 2), (0x40000000, 0),
                (0x40000000, PEEK_MAX + 1), (0x7FFFFFFE, 4), (0x8000FFFE, 4)):
        try:
            req_peek(1, *bad)
        except ValueError:
            continue
        raise AssertionError('accepted %r' % (bad,))
    st = struct.pack(STATS_FMT, 1, 1, 60, 66000000, 39600000, 30000, 1500,
                     26000000, 0, 14000000, 0, 20, 60000000) + bytes(2 * ORDERS)
    assert len(st) == 96
    s = decode_stats(st)
    assert round(s['counter_hz']) == 66000000 and round(s['dsp'], 2) == 0.60
    print('selftest ok')


COMMANDS = {'cfw': cmd_cfw, 'stats': cmd_stats, 'peek': cmd_peek, 'dump': cmd_dump, 'keys': cmd_keys,
            'turn': cmd_turn}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['selftest']:
        selftest()
        return
    if not argv or argv[0] not in COMMANDS:
        sys.exit(__doc__)
    args = (key_args(argv[1:]) if argv[0] == 'keys' else turn_args(argv[1:]) if argv[0] == 'turn'
            else argv[1:])
    if sys.platform == 'win32':
        from winmidi import Port
    else:
        from midimido import Port
    port = Port()
    try:
        COMMANDS[argv[0]](Diag(port), args)
    finally:
        port.close()


if __name__ == '__main__':
    main()
