/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef REMOTE_KEYS_H
#define REMOTE_KEYS_H

enum { RK_CAPS = 0x10, RK_OPEN, RK_ACTION, RK_STATUS, RK_CANCEL };
enum { RK_PRESS = 1, RK_RELEASE, RK_TAP, RK_TURN };
enum { RK_NONE, RK_ACCEPTED, RK_COMPLETED, RK_CANCELLED, RK_EXPIRED, RK_FAILED };
enum { RK_OK, RK_INVALID, RK_UNKNOWN, RK_BUSY, RK_STALE, RK_OVERLAP, RK_OFF };
enum { RK_KEY_COUNT = 57 };
/* Revision 3 adds TURN, encoder turns, and the encoder count to CAPS. A port
 * with the OS's encoder input (ENC_INPUT) sets RK_ENCODERS; others have none. */
#ifndef RK_ENCODERS
#define RK_ENCODERS 0
#endif
enum { RK_REVISION = 3, RK_MAX_TURNS = 30, RK_MAX_STEP = 30 };

int rk_receive(const unsigned char *packed, unsigned length, unsigned char *reply);
void rk_tick(void *ctrl);

#endif
