# SPDX-License-Identifier: GPL-2.0-or-later
"""Elektron's own MIDI RPC (the channel Transfer uses), over the same Port as
digiusb.py: Ping, SoftwareVersion, Screenshot and the path-addressed data store
that holds projects and sounds.

    request  F0 00 20 3C 10 00 <pack7: u16 seq, u16 0, u8 cmd, args> F7
    reply    F0 00 20 3C 10 00 <pack7: u16 own seq, u16 request seq,
                                u8 cmd | 0x80, args> F7

The stock OS answers these, so everything here works with or without
digihealth installed. Integers are big-endian, strings are null-terminated.
The data store layout follows what elektroid (github.com/dagargo/elektroid)
sends and reads; Screenshot is the request elektron-screen-tool uses.
"""
import struct
import zlib

from digiusb import pack7, unpack7

HEADER = bytes([0xF0, 0x00, 0x20, 0x3C, 0x10, 0x00])
PING, VERSION, SCREENSHOT = 0x01, 0x02, 0x04
DATA_LIST, DATA_READ_OPEN, DATA_READ_PARTIAL, DATA_READ_CLOSE = 0x53, 0x54, 0x55, 0x56
DATA_WRITE_OPEN, DATA_WRITE_PARTIAL, DATA_WRITE_CLOSE = 0x57, 0x58, 0x59
DATA_CLEAR = 0x5C
BLOCK = 0x2000                # data store block, as elektroid


class RpcError(Exception):
    pass


def frame(seq, cmd, args=b''):
    return HEADER + pack7(struct.pack('>HHB', seq & 0xFFFF, 0, cmd) + args) + b'\xF7'


def parse(sx):
    """An RPC SysEx -> (response_to, cmd, args), or None."""
    sx = bytes(sx)
    if not sx.startswith(HEADER) or not sx.endswith(b'\xF7'):
        return None
    msg = unpack7(sx[len(HEADER):-1])
    if len(msg) < 5:
        return None
    _seq, reply_to, cmd = struct.unpack_from('>HHB', msg)
    return reply_to, cmd, msg[5:]


def cstring(text):
    return text.encode('cp1252') + b'\0'


def read_cstring(data, at):
    end = data.find(b'\0', at)
    end = len(data) if end < 0 else end
    return data[at:end].decode('cp1252', 'replace'), end + 1


