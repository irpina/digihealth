# Remote keys probe

An experimental Digitakt mk1 input bridge for OS 1.53 and 1.54, exercised
through an emulator mailbox. It has no USB receiver and performs no key
actions on its own.

The root-level `core_candidate.c` and `core_candidate.s` keep physical and synthetic held
state separate. They send their union through the original panel group
handler, preserving the firmware's modifier, repeat and release behavior
and the core's normal key interception. The candidate is private to this
example; the released core and SDK exports are unchanged.

`core_keys_set(group, mask)` sets the synthetic mask for one of the six
panel groups. It returns `CORE_KEYS_OK`, `CORE_KEYS_INVALID`,
`CORE_KEYS_BUSY` or `CORE_KEYS_OVERLAP`. A new synthetic hold on a physical
key is refused. Releasing synthetic ownership leaves physical keys held.
`core_keys_clear()` clears synthetic ownership, retaining any hold it
cannot yet release because of queue pressure; callers must retry cleanup.

Both paths mask interrupts through the driver call. Synthetic transitions
reserve queue headroom and respect the stock key record pool. The bridge
has no lease or automatic timeout; it is a test primitive for a bounded
helper, not a finished remote-control mod.

`probe.c` subscribes to the UI tick. The test writes `rk_probe` as four
32-bit words: pending (1=set, 2=clear), group, mask and result. The tick
executes the request, writes the result and clears pending. Its key-event
subscriber records the last key, flags and event count in `rk_key_events`;
setting that array's fourth word consumes SRC to test mod interception.
These mailboxes are test instrumentation, not an external protocol.

Run the host checks with:

```sh
mise exec python@3.12 -- python tests/test_remote_keys.py
```

The firmware checks also need `ELEKLOADER_PATH` (the Elekloader source checkout),
`DIGIEMU_PATH`, `ELEKLOADER_STOCK` (1.53),
`ELEKLOADER_STOCK_154` and `ELEKLOADER_CROSS`, using a Python environment
with the pinned emulator's dependencies. Set `REMOTE_KEYS_OUT` to retain
its temporary builds, reports and screenshots outside the repository.
Missing firmware or emulator inputs are reported as skips.
