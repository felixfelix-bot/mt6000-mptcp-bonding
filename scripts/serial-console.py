#!/usr/bin/env python3
"""
Serial console helper for OpenWrt routers (the password-free bootstrap).

Why: a TollGate-flashed MT6000 derives its root password from a per-device
mnemonic that is not stored anywhere retrievable. The UART console does not ask
for it — it drops straight to a root shell. This gets you in and can install an
SSH key so Ansible can take over.

Requires: pyserial  (pip install pyserial)

Usage:
  serial-console.py --port /dev/ttyUSB0 --cmd 'uname -a'
  serial-console.py --port /dev/ttyUSB0 --shell
  serial-console.py --port /dev/ttyUSB0 --install-key ~/.ssh/id_ed25519.pub
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.exit("pyserial missing: pip install pyserial")

BAUD = 115200


def open_port(port: str, baud: int = BAUD) -> "serial.Serial":
    return serial.Serial(port, baud, timeout=1)


def drain(ser, seconds: float) -> str:
    """Read whatever arrives within `seconds` (resetting the clock on activity)."""
    buf = b""
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = ser.read(65536)
        if chunk:
            buf += chunk
            deadline = time.time() + seconds
    return buf.decode(errors="replace")


def run_cmd(ser, cmd: str, wait: float = 6.0) -> str:
    ser.write(cmd.encode() + b"\n")
    return drain(ser, wait)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyUSB0", help="serial device")
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--cmd", help="run one command and print the output")
    ap.add_argument("--shell", action="store_true", help="interactive shell (Ctrl-] to exit)")
    ap.add_argument("--install-key", metavar="PUBKEY_PATH",
                    help="append an SSH pubkey for both dropbear and root")
    args = ap.parse_args()

    ser = open_port(args.port, args.baud)
    ser.reset_input_buffer()
    print(drain(ser, 2.0), end="")          # banner / prompt

    if args.install_key:
        with open(args.install_key) as fh:
            pub = fh.read().strip()
        marker = pub.split()[1][:20]
        cmds = [
            "mkdir -p /etc/dropbear /root/.ssh",
            f"grep -q '{marker}' /etc/dropbear/authorized_keys 2>/dev/null || echo '{pub}' >> /etc/dropbear/authorized_keys",
            f"grep -q '{marker}' /root/.ssh/authorized_keys 2>/dev/null || echo '{pub}' >> /root/.ssh/authorized_keys",
            "chmod 600 /etc/dropbear/authorized_keys /root/.ssh/authorized_keys",
            "grep -c . /etc/dropbear/authorized_keys /root/.ssh/authorized_keys",
        ]
        for c in cmds:
            print(run_cmd(ser, c), end="")
        print("\nKey installed. Test:  ssh root@<router-ip> true")
        return 0

    if args.cmd:
        print(run_cmd(ser, args.cmd), end="")
        return 0

    if args.shell:
        print("Interactive. Ctrl-] to exit.")
        try:
            while True:
                line = input("> ")
                print(run_cmd(ser, line), end="")
        except (EOFError, KeyboardInterrupt):
            print()
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
