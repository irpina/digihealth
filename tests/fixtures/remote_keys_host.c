/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <string.h>

volatile unsigned char fw_key_state[7];
volatile unsigned fw_ui_queue[8];
volatile unsigned fw_panel_variant;
volatile unsigned char rk_off;
static unsigned clock_ticks, depth, calls;
static unsigned fill_after_group;

unsigned core_keys_lock(void) { return depth++; }
void core_keys_unlock(unsigned sr) { assert(depth && --depth == sr); }
void core_stock_buttons(unsigned group, unsigned mask)
{
    assert(depth);
    ++calls;
    fw_key_state[group] = mask;
    if (!mask && group == fill_after_group) {
        fw_ui_queue[1] = 113;
        fill_after_group = 99;
    }
}
unsigned rk_now(void) { return clock_ticks; }

#ifndef RK_OS_MINOR
#define RK_OS_MINOR 53
#endif
#ifndef RK_PRODUCT
#define RK_PRODUCT 1
#endif
#include "../../core_candidate.c"
#include "../../remote_keys.c"

void host_reset(void)
{
    assert(!depth);
    memset((void *)fw_key_state, 0, sizeof(fw_key_state));
    memset((void *)fw_ui_queue, 0, sizeof(fw_ui_queue));
    memset(physical, 0, sizeof(physical));
    memset(synthetic, 0, sizeof(synthetic));
    fw_ui_queue[4] = 255;
    initialized = clock_ticks = calls = 0;
    fill_after_group = 99;
    session = operation = action = key = duration = state = reason = 0;
    memset(masks, 0, sizeof(masks));
    fw_panel_variant = 0;
    rk_off = 0;
    held_key = held_start = held_ticks = tap_start = 0;
    started = tapping = cleanup = closed = 0;
}
void host_advance(unsigned ms) { clock_ticks += ms * TICKS_PER_MS; rk_tick(0); }
void host_clock(unsigned ticks) { clock_ticks = ticks; }
void host_physical(unsigned mask) { core_panel_buttons(2, mask); }
void host_busy(unsigned busy) { fw_ui_queue[1] = busy ? 113 : 0; }
unsigned host_mask(void) { return fw_key_state[2]; }
unsigned host_calls(void) { return calls; }
void host_physical_group(unsigned group, unsigned mask) { core_panel_buttons(group, mask); }
unsigned host_group(unsigned group) { return fw_key_state[group]; }
void host_variant(unsigned flags) { fw_panel_variant = flags; }
void host_fill_after(unsigned group) { fill_after_group = group; }
void host_off(unsigned off) { rk_off = off; }
