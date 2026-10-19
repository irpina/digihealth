| SPDX-License-Identifier: GPL-2.0-or-later
        .section .run, "ax"
        .globl rk_now
rk_now:
        move.l  KEY_CLOCK, %d0
        rts
