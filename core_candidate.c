/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "core_candidate.h"

extern volatile unsigned char fw_key_state[];
extern volatile unsigned fw_ui_queue[];
extern unsigned core_keys_lock(void);
extern void core_keys_unlock(unsigned saved_sr);
extern void core_stock_buttons(unsigned group, unsigned mask);
#ifndef RK_ENCODERS
#define RK_ENCODERS 0
#endif

static unsigned char physical[CORE_KEYS_GROUPS], synthetic[CORE_KEYS_GROUPS];
static unsigned initialized;

/* Interrupts stay masked through each driver call: its repeat state and
 * record pool are shared with the panel receiver and repeat producer. */
static void initialize(void)
{
    unsigned i;
    if (!initialized) {
        for (i = 0; i < CORE_KEYS_GROUPS; ++i)
            physical[i] = fw_key_state[i];
        initialized = 1;
    }
}

void core_panel_buttons(unsigned group, unsigned mask)
{
    unsigned sr = core_keys_lock();
    initialize();
    if (group < CORE_KEYS_GROUPS) {
        physical[group] = mask;
        mask |= synthetic[group];
    }
    core_stock_buttons(group, mask);
    core_keys_unlock(sr);
}

int core_keys_set(unsigned group, unsigned mask)
{
    unsigned sr, old_mask, new_mask, count, capacity;
    int result = CORE_KEYS_OK;
    if (group >= CORE_KEYS_GROUPS || mask > 255)
        return CORE_KEYS_INVALID;
    sr = core_keys_lock();
    initialize();
    old_mask = physical[group] | synthetic[group];
    new_mask = physical[group] | mask;
    if (mask & ~synthetic[group] & physical[group]) {
        result = CORE_KEYS_OVERLAP;
    } else if (old_mask != new_mask) {
        count = fw_ui_queue[1];
        capacity = fw_ui_queue[4] + 1;
        /* Reserve eight queue slots and stay below the driver's 128-record
         * key pool: queue_send stores pointers and checks neither limit. */
        if (capacity < 16 || count > capacity - 16 || count > 112)
            result = CORE_KEYS_BUSY;
        else
            core_stock_buttons(group, new_mask);
    }
    if (result == CORE_KEYS_OK)
        synthetic[group] = mask;
    core_keys_unlock(sr);
    return result;
}

#if RK_ENCODERS
/* The panel's encoder input (core_candidate.s): one turn message, encoder
 * 0-7 = A-H, 8 = LEVEL, delta in counts as the panel sends them. */
extern void fw_encoder(unsigned encoder, int delta);
#endif

/* One encoder turn, as if the panel had sent it: the OS adds it to that
 * encoder's count and turns the counts into UI events itself. Refused while
 * the UI's event queue is as full as core_keys_set refuses it. */
int core_encoder_turn(unsigned encoder, int delta)
{
#if RK_ENCODERS
    unsigned sr, count, capacity;
    int result = CORE_KEYS_OK;
    if (encoder >= RK_ENCODERS || delta == 0 || delta < -127 || delta > 127)
        return CORE_KEYS_INVALID;
    sr = core_keys_lock();
    count = fw_ui_queue[1];
    capacity = fw_ui_queue[4] + 1;
    if (capacity < 16 || count > capacity - 16 || count > 112)
        result = CORE_KEYS_BUSY;
    else
        fw_encoder(encoder, delta);
    core_keys_unlock(sr);
    return result;
#else
    (void)encoder; (void)delta;
    return CORE_KEYS_INVALID;
#endif
}

int core_keys_clear(void)
{
    unsigned group;
    int result;
    for (group = 0; group < CORE_KEYS_GROUPS; ++group) {
        result = core_keys_set(group, 0);
        if (result != CORE_KEYS_OK)
            return result;
    }
    return CORE_KEYS_OK;
}
