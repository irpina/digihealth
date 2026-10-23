# Button names and device maps

The script uses the same logical names and IDs on both desktop mk1 products.
Firmware UI IDs and panel positions differ. Panel group/bit are zero-based.
The canonical source is `keys.def`; `tools/keymap.py` reads the same table.

Digitakt OS 1.53/1.54 and Digitone OS 1.44 are supported. Digitone 1.43 keeps
diagnostics only; its key bridge is not enabled without stock-file verification.
Digitone Keys additional controls, knob rotation, master volume and power
are outside this button map; knob rotation on the Digitone is TURN, by
encoder number (A-H 1-8, LEVEL 9), not a button (see the README's Encoder turns).

| Name | Logical/USB ID | Digitakt UI ID | Digitakt group:bit | Digitone UI ID | Digitone group:bit |
|---|---:|---:|---|---:|---|
| `FUNC` | 1 | 1 | 2:1 | 1 | 0:2 |
| `SRC` | 2 | 20 | 2:3 | — | — |
| `TRK` | 3 | 2 | 2:0 | — | — |
| `MIDI` | 4 | — | — | 2 | 0:1 |
| `NOTE` | 5 | — | — | 3 | 0:0 |
| `PTN` | 6 | 3 | 4:1 | 4 | 3:0 |
| `BANK` | 7 | 4 | 4:0 | 5 | 5:0 |
| `SONG` | 8 | 5 | 4:2 | 6 | 1:0 |
| `SETTINGS` | 9 | 6 | 3:0 | 7 | 1:1 |
| `SAMPLING` | 10 | 7 | 3:1 | — | — |
| `VOICE` | 11 | — | — | 8 | 2:1 |
| `TEMPO` | 12 | 8 | 3:2 | 9 | 3:3 |
| `RECORD` | 13 | 9 | 4:3 | 10 | 2:0 |
| `PLAY` | 14 | 10 | 4:4 | 11 | 3:2 |
| `STOP` | 15 | 11 | 4:5 | 12 | 4:1 |
| `YES` | 16 | 12 | 4:6 | 13 | 3:4 |
| `NO` | 17 | 13 | 3:3 | 14 | 4:2 |
| `UP` | 18 | 14 | 4:7 | 15 | 3:5 |
| `DOWN` | 19 | 15 | 3:5 | 16 | 4:4 |
| `LEFT` | 20 | 16 | 3:4 | 17 | 4:3 |
| `RIGHT` | 21 | 17 | 3:6 | 18 | 3:6 |
| `PAGE` | 22 | 18 | 3:7 | 19 | 3:7 |
| `TRIG` | 23 | 19 | 2:2 | 20 | 2:2 |
| `SYN1` | 24 | — | — | 21 | 2:3 |
| `SYN2` | 25 | — | — | 22 | 2:4 |
| `FLTR` | 26 | 21 | 2:5 | 23 | 2:5 |
| `AMP` | 27 | 22 | 2:6 | 24 | 2:6 |
| `LFO` | 28 | 23 | 2:7 | 25 | 2:7 |
| `1` | 29 | 24 | 0:0 | 26 | 3:1 |
| `2` | 30 | 25 | 0:1 | 27 | 4:0 |
| `3` | 31 | 26 | 0:2 | 28 | 5:3 |
| `4` | 32 | 27 | 0:3 | 29 | 5:4 |
| `5` | 33 | 28 | 0:4 | 30 | 5:5 |
| `6` | 34 | 29 | 0:5 | 31 | 5:6 |
| `7` | 35 | 30 | 0:6 | 32 | 5:7 |
| `8` | 36 | 31 | 0:7 | 33 | 4:5 |
| `9` | 37 | 32 | 1:0 | 34 | 5:1 |
| `10` | 38 | 33 | 1:1 | 35 | 5:2 |
| `11` | 39 | 34 | 1:2 | 36 | 6:0 |
| `12` | 40 | 35 | 1:3 | 37 | 6:1 |
| `13` | 41 | 36 | 1:4 | 38 | 6:2 |
| `14` | 42 | 37 | 1:5 | 39 | 6:3 |
| `15` | 43 | 38 | 1:6 | 40 | 6:4 |
| `16` | 44 | 39 | 1:7 | 41 | 6:5 |
| `T1` | 45 | — | — | 42 | 4:6 |
| `T2` | 46 | — | — | 43 | 4:7 |
| `T3` | 47 | — | — | 44 | 6:6 |
| `T4` | 48 | — | — | 45 | 6:7 |
| `A_PUSH` | 49 | 40 | 5:4 | 46 | 0:4 |
| `B_PUSH` | 50 | 41 | 5:3 | 47 | 0:5 |
| `C_PUSH` | 51 | 42 | 5:6 | 48 | 0:6 |
| `D_PUSH` | 52 | 43 | 5:5 | 49 | 0:7 |
| `E_PUSH` | 53 | 44 | 5:2 | 50 | 1:4 |
| `F_PUSH` | 54 | 45 | 5:1 | 51 | 1:5 |
| `G_PUSH` | 55 | 46 | 5:0 | 52 | 1:6 |
| `H_PUSH` | 56 | 47 | 5:7 | 53 | 1:7 |
| `LEVEL_PUSH` | 57 | 48 | 6:0 | 54 | 0:3 |

`TRIG` is the parameter-page key; `1`–`16` are the numbered trig keys.
`NOTE` is the Digitone keyboard-setup button (called KEYBOARD in the emulator).
`SETTINGS` is called GLOBAL in the older runtime labels.

Mapping provenance: Digitakt tables `0x4018e89c` (1.53) and `0x4018eb9c` (1.54);
Digitone table `0x401a9084` (1.44). Firmware-backed tests assert every entry
and observe the mapped UI IDs through the compiled driver/core.