class Rpc:
    def __init__(self, port):
        self.port = port
        self.seq = 0x100

    def call(self, cmd, args=b'', timeout=2.0):
        """-> the reply's arguments. Other traffic on the port is ignored."""
        self.seq = (self.seq + 1) & 0xFFFF or 1
        seq = self.seq
        self.port.send(frame(seq, cmd, args))
        found = []

        def answered(msgs):
            for m in msgs[len(found):]:
                r = parse(m)
                found.append(r[2] if r and r[0] == seq and r[1] == cmd | 0x80 else None)
            return any(f is not None for f in found)
        self.port.receive(timeout, until=answered)
        for f in found:
            if f is not None:
                return f
        raise TimeoutError('no answer to RPC 0x%02x' % cmd)

    # --- identity -------------------------------------------------------------

    def ping(self, timeout=2.0):
        """-> (product id, device name)."""
        a = self.call(PING, timeout=timeout)
        name, _ = read_cstring(a, 2 + a[1])
        return a[0], name

    def version(self, timeout=2.0):
        a = self.call(VERSION, timeout=timeout)
        _, nxt = read_cstring(a, 0)
        return read_cstring(a, nxt)[0]

    # --- screen ---------------------------------------------------------------

    def screenshot(self, timeout=2.0):
        """-> Screen. The request has no argument (Transfer adds a u32; not needed)."""
        a = self.call(SCREENSHOT, timeout=timeout)
        if len(a) < 9:
            raise RpcError('short screenshot reply')
        _ver, w, h, n = struct.unpack_from('>BHHI', a)
        if n != w * ((h + 31) // 32) * 4 or len(a) < 9 + n:
            raise RpcError('screenshot of %dx%d with %d pixel bytes' % (w, h, n))
        return Screen(w, h, a[9:9 + n])

    # --- data store (projects, sounds) ---------------------------------------

    def _ok(self, a, what):
        if not a or not a[0]:
            raise RpcError('%s refused: %s' % (what, read_cstring(a, 1)[0] if len(a) > 1 else ''))
        return a

    def data_list(self, path, timeout=5.0):
        """-> list of dicts: name, dir, and for objects id, size, ops, valid, metadata."""
        a = self._ok(self.call(DATA_LIST, cstring(path) + struct.pack('>iiB', 0, 0, 1), timeout),
                     'list ' + path)
        out, at = [], 13
        while at < len(a):
            name, at = read_cstring(a, at)
            children, kind = a[at], a[at + 1]
            at += 2
            if kind == 1:
                out.append({'name': name, 'dir': True, 'entries': struct.unpack_from('>I', a, at)[0]})
                at += 4
            elif kind == 2:
                ident, size, ops, valid, meta = struct.unpack_from('>iIHBB', a, at)
                at += 12
                out.append({'name': name, 'dir': bool(children), 'id': ident, 'size': size,
                            'ops': ops, 'valid': bool(valid), 'metadata': bool(meta)})
            else:
                raise RpcError('unknown list entry type %d in %s' % (kind, path))
        return out

    def data_read(self, path, timeout=5.0, progress=None):
        """-> the object's bytes, as Transfer stores them in a package."""
        compress = 0 if path.startswith('/samples') else 1
        a = self._ok(self.call(DATA_READ_OPEN, cstring(path) + struct.pack('>IB', BLOCK, compress),
                               timeout), 'open ' + path)
        job, = struct.unpack_from('>I', a, 1)
        data, seq = bytearray(), 0
        try:
            while True:
                a = self._ok(self.call(DATA_READ_PARTIAL, struct.pack('>II', job, seq), timeout),
                             'read ' + path)
                _job, _seq, done, last, _hash, n = struct.unpack_from('>IIIBII', a, 1)
                data += a[22:22 + n]
                seq += 1
                if progress and n:
                    progress(done / 1000)
                if last:
                    break
        finally:
            self.call(DATA_READ_CLOSE, struct.pack('>I', job), timeout)
        return bytes(data)

    def data_write(self, path, data, timeout=5.0, progress=None):
        """Replace the object at `path` with `data` (a previous data_read)."""
        a = self._ok(self.call(DATA_WRITE_OPEN, struct.pack('>I', len(data)) + cstring(path), timeout),
                     'open for write ' + path)
        job, = struct.unpack_from('>I', a, 1)
        for seq, at in enumerate(range(0, len(data), BLOCK)):
            block = data[at:at + BLOCK]
            a = self._ok(self.call(DATA_WRITE_PARTIAL, struct.pack(
                '>IIII', job, seq, zlib.crc32(block, 0xFFFFFFFF) & 0xFFFFFFFF, len(block)) + block,
                timeout), 'write ' + path)
            total, = struct.unpack_from('>I', a, 9)
            if total != at + len(block):
                raise RpcError('device took %d bytes, %d sent' % (total, at + len(block)))
            if progress:
                progress((at + len(block)) / len(data))
        a = self._ok(self.call(DATA_WRITE_CLOSE, struct.pack('>II', job, len(data)), timeout),
                     'close ' + path)
        size, = struct.unpack_from('>I', a, 5)
        if size != len(data):
            raise RpcError('device stored %d bytes of %d' % (size, len(data)))


    def data_clear(self, path, timeout=5.0):
        """Empty the slot at `path` (DataClear: the path, reply u8 status)."""
        self._ok(self.call(DATA_CLEAR, cstring(path), timeout), 'clear ' + path)


class Screen:
    """The 1-bit framebuffer: column-major big-endian u32 words, row 0 at the
    least significant end of each column, a set bit is a lit pixel."""

    def __init__(self, w, h, pixels):
        self.w, self.h, self.pixels = w, h, bytes(pixels)

    def rows(self):
        stride = (self.h + 31) // 32
        cols = []
        for x in range(self.w):
            o = x * stride * 4
            cols.append(int.from_bytes(self.pixels[o:o + stride * 4], 'big'))
        shift = stride * 32 - self.h
        return [[(c >> (shift + y)) & 1 for c in cols] for y in range(self.h)]

    def lit(self):
        return sum(bin(b).count('1') for b in self.pixels)

    def png(self, scale=1):
        """-> PNG bytes, white on black, each pixel `scale` x `scale`."""
        raw = bytearray()
        for row in self.rows():
            bits = [v for v in row for _ in range(scale)]
            line = bytearray(b'\0')
            for i in range(0, len(bits), 8):
                byte = 0
                for k, v in enumerate(bits[i:i + 8]):
                    byte |= v << (7 - k)
                line.append(byte)
            raw += line * scale

        def chunk(kind, body):
            return (struct.pack('>I', len(body)) + kind + body
                    + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF))
        return (b'\x89PNG\r\n\x1a\n'
                + chunk(b'IHDR', struct.pack('>IIBBBBB', self.w * scale, self.h * scale, 1, 0, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))

    def __eq__(self, other):
        return isinstance(other, Screen) and (self.w, self.h, self.pixels) == (other.w, other.h, other.pixels)

    def __hash__(self):
        return hash(self.pixels)
