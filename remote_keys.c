/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "remote_keys.h"
#include "core_candidate.h"

#ifndef RK_OS_MINOR
#error "RK_OS_MINOR must identify the OS port"
#endif
#ifndef RK_PRODUCT
#error "RK_PRODUCT must identify the device"
#endif

static const struct key_position { unsigned char ui, group, mask; } keymap[RK_KEY_COUNT + 1] = {
#if RK_PRODUCT == 1
#define KEY(id, name, dc, dg, dm, nc, ng, nm) [id] = {dc, dg, dm},
#elif RK_PRODUCT == 2
#define KEY(id, name, dc, dg, dm, nc, ng, nm) [id] = {nc, ng, nm},
#else
#error "Unknown key map"
#endif
#include "keys.def"
#undef KEY
};
#if RK_PRODUCT == 2
extern volatile unsigned fw_panel_variant;
#endif

/* The REMOTE KEYS row (sysinfo.s): nonzero when the user turned it off. */
extern volatile unsigned char rk_off;

extern unsigned rk_now(void);
extern unsigned core_keys_lock(void);
extern void core_keys_unlock(unsigned sr);

enum { TICKS_PER_MS = 132000, MAX_HOLD_MS = 2000, MAX_TAP_MS = 250 };
static unsigned session, operation, action, key, duration, state, reason;
static unsigned char masks[CORE_KEYS_GROUPS];
static unsigned held_key, held_start, held_ticks, tap_start;
static unsigned started, tapping, cleanup, closed;

static unsigned get32(const unsigned char *p)
{
    return (unsigned)p[0] << 24 | (unsigned)p[1] << 16 | (unsigned)p[2] << 8 | p[3];
}

static void put32(unsigned char *p, unsigned value)
{
    p[0] = value >> 24; p[1] = value >> 16; p[2] = value >> 8; p[3] = value;
}

static void put16(unsigned char *p, unsigned value)
{
    p[0] = value >> 8; p[1] = value;
}

static unsigned owned(void)
{
    unsigned i;
    for (i = 0; i < CORE_KEYS_GROUPS; ++i)
        if (masks[i]) return 1;
    return 0;
}

static void bitmap(unsigned char *p, unsigned supported)
{
    unsigned i;
    for (i = 0; i < 8; ++i) p[i] = 0;
    for (i = 1; i <= RK_KEY_COUNT; ++i)
        if (keymap[i].mask && (supported || (masks[keymap[i].group] & keymap[i].mask)))
            p[7 - (i - 1) / 8] |= 1u << ((i - 1) % 8);
}

/* Exact canonical packing prevents an oversized request becoming a valid prefix. */
static int unpack(const unsigned char *src, unsigned length, unsigned char *dst, unsigned count)
{
    unsigned at = 0, out = 0, n, i, header;
    if (length != count + (count + 6) / 7)
        return 0;
    while (out < count) {
        n = count - out;
        if (n > 7) n = 7;
        header = src[at++];
        if (header & 0x80 || header & ((1u << (7 - n)) - 1))
            return 0;
        for (i = 0; i < n; ++i) {
            if (src[at] & 0x80) return 0;
            dst[out++] = src[at++] | ((header << (i + 1)) & 0x80);
        }
    }
    return 1;
}

static int report(unsigned char *reply)
{
    put32(reply + 4, session);
    put32(reply + 8, operation);
    reply[12] = state;
    reply[13] = reason;
    bitmap(reply + 14, 0);
    reply[22] = !!cleanup;
    return 23;
}

