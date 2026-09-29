# SPDX-License-Identifier: GPL-2.0-or-later
"""Read digihealth's diagnostics from a Digitakt mk1 over USB MIDI.

    python digiusb.py cfw                  which mod answers, and its uptime
    python digiusb.py stats [N]            CPU, DSP, clock, heap: N readings, one a second
    python digiusb.py peek ADDR [LEN]      hex dump of DDR or the SRAM (0x8000xxxx)
    python digiusb.py dump ADDR LEN OUT    DDR to a file
    python digiusb.py selftest             check the encoder and decoder, no device

digihealth answers its own SysEx channel: Elektron's manufacturer header
with a device byte (0x7D) no Elektron machine uses, so a stock OS drops the
messages and talking to it does nothing. Every command only reads; the mod
has no command that writes.

    request  F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd, payload> F7
    reply    F0 00 20 3C 7D 00 <pack7: u16 seq, u8 cmd | 0x80, u8 status,
                                payload> F7

Integers are big-endian. status 0 is ok, 1 an argument the mod refuses, 2 an
unknown command. Windows only (winmidi.py, through ctypes: no packages).
Windows lets one program at a time open a MIDI port: close Elektron
Transfer first.
"""
import struct
import sys
import time

HEADER = bytes([0xF0, 0x00, 0x20, 0x3C, 0x7D, 0x00])
HELLO, STATS, PEEK = 0x01, 0x02, 0x03
STATUS = {0: 'ok', 1: 'refused', 2: 'unknown command'}
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


class Diag:
    """The channel, over a winmidi Port. A stock OS never answers."""

    def __init__(self, port):
        self.port = port
        self.seq = 0x200

    def call(self, builder, *args, timeout=2.0, tries=2):
        """Every command only reads, so a lost request or reply (seen now and
        then on the first message after the port opens) is asked again."""
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
                found.append(r if r and r['seq'] == seq and r['cmd'] == cmd | 0x80 else None)
            return any(found)
        self.port.receive(timeout, until=answered)
        for r in found:
            if r:
                if r['status']:
                    raise ValueError('the mod refused 0x%02x: %s'
                                     % (cmd, STATUS.get(r['status'], r['status'])))
                return r['payload']
        raise TimeoutError('no answer: is a firmware with digihealth installed?')


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


COMMANDS = {'cfw': cmd_cfw, 'stats': cmd_stats, 'peek': cmd_peek, 'dump': cmd_dump}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['selftest']:
        selftest()
        return
    if not argv or argv[0] not in COMMANDS:
        sys.exit(__doc__)
    from winmidi import Port
    port = Port()
    try:
        COMMANDS[argv[0]](Diag(port), argv[1:])
    finally:
        port.close()


if __name__ == '__main__':
    main()
