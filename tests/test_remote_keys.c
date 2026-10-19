/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <stdio.h>

volatile unsigned char fw_key_state[7];
volatile unsigned fw_ui_queue[8];
static unsigned locked, calls, last_group, last_mask;

unsigned core_keys_lock(void)
{
    assert(!locked);
    locked = 1;
    return 0x2000;
}

void core_keys_unlock(unsigned sr)
{
    assert(locked && sr == 0x2000);
    locked = 0;
}

void core_stock_buttons(unsigned group, unsigned mask)
{
    assert(locked);
    ++calls;
    last_group = group;
    last_mask = mask;
    fw_key_state[group] = mask;
}

#include "../core_candidate.c"

int main(void)
{
    unsigned before;
    fw_ui_queue[4] = 255;
    fw_key_state[2] = 0x10;  /* A physical key is already down at startup. */
    assert(core_keys_set(2, 2) == CORE_KEYS_OK && last_mask == 0x12);
    assert(core_keys_set(2, 10) == CORE_KEYS_OK && last_mask == 0x1a);
    assert(core_keys_set(2, 2) == CORE_KEYS_OK && last_mask == 0x12);
    assert(core_keys_clear() == CORE_KEYS_OK && last_mask == 0x10);
    assert(last_group == 2 && physical[2] == 0x10 && synthetic[2] == 0);

    assert(core_keys_set(2, 0x10) == CORE_KEYS_OVERLAP);
    assert(synthetic[2] == 0 && fw_key_state[2] == 0x10);
    core_panel_buttons(2, 0);
    assert(core_keys_set(2, 2) == CORE_KEYS_OK);
    before = calls;
    assert(core_keys_set(2, 2) == CORE_KEYS_OK && calls == before);

    core_panel_buttons(2, 0x12);  /* The person takes over FUNC. */
    assert(last_mask == 0x12);
    before = calls;
    assert(core_keys_clear() == CORE_KEYS_OK && calls == before);
    assert(synthetic[2] == 0 && fw_key_state[2] == 0x12);
    core_panel_buttons(2, 0);
    assert(fw_key_state[2] == 0);

    fw_ui_queue[1] = 113;
    before = calls;
    assert(core_keys_set(2, 2) == CORE_KEYS_BUSY && calls == before);
    assert(synthetic[2] == 0);
    fw_ui_queue[1] = 112;
    assert(core_keys_set(2, 2) == CORE_KEYS_OK);
    fw_ui_queue[1] = 113;
    assert(core_keys_clear() == CORE_KEYS_BUSY && synthetic[2] == 2);
    fw_ui_queue[1] = 0;
    assert(core_keys_clear() == CORE_KEYS_OK);
    fw_ui_queue[4] = 31;
    assert(core_keys_set(2, 2) == CORE_KEYS_OK);
    fw_ui_queue[1] = 25;
    assert(core_keys_clear() == CORE_KEYS_BUSY && synthetic[2] == 2);
    fw_ui_queue[1] = 0;
    assert(core_keys_clear() == CORE_KEYS_OK && fw_key_state[2] == 0);

    before = calls;
    assert(core_keys_set(7, 1) == CORE_KEYS_INVALID);
    assert(core_keys_set(2, 256) == CORE_KEYS_INVALID);
    assert(calls == before && !locked);
    puts("remote key state tests passed");
    return 0;
}
