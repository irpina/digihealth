# digihealth

A performance and diagnostics mod for the Digitakt (mk1), OS 1.53. It adds
two rows to SETTINGS:

- **FAST AUDIO** (on by default) runs the audio render's hot code from the
  processor's free on-chip SRAM instead of DDR. It is the same code, so the
  audio is identical, and the DSP load is lower. Measured on a unit, with
  the same song before and after:
  - the render went from 537 to 480 µs a block;
  - DSP load went from 80.5 to 72.0 %.
- **SYSTEM INFO** shows CPU, DSP, RAM and free sample memory in the top bar.
  A read-only USB diagnostics channel lets `tools/digiusb.py` read the same
  figures, and more, from a computer.

It is an [elekloader](https://github.com/irpina/elekloader) mod.
elekloader builds a custom OS file on your own machine, from your stock OS
file and the mods you pick; nothing from Elektron is distributed.

## FAST AUDIO

The render's hot code, 0x400716c0-0x4007629a (about 19 KB), is copied into
the SRAM, which the OS clears at boot and never uses. The copy's
references to itself are pointed at the copy, and the render's calls go
through stubs that pick the copy or the original.

- **When it switches on:** two seconds after the unit's screen comes up,
  once. Untick it in SETTINGS and it stays off until the next power-on;
  unticking isn't saved.
- **Safety nets:**
  - It refuses to start if that SRAM isn't empty.
  - Once a second it checks the copy against its checksum. If anything has
    written over it, it goes back to the original code for good.
  - FUNC at power-on and the stock OS file always recover the unit.

Why it is faster: the render runs about 17 KB of code for every block
through an 8 KB instruction cache, which costs about a thousand cache
misses a block. The SRAM answers without wait states.

## SYSTEM INFO

- **The top bar** shows two pages, two seconds each:
  - CPU and DSP load (DSP now and its peak in the last second);
  - free RAM (the heap) and free sample memory.
- **The USB channel** is SysEx with Elektron's manufacturer header and a
  device byte (0x7D) that no Elektron machine uses, so a stock OS ignores
  it. It has three commands, all read-only: HELLO, STATS (the once-a-second
  snapshot: render and idle time, the heap by block size, free sample
  memory, FAST AUDIO's state) and PEEK (DDR and the SRAM only).

```text
python tools/digiusb.py cfw          # which mod answers, and its uptime
python tools/digiusb.py stats 10     # ten readings, one a second
python tools/digiusb.py peek 0x4020bb14 64
```

`digiusb.py` runs on Windows (winmm, through ctypes; no packages). Close
Elektron Transfer first: Windows lets one program at a time open a MIDI
port.

## Install

You need three things:
- **elekloader**:
  - **Windows:** download `elekloader-<version>-windows.zip` from
    [elekloader's releases](https://github.com/irpina/elekloader/releases/latest),
    unzip it and run `elekloader.exe`. The core mod, which every linkable mod
    needs, is built in.
  - **Other systems:** run elekloader from source with Python 3.9 or newer
    (see [its README](https://github.com/irpina/elekloader#install)). There
    you also need `core-2.0a.elemod`, which is attached to this repository's
    releases too.
- **This mod:** `digihealth-1.0.elemod`, from
  [this repository's releases](https://github.com/irpina/digihealth/releases/latest).
- **The stock OS file:** `Digitakt_OS1.53.syx`, from
  [Elektron's Digitakt downloads](https://www.elektron.se/support-downloads/digitakt).
  The mod is for the Digitakt mk1 on OS 1.53 only; elekloader recognises
  the file by its hash.

Then build your OS in elekloader's window:

1. **Change stock firmware...** (top right): choose `Digitakt_OS1.53.syx`.
2. **+ Install from file...**: choose `digihealth-1.0.elemod`. From source,
   install `core-2.0a.elemod` the same way.
3. **Tick digihealth.** core is ticked with it. The check below the list should
   say "No conflicts ... Ready to build". To add other mods, such as [digislicer](https://github.com/irpina/digislicer), install and tick them as well.
4. **OS version shown**: the 4 characters the unit will show, for example
   `DH10`.
5. **BUILD FIRMWARE**, and save the `.syx`. elekloader verifies it before
   writing it.

Flash it with Elektron Transfer, as for any OS update
([Elektron's instructions](https://support.elektron.se/support/solutions/articles/43000662890-how-to-update-your-device)):
1. Connect the unit over USB.
2. In Transfer, select the unit and **Connect**.
3. Drag the `.syx` onto **Drop files here**.
4. Press **YES** on the unit.

Don't turn it off until the upgrade is done.

Or on the command line (elekloader from source):

```bash
python -m elekloader.patch --stock Digitakt_OS1.53.syx \
    --mod core-2.0a.elemod --mod digihealth-1.0.elemod \
    --out Digitakt_OS1.53-health.syx --version DH10
```

**Recovery:** elekloader never changes the bootloader, so the stock OS
file always restores the unit. Hold **FUNC** while powering on for the
startup menu, and press **TRIG 4** for OS UPGRADE. Then send the stock
`.syx` with Transfer's legacy OS upgrade mode.

## Build it from source

The Digitakt mk1 cross toolchain (m68k binutils and gcc; on Debian or
Ubuntu, `apt install binutils-m68k-linux-gnu gcc-m68k-linux-gnu`; on
Windows, inside WSL) and elekloader, importable (installed, or on
`PYTHONPATH`):

```bash
python build.py --stock Digitakt_OS1.53.syx      # -> out/digihealth-1.0.elemod
python -m elekloader.lint out/digihealth-1.0.elemod --stock Digitakt_OS1.53.syx --with core-2.0a.elemod
```

`build.py`, not `elekloader.sdk.build` alone. FAST AUDIO needs parts
worked out from your stock file:
- the checks that the code block can run from the SRAM;
- its fix-ups;
- the render's call-site stubs.

`build.py` computes them with elekloader's ColdFire decoder, then hands
them to elekloader's SDK.

| file | |
|---|---|
| `mod.json` | the mod: its sites, handlers, tables and resources; `fast_audio` is `build.py`'s input |
| `build.py` | FAST AUDIO's plan from the stock file, then the SDK |
| `fastaudio.s` | the FAST AUDIO row, the copy, the stubs' switch and the watchdog |
| `sysinfo.s` | the SYSTEM INFO row and readout, the render and idle timing, the USB channel |
| `os153.inc` | the stock routines it calls |
| `tools/digiusb.py`, `tools/winmidi.py` | the USB channel's other end |

## How it was checked

These checks ran in digikit's emulator, which runs the stock OS and the
mods through the real bootloader.

- **Bit-exact audio:** with FAST AUDIO on, every output sample and the audio
  engine's whole state after every render are identical to stock's. Four
  scenarios: a sample-heavy pattern, a trig-heavy pattern, all eight tracks
  busy, and parameter changes while playing (6,000-7,500 renders each).
  This holds for digihealth alone and combined with digislicer.
- **A cold boot against stock** (35.6 s): FAST AUDIO comes on by itself,
  then the row is unticked and ticked again, with playing in between.
  - Every sample of the audio is identical to stock's. The recording stops
    one 1 ms block earlier: the hook bus changes the UI task's timing by a
    few instructions, and `core` alone does the same.
  - The screens are those of the custom build the mod comes from, to the
    pixel.
- **On a unit:** FAST AUDIO and SYSTEM INFO ran on a Digitakt mk1 in the
  custom builds this mod comes from, where the figures above were
  measured.

## Licence

GPL-2.0: see [LICENSE](LICENSE). Not affiliated with Elektron. Digitakt is
a trademark of Elektron. Custom firmware is at your own risk.
