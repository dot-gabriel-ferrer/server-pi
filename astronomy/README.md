# Astronomy Module — telesco-pi Submodule Integration

This directory hosts the **astronomical instrumentation and astrometry** module.
The application code lives in the separate [`telesco-pi`](https://github.com/dot-gabriel-ferrer/telesco-pi)
repository, which is integrated here as a **Git submodule** so that version
control and CI remain independent for each project.

---

## 1 · Add the submodule (first time only)

Run the following commands from the **root of the `server-pi` repository**:

```bash
# Register telesco-pi as a submodule inside the astronomy/ directory
git submodule add https://github.com/dot-gabriel-ferrer/telesco-pi \
    astronomy/telesco-pi

# Initialise and fetch the submodule's contents
git submodule update --init --recursive

# Commit the new .gitmodules file and the submodule reference
git add .gitmodules astronomy/telesco-pi
git commit -m "feat(astronomy): add telesco-pi as a git submodule"
```

After this step the directory `astronomy/telesco-pi/` will contain a full
working tree of the `telesco-pi` repository, pinned to the commit that was
HEAD at the time of registration.

---

## 2 · Clone the parent repo (subsequent checkouts)

When someone clones `server-pi` for the first time they must also initialise
the submodule:

```bash
# Option A — clone and initialise submodules in one step
git clone --recurse-submodules \
    https://github.com/dot-gabriel-ferrer/server-pi.git

# Option B — already cloned, submodule not yet initialised
git submodule update --init --recursive
```

---

## 3 · Update the submodule to the latest upstream commit

```bash
# Pull the latest changes from the default branch of telesco-pi
git submodule update --remote astronomy/telesco-pi

# Review and commit the updated pointer
git add astronomy/telesco-pi
git commit -m "chore(astronomy): bump telesco-pi submodule to latest"
```

---

## 4 · Build and run

```bash
cd astronomy

# Build the Docker image from the submodule source
docker compose build

# Start the service
docker compose up -d

# Follow logs
docker compose logs -f telesco-pi
```

The `docker-compose.yml` in this directory:

| Feature | Detail |
|---------|--------|
| **USB bus access** | `/dev/bus/usb` is passed through in full so that libusb can reach the telescope mount and any connected cameras without restarting the container after hot-plug events. |
| **cgroup device rule** | `c 189:* rmw` grants read / mknod / write on USB character devices (kernel major 189) only — more surgical than `privileged: true`. |
| **CPU limit** | `TELESCO_CPU_LIMIT=1.5` (out of 4 cores on the Pi 5) — prevents plate-solving from monopolising the scheduler. |
| **Memory limit** | `TELESCO_MEMORY_LIMIT=1g` — prevents the astrometry index files from filling RAM and swapping out domotics services. |
| **Adjusting limits** | Edit `TELESCO_CPU_LIMIT`, `TELESCO_MEMORY_LIMIT`, etc. in `astronomy/.env` and run `docker compose up -d` to apply. |

---

## 5 · Hardware notes

### Telescope mount (USB-serial)

The mount adapter (EQMod HID, Sky-Watcher, etc.) typically appears as
`/dev/ttyUSB0` or `/dev/ttyACM0`.  Because `/dev/bus/usb` is already mapped,
libusb-based drivers work out of the box.  For POSIX serial drivers add a
specific `devices:` entry in `docker-compose.yml`:

```yaml
devices:
  - /dev/bus/usb:/dev/bus/usb
  - /dev/ttyUSB0:/dev/ttyUSB0   # EQMod / Sky-Watcher adapter
```

### Astronomy cameras (ZWO ASI, QHY, etc.)

ZWO ASI cameras use `libASICamera2` over libusb — no extra device mapping
needed beyond `/dev/bus/usb`.  For QHY cameras the same applies.

---

## 6 · Troubleshooting

| Symptom | Fix |
|---------|-----|
| `no such file or directory: /dev/bus/usb` | USB subsystem not loaded. Run `sudo modprobe usbcore` on the host. |
| Camera not found inside container | Check `dmesg` for device disconnects; verify the user running Docker belongs to the `plugdev` group. |
| Plate-solving OOM-killed | Increase `TELESCO_MEMORY_LIMIT` in `.env` and restart. |
| `git submodule update` fails | Ensure you have network access and a valid SSH key / HTTPS token for the `telesco-pi` repository. |