int rk_receive(const unsigned char *packed, unsigned length, unsigned char *reply)
{
    unsigned char request[16];
    unsigned command, count, owner, number, a, k, ms, sr;
    int result = 4;
    if (length < 4) return 0;
    command = packed[3] | ((packed[0] << 3) & 0x80);
    if (command < RK_CAPS || command > RK_CANCEL) return 0;
    reply[0] = packed[1] | ((packed[0] << 1) & 0x80);
    reply[1] = packed[2] | ((packed[0] << 2) & 0x80);
    reply[2] = command | 0x80;
    reply[3] = RK_INVALID;
    count = command == RK_CAPS ? 3 : command == RK_ACTION ? 15 : command == RK_STATUS ? 11 : 7;
    if (!unpack(packed, length, request, count)) return 4;
    reply[3] = RK_OK;
#if RK_PRODUCT == 2
    if (fw_panel_variant & 0x80000) {
        reply[3] = RK_INVALID;
        return 4;
    }
#endif
    if (rk_off) {
        reply[3] = RK_OFF;
        return 4;
    }
    if (command == RK_CAPS) {
        put16(reply + 4, 2);
        reply[6] = RK_PRODUCT; reply[7] = 1; reply[8] = RK_OS_MINOR;
        bitmap(reply + 9, 1);
        put16(reply + 17, 100);
        put16(reply + 19, MAX_TAP_MS);
        put16(reply + 21, MAX_HOLD_MS);
        return 23;
    }
    owner = get32(request + 3);
    sr = core_keys_lock();
    if (!owner) {
        reply[3] = RK_INVALID;
    } else if (command == RK_OPEN) {
        if (owner == session) {
            if (closed) reply[3] = RK_STALE;
        } else if (owned() || cleanup || state == RK_ACCEPTED) {
            reply[3] = RK_BUSY;
        } else {
            session = owner;
            operation = action = key = duration = reason = state = closed = 0;
        }
    } else if (owner != session) {
        reply[3] = RK_STALE;
    } else if (command == RK_STATUS) {
        number = get32(request + 7);
        if (number && number != operation) reply[3] = RK_STALE;
    } else if (command == RK_CANCEL) {
        if (!closed) {
            cleanup = RK_CANCELLED;
            state = RK_ACCEPTED;
        }
    } else {
        number = get32(request + 7);
        a = request[11]; k = request[12];
        ms = (unsigned)request[13] << 8 | request[14];
        if (!number || k < 1 || k > RK_KEY_COUNT || !keymap[k].mask || a < RK_PRESS || a > RK_TAP ||
            (a == RK_RELEASE ? ms != 0 : !ms || ms > (a == RK_PRESS ? MAX_HOLD_MS : MAX_TAP_MS))) {
            reply[3] = RK_INVALID;
        } else if (number == operation) {
            if (a != action || k != key || ms != duration) reply[3] = RK_INVALID;
        } else if (closed || number < operation || operation == 0xffffffffu || number != operation + 1) {
            reply[3] = RK_STALE;
        } else if (cleanup || state == RK_ACCEPTED || (a == RK_PRESS && held_key && held_key != k)) {
            reply[3] = RK_BUSY;
        } else if (a == RK_TAP && (masks[keymap[k].group] & keymap[k].mask)) {
            reply[3] = RK_OVERLAP;
        } else {
            operation = number; action = a; key = k; duration = ms;
            state = RK_ACCEPTED; reason = RK_OK; tapping = 0;
            started = rk_now();
        }
    }
    if (reply[3] == RK_OK) result = report(reply);
    core_keys_unlock(sr);
    return result;
}

void rk_tick(void *ctrl)
{
    unsigned sr = core_keys_lock(), now = rk_now(), target, group;
    int result;
    (void)ctrl;
    /* shortcut: a >32 s UI stall can wrap DTIM0; use a wider clock if required. */
    if ((held_key && now - held_start >= held_ticks) ||
        (state == RK_ACCEPTED && !tapping && now - started >= MAX_HOLD_MS * TICKS_PER_MS))
        cleanup = RK_EXPIRED;
    /* Turned off: let go of what the session holds or is about to do. */
    if (rk_off && !cleanup && (owned() || state == RK_ACCEPTED))
        cleanup = RK_CANCELLED;
    if (cleanup) {
        for (group = 0; group < CORE_KEYS_GROUPS; ++group) {
            if (core_keys_set(group, 0) != CORE_KEYS_OK) break;
            masks[group] = 0;
        }
        if (group == CORE_KEYS_GROUPS) {
            held_key = tapping = 0;
            state = cleanup; cleanup = 0; closed = 1; reason = RK_OK;
        }
    } else if (state == RK_ACCEPTED) {
        if (tapping && now - tap_start < duration * TICKS_PER_MS) {
            core_keys_unlock(sr);
            return;
        }
        group = keymap[key].group;
        target = action == RK_RELEASE || tapping ? masks[group] & ~keymap[key].mask
                                                : masks[group] | keymap[key].mask;
        result = core_keys_set(group, target);
        if (result == CORE_KEYS_OK) {
            masks[group] = target;
            if (action == RK_PRESS) {
                if (!held_key) {
                    held_key = key; held_start = now; held_ticks = duration * TICKS_PER_MS;
                }
                state = RK_COMPLETED;
            } else if (action == RK_RELEASE) {
                if (held_key == key) held_key = 0;
                state = RK_COMPLETED;
            } else if (!tapping) {
                tapping = 1; tap_start = now;
            } else {
                tapping = 0; state = RK_COMPLETED;
            }
        } else if (result != CORE_KEYS_BUSY) {
            state = RK_FAILED;
            reason = result == CORE_KEYS_OVERLAP ? RK_OVERLAP : RK_INVALID;
        }
    }
    core_keys_unlock(sr);
}
