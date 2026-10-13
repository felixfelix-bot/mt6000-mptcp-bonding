# ACCESS — from zero to an Ansible-manageable router

Two paths. **Serial is the bootstrap; SSH is the goal.** You need SSH because
Ansible speaks SSH.

---

## Path A — USB-UART serial console (no password, always works)

The MT6000 exposes a 3-pin UART header. `openwrt-serial-lab` on the fleet host
holds the adapter setup; here is the essence.

**Wiring (3 wires only):**

| Adapter | Router header |
|---|---|
| GND | GND |
| TX | RX |
| RX | TX |

Do **not** connect VCC — the router is powered by its own supply.

**Serial params:** `115200 8N1`, no flow control.

```sh
# find the adapter
ls /dev/ttyUSB*                     # FTDI FT232R shows as ttyUSB0
python3 scripts/serial-console.py --port /dev/ttyUSB0 --cmd 'uname -a'
python3 scripts/serial-console.py --port /dev/ttyUSB0 --shell    # interactive
```

**Why this matters:** a TollGate-flashed box has a root password derived from a
per-device mnemonic (shown once at provisioning, not stored anywhere the fleet
can read). The console does not ask for it. Symptom if the lead is not seated:
echo test shows your own write, then nothing — check TX/RX are not swapped.

---

## Path B — SSH (what Ansible uses)

### B1. Install a key over serial

```sh
python3 scripts/serial-console.py --port /dev/ttyUSB0 \
  --install-key ~/.ssh/id_ed25519.pub
```

The script appends to **both** `/etc/dropbear/authorized_keys` and
`/root/.ssh/authorized_keys` (dropbear images differ in which one they consult).

### B2. Reachability

The router's management address must be on an interface that is **zoned** and
answers on :22. Check from the router itself:

```sh
ssh <router> 'netstat -tlnp | grep ":22 "'
```

If you reach the LAN bridge but not the new WAN, that is §4 of `LEARNINGS.md`
(unzoned interface → fw4 `handle_reject`). Fix the zone, or manage over the
LAN side.

### B3. If direct routing is unavailable, use a jump host

In this deployment the router's reachable management address is on its LAN
bridge, which is only routable from a host plugged into it. Ansible handles
that with `ProxyJump`:

```ini
# ~/.ssh/config
Host vil4
    HostName 10.115.249.1
    User root
    ProxyJump cobrador
    StrictHostKeyChecking no
    ConnectTimeout 15
```

```yaml
# inventory/hosts.yml
all:
  hosts:
    mt6000:
      ansible_host: vil4
```

**Verify before running Ansible:**
```sh
ssh vil4 'echo OK'     # must print OK
ansible -i inventory/hosts.yml mt6000 -m ping
```

---

## Troubleshooting matrix

| Symptom | Cause | Fix |
|---|---|---|
| serial: nothing at all | TX/RX swapped, or wrong port | swap the two data wires |
| serial: echo only, no shell | router not booted / baud wrong | confirm 115200 8N1, press Enter |
| ssh: `Connection refused` | interface unzoned (fw4 REJECT) | §4 LEARNINGS.md |
| ssh: `Connection closed` right after kex | tproxy rule diverting to the router | §5 LEARNINGS.md |
| ssh: `Permission denied (publickey)` | key in wrong file | install to both paths (B1) |
| ssh: timeout, `tcpdump` shows SYN in and **no** reply | per-source table missing connected route | §3 LEARNINGS.md |
