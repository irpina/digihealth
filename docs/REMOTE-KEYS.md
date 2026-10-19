# Bounded key protocol, revision 2

Digitakt mk1 OS 1.53/1.54 and desktop Digitone mk1 OS 1.44. All messages use
the existing DigiHealth USB-only SysEx channel; HELLO/STATS/PEEK and diagnostic
revision 1 are unchanged.
DIN source masks 8 and 16 are refused; USB source masks 2 and 4 are accepted.
This is ownership and duplicate suppression, not authentication. On the
Digitakt, the SETTINGS row REMOTE KEYS (on at power-on) is the unit's own
switch (the Digitone has no row yet; see the README): off,
every key command, CAPABILITIES included, is answered with status 6 and no
payload, and the next UI tick cancels the session, letting go of what it
holds as CANCEL does. Diagnostics answer either way.

```
request F0 00 20 3C 7D 00 <pack7: u16 sequence, u8 command, arguments> F7
reply   F0 00 20 3C 7D 00 <pack7: u16 sequence, u8 command|0x80, u8 status, payload> F7
```

Integers are big-endian. Packing places the high bits of up to seven bytes
in header bits 6 through 0, followed by their low seven bits. Every encoded
byte must be below 128; unused header bits must be zero. Key commands require
an exact decoded length, refusing truncated, oversized and noncanonical input.

| Command | Arguments | Successful reply payload |
|---|---|---|
| `0x10 CAPABILITIES` | none | `u16 revision, u8 product, u8 OS_major, u8 OS_minor, u64 keys, u16 default_tap_ms, u16 max_tap_ms, u16 max_hold_ms` |
| `0x11 OPEN` | `u32 session` | report |
| `0x12 ACTION` | `u32 session, u32 operation, u8 action, u8 key, u16 duration_ms` | report |
| `0x13 STATUS` | `u32 session, u32 operation` | report |
| `0x14 CANCEL` | `u32 session` | report |

Capabilities: revision 2, product 1 (Digitakt mk1, OS 1.53/1.54) or 2
(desktop Digitone mk1, OS 1.44). The 64-bit bitmap marks logical key ID minus
one, with 48 bits set on Digitakt and 54 on Digitone. Default tap is 100 ms,
maximum tap 250 ms and maximum press lease 2000 ms. The complete payload is
19 bytes (`>HBBBQHHH`). The client validates revision, product/OS and bitmap
before OPEN, then refuses absent names before ACTION. A Digitone Keys panel
is refused by the firmware using its board flag.

Revision 1 used smaller capability and ownership masks. Its clients reject
the revision-2 capability reply before acting; revision-2 clients likewise
refuse old helpers. ACTION arguments and FUNC/SRC IDs 1/2 are preserved.
Diagnostics framing, HELLO/STATS/PEEK and diagnostics revision 1 are unchanged.

A report is `u32 session, u32 operation, u8 state, u8 reason, u64 owned_keys,
u8 cleanup_pending` (19 bytes, `>IIBBQB`). States: 0 none, 1 accepted, 2 completed,
3 cancelled, 4 expired, 5 failed. Reasons and envelope statuses: 0 OK,
1 invalid/refused, 2 unknown-command, 3 busy, 4 stale session/operation,
5 physical overlap, 6 remote keys off. Failed envelopes contain no payload. `owned_keys`
uses the logical capability mask. `cleanup_pending` is 0 or 1.

Actions: 1 PRESS (1..2000 ms lease), 2 RELEASE (duration zero), 3 TAP
(1..250 ms). The shared logical IDs and device maps are defined once in
`keys.def`, consumed by both C and Python. See [the button table](BUTTONS.md).
The stock driver generates UI events and modifier/release suppression. No
gesture or menu policy is encoded. The bridge supports groups 0..6; valid
positions differ by device. Unsupported keys have no map and are refused.

Sessions must be nonzero. One operation can be pending, with at most one
persistent hold and a tap of another key, including across groups. Operations
start at 1 and increase by one without wrapping. Duplicate IDs with identical payloads
return the retained latest state; conflicting duplicates and older/skipped
IDs are refused. A repeated PRESS never renews an existing hold's lease,
even with a fresh operation ID. OPEN of the current session never resets
its deadline/history. A different session may open only when no operation
or cleanup is pending and no synthetic key remains owned.

STATUS operation zero queries the latest report, including before the first
action. Other operation IDs must match the latest accepted operation. A newer
operation replaces that report. CANCEL is idempotent, closes the session after
cleanup and remains available during pending work. A closed session permits
status, duplicate-result queries and cancel, but no new actions or reopen.

The MIDI receiver validates and queues work under a short interrupt mask;
only the UI tick invokes the input bridge. Accepted means queued. Completed
means the driver transition was delivered (including the release of a tap),
not that a view handled it. A lost ACTION reply is resolved with STATUS for
the same session/operation, never a new action. Unknown outcomes after reset
must not be replayed automatically. There is no exactly-once guarantee across
reset. A lost CANCEL reply is resolved through STATUS operation zero; if its
delivery cannot be confirmed, the client reports that instead of success.

The bridge merges physical and synthetic group masks. Newly taking ownership
of a physically held key is refused. A subsequent physical takeover survives
synthetic release/cancel. Queue backpressure leaves ownership intact and is
retried by the UI tick. Cleanup records each released group immediately;
STATUS retains only the ownership still awaiting a busy release. A queued
action that cannot start within 2000 ms expires. A hold's lease starts on the
actual driver press. A tap's dwell also
starts on its actual press; release retries while the queue is busy.

DTIM0 runs at the 132 MHz bus clock, with unsigned subtraction across normal
counter wrap. Deadlines are checked only when the UI task runs. A stalled UI
can delay cleanup; stalls longer than the approximately 32.5-second counter
wrap can cause further delay. Queue backpressure can also postpone a release.
The client cancels in its context manager; disconnects rely on firmware leases.

The panel bridge remains a private core candidate in DigiHealth. Released
Elekloader core exports are unchanged.
