/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "../../../core_candidate.h"

/* Emulator-only mailbox: pending (1=set, 2=clear), group, mask, result. */
volatile unsigned rk_probe[4];
volatile unsigned rk_key_events[4];

int rk_key(void *brain, void *event)
{
    const unsigned *words = event;
    (void)brain;
    rk_key_events[0] = words[3];
    rk_key_events[1] = words[4];
    ++rk_key_events[2];
    return rk_key_events[3] && words[3] == 20;
}

void rk_tick(void *ctrl)
{
    (void)ctrl;
    if (rk_probe[0] == 1) {
        rk_probe[3] = core_keys_set(rk_probe[1], rk_probe[2]);
        rk_probe[0] = 0;
    } else if (rk_probe[0] == 2) {
        rk_probe[3] = core_keys_clear();
        rk_probe[0] = 0;
    }
}
