# SPDX-License-Identifier: GPL-2.0-or-later
"""A tiny SysEx client over the Windows MIDI API (winmm, through ctypes: no
packages). Opens the Digitakt's USB MIDI ports, sends one SysEx message and
collects whatever SysEx comes back for a while."""
import ctypes
import time
from ctypes import wintypes

winmm = ctypes.WinDLL('winmm')
MHDR_DONE = 0x1
CALLBACK_NULL = 0


class MIDIHDR(ctypes.Structure):
    pass


MIDIHDR._fields_ = [('lpData', ctypes.c_void_p), ('dwBufferLength', wintypes.DWORD),
                    ('dwBytesRecorded', wintypes.DWORD), ('dwUser', ctypes.c_void_p),
                    ('dwFlags', wintypes.DWORD), ('lpNext', ctypes.POINTER(MIDIHDR)),
                    ('reserved', ctypes.c_void_p), ('dwOffset', wintypes.DWORD),
                    ('dwReserved', ctypes.c_void_p * 8)]


def _names(count, caps_fn, struct):
    out = []
    for i in range(count()):
        c = struct()
        caps_fn(i, ctypes.byref(c), ctypes.sizeof(c))
        out.append(c.szPname)
    return out


class _InCaps(ctypes.Structure):
    _fields_ = [('wMid', wintypes.WORD), ('wPid', wintypes.WORD),
                ('vDriverVersion', wintypes.UINT), ('szPname', wintypes.WCHAR * 32),
                ('dwSupport', wintypes.DWORD)]


class _OutCaps(ctypes.Structure):
    _fields_ = [('wMid', wintypes.WORD), ('wPid', wintypes.WORD),
                ('vDriverVersion', wintypes.UINT), ('szPname', wintypes.WCHAR * 32),
                ('wTechnology', wintypes.WORD), ('wVoices', wintypes.WORD),
                ('wNotes', wintypes.WORD), ('wChannelMask', wintypes.WORD),
                ('dwSupport', wintypes.DWORD)]


def find(name='Digitakt'):
    ins = _names(winmm.midiInGetNumDevs, winmm.midiInGetDevCapsW, _InCaps)
    outs = _names(winmm.midiOutGetNumDevs, winmm.midiOutGetDevCapsW, _OutCaps)
    i = [k for k, n in enumerate(ins) if name in n]
    o = [k for k, n in enumerate(outs) if name in n]
    if not i or not o:
        raise OSError('no %s MIDI port (ports: %s). Is it connected over USB and '
                      'switched on, with USB CONFIG set to USB MIDI or Overbridge?'
                      % (name, ', '.join(sorted(set(ins + outs))) or 'none'))
    return i[0], o[0]


class Port:
    def __init__(self, name='Digitakt', nbuf=16, size=65536):
        i, o = find(name)
        self.hin, self.hout = wintypes.HANDLE(), wintypes.HANDLE()
        r = winmm.midiInOpen(ctypes.byref(self.hin), i, 0, 0, CALLBACK_NULL)
        if r:
            raise OSError('midiInOpen failed: %d (is another program using it?)' % r)
        r = winmm.midiOutOpen(ctypes.byref(self.hout), o, 0, 0, CALLBACK_NULL)
        if r:
            winmm.midiInClose(self.hin)
            raise OSError('midiOutOpen failed: %d' % r)
        self.bufs = []
        for _ in range(nbuf):
            mem = ctypes.create_string_buffer(size)
            h = MIDIHDR(lpData=ctypes.cast(mem, ctypes.c_void_p), dwBufferLength=size)
            winmm.midiInPrepareHeader(self.hin, ctypes.byref(h), ctypes.sizeof(h))
            winmm.midiInAddBuffer(self.hin, ctypes.byref(h), ctypes.sizeof(h))
            self.bufs.append((h, mem))
        winmm.midiInStart(self.hin)

    def send(self, data):
        mem = ctypes.create_string_buffer(bytes(data), len(data))
        h = MIDIHDR(lpData=ctypes.cast(mem, ctypes.c_void_p), dwBufferLength=len(data))
        winmm.midiOutPrepareHeader(self.hout, ctypes.byref(h), ctypes.sizeof(h))
        r = winmm.midiOutLongMsg(self.hout, ctypes.byref(h), ctypes.sizeof(h))
        if r:
            raise OSError('midiOutLongMsg failed: %d' % r)
        t = time.time()
        while not h.dwFlags & MHDR_DONE and time.time() - t < 5:
            time.sleep(0.001)
        winmm.midiOutUnprepareHeader(self.hout, ctypes.byref(h), ctypes.sizeof(h))

    def receive(self, seconds=1.0, until=None):
        """-> list of SysEx messages received within `seconds` (stops early
        once `until(msgs)` is true)."""
        msgs, end = [], time.time() + seconds
        while time.time() < end:
            for h, mem in self.bufs:
                if h.dwFlags & MHDR_DONE:
                    n = h.dwBytesRecorded
                    if n:
                        msgs.append(bytes(mem.raw[:n]))
                    h.dwBytesRecorded = 0
                    h.dwFlags &= ~MHDR_DONE
                    winmm.midiInAddBuffer(self.hin, ctypes.byref(h), ctypes.sizeof(h))
            if until and until(msgs):
                break
            time.sleep(0.002)
        return msgs

    def close(self):
        winmm.midiInStop(self.hin)
        winmm.midiInReset(self.hin)
        for h, _mem in self.bufs:
            winmm.midiInUnprepareHeader(self.hin, ctypes.byref(h), ctypes.sizeof(h))
        winmm.midiInClose(self.hin)
        winmm.midiOutClose(self.hout)


if __name__ == '__main__':
    p = Port()
    try:
        p.send(bytes.fromhex('F07E7F0601F7'))       # universal Identity Request
        for m in p.receive(1.5):
            print(m.hex(' '))
    finally:
        p.close()
