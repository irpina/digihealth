# SPDX-License-Identifier: GPL-2.0-or-later
"""Mido transport shared by diagnostics and keys; adapted from the measurement runner."""
import time


class Port:
    def __init__(self):
        import mido
        self.mido = mido

        def choose(names):
            matches = [name for name in names
                       if any(product in name.lower() for product in ('digitakt', 'digitone'))]
            if len(matches) != 1:
                raise OSError('Expected one Digitakt/Digitone port; found %r. Available: %r' % (matches, names))
            return matches[0]

        self.incoming = mido.open_input(choose(mido.get_input_names()))
        try:
            self.outgoing = mido.open_output(choose(mido.get_output_names()))
        except Exception:
            self.incoming.close()
            raise

    def send(self, data):
        self.outgoing.send(self.mido.Message.from_bytes(data))

    def receive(self, seconds=1.0, until=None):
        messages = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            messages.extend(bytes(message.bytes()) for message in self.incoming.iter_pending()
                            if message.type == 'sysex')
            if until and until(messages):
                break
            time.sleep(0.002)
        return messages

    def close(self):
        self.incoming.close()
        self.outgoing.close()
