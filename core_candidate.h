/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CORE_KEYS_CANDIDATE_H
#define CORE_KEYS_CANDIDATE_H

enum { CORE_KEYS_GROUPS = 7 };
enum { CORE_KEYS_OK, CORE_KEYS_INVALID, CORE_KEYS_BUSY, CORE_KEYS_OVERLAP };

/* Private candidate interface; not part of the released core ABI. */
int core_keys_set(unsigned group, unsigned mask);
int core_keys_clear(void);
void core_panel_buttons(unsigned group, unsigned physical_mask);

#endif
