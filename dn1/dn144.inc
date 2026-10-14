| SPDX-License-Identifier: GPL-2.0-or-later
| Digitone mk1 MAIN OS 1.44: the stock routines and data of dn143.inc, found
| again in 1.44. The routines by their code (most of them 0x274 further on,
| the mutex calls and the task switch where they were); the data through the
| code that reads it (the RAM 0x1000 further on, the heap's control block and
| the font 0x400, the SysEx router's table 0x3d0).
        | ---- stock routines and data (MAIN OS 1.44) ----------------------
        .equ OP_NEW,      0x400e96c0    | operator new(size) -> d0
        .equ ITEM_CTOR,   0x400dfd9c    | MenuItem(this, label, select, draw, change, id, step)
        .equ MENU_ADD,    0x400df5d2    | Menu::addItem(menu, item)
        .equ FN_MGR,      0x40165bb0    | std::function manager, a 4-byte function pointer
        .equ STR_CTOR,    0x4018afc8    | std::string(this, const char*, alloc&)
        .equ INVALIDATE,  0x400e539e    | View::invalidate(view)
        .equ BLIT,        0x400de4c0    | blit(dst, src, x, y, centre)
        .equ CHECKBOXES,  0x419ee988    | -> Bitmap[2], 0x1c each: empty, ticked
        .equ DRAWALL,     0x400e5f0e    | ViewController::drawAll(ctrl, Bitmap&)
        .equ FILLRECT,    0x400dd506    | (bmp, x0, y0, x1, y1, colour)
        .equ FRAMERECT,   0x400dd2ea    | (bmp, x0, y0, x1, y1, colour), outline
        .equ TEXTF,       0x400de0dc    | (bmp, rawfont, x, y, maxlen, fmt, ...)
        .equ FONT5,       0x402319c8    | the stock 5-px font
        .equ LOCK,        0x40001a4c    | mutex lock(m)
        .equ UNLOCK,      0x40001b6e    | mutex unlock(m)
        .equ HEAPMTX,     0x41993de8    | the heap's mutex
        .equ HEAPCB,      0x40240058    | buddy allocator control block
        .equ SYSEX_SEND,  0x400ee5c8    | bool (buf, len, abort*, port); 2 = USB
        .equ DTMR0,       0xFC070000    | DMA timer 0 mode
        .equ DTCN0,       0xFC07000C    | DMA timer 0 counter, bus clock
        .equ PPMSR0,      0xFC04002D    | peripheral clock set register
        .equ CUR_TCB,     0x429F2188    | the running task's TCB
        .equ IDLE_TCB,    0x4137465C    | the idle task (the init task, parked)
        .equ IDLE_PC,     0x4008D3EC    | where it spins: bra.b *
        .equ VBR_FN,      0x40001df4    | the call 0x40000538 made
        .equ SYSEX_A3,    0x401ce1ea    | the SysEx router's table (the lea hook_sysex replaces)
        | No sample engine, so no SMPFREE: SYSTEM INFO shows RAM alone.
