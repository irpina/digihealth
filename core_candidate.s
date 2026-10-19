| SPDX-License-Identifier: GPL-2.0-or-later
        .section .run, "ax"
        .globl core_keys_lock, core_keys_unlock, core_stock_buttons
        .globl fw_key_state, fw_ui_queue
        .set fw_key_state, KEY_STATE
        .set fw_ui_queue, UI_QUEUE
        .ifndef KEY_STACK
        .equ KEY_STACK, 68
        .endif
        .ifdef KEY_VARIANT
        .globl fw_panel_variant
        .set fw_panel_variant, KEY_VARIANT
        .endif

core_keys_lock:
        moveq   #0, %d0
        move.w  %sr, %d0
        move.w  #0x2700, %sr
        rts

core_keys_unlock:
        move.l  4(%sp), %d0
        move.w  %d0, %sr
        rts

| Reproduce only the displaced prologue, then use the original driver.
core_stock_buttons:
        lea     -KEY_STACK(%sp), %sp
        movem.l %d2-%d7/%a2-%a6, (%sp)
        jmp     KEY_CONTINUE
