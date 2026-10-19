/* SPDX-License-Identifier: GPL-2.0-or-later */
volatile unsigned key_seen[64], key_consume;

int key_observe(void *brain, void *event)
{
    const unsigned *words = event;
    (void)brain;
    if (words[3] < 64) ++key_seen[words[3]];
    return key_consume;
}
