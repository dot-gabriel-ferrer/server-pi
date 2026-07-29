# server-pi

Modular microservices infrastructure for a **Raspberry Pi 5** running
**Raspberry Pi OS Lite 64-bit**, orchestrated with Docker Compose.

Each functional domain lives in its own subdirectory with an independent
`docker-compose.yml` and `.env` file.  All modules that need to communicate
share a single external Docker bridge network (`server-pi-net`).

---

## Directory Structure

```
server-pi/
├── .env                              # Global variables (PUID, PGID, TZ, DATA_DIR)
│
├── management/                       # Monitoring & management UI
│   ├── .env
│   ├── docker-compose.yml            # Portainer · Glances · Homepage
│   └── homepage/
│       └── config/                   # Homepage YAML configuration files
│           ├── settings.yaml
│           ├── services.yaml         # Static entries (non-Docker services)
│           ├── docker.yaml           # Docker auto-discovery via socket
│           ├── bookmarks.yaml
│           └── widgets.yaml
│
├── domotics/                         # Home automation & plant monitoring
│   ├── .env
│   ├── docker-compose.yml            # Mosquitto · Zigbee2MQTT · Home Assistant
│   └── mosquitto/
│       └── config/
│           └── mosquitto.conf
│
└── astronomy/                        # Astronomical instrumentation & astrometry
    ├── .env
    ├── docker-compose.yml            # telesco-pi (built from submodule)
    ├── README.md                     # Submodule integration instructions
    └── telesco-pi/                   # Git submodule → dot-gabriel-ferrer/telesco-pi
```

---

## Modules

| Module | Services | Key features |
|--------|----------|-------------|
| **management** | Portainer, Glances, Homepage | Host metrics (incl. SoC temperature), Docker auto-discovery |
| **domotics** | Mosquitto, Zigbee2MQTT, Home Assistant | Zigbee coordinator mapped by USB serial ID; HA on host network |
| **astronomy** | telesco-pi | USB bus access, cgroup device rule, CPU/RAM resource limits |

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Raspberry Pi OS Lite 64-bit | Tested on Pi 5 |
| Docker Engine ≥ 24 | Installed via the official `get-docker.sh` script |
| Docker Compose plugin ≥ 2.20 | Bundled with modern Docker Engine |
| Git ≥ 2.25 | Required for submodule support |

---

## Sequential Setup Commands

Run each block **in order** on the Raspberry Pi host.

### Step 1 — Install Docker

```bash
# Download and run the official Docker installation script
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh

# Add the current user to the docker group (avoids sudo for every docker command)
sudo usermod -aG docker $USER

# Apply the group change in the current shell session
newgrp docker

# Verify
docker version
docker compose version
```

### Step 2 — Clone this repository

```bash
# Clone with submodules in one step
git clone --recurse-submodules \
    https://github.com/dot-gabriel-ferrer/server-pi.git
cd server-pi
```

### Step 3 — Configure environment variables

```bash
# Review and edit the root .env (PUID, PGID, TZ, DATA_DIR)
# Tip: run `id -u` and `id -g` to get your user and group IDs
nano .env

# Repeat for each module (only override values that differ from the root)
nano management/.env
nano domotics/.env
nano astronomy/.env
```

### Step 4 — Identify your Zigbee coordinator device

```bash
# List USB serial devices by stable ID
ls -l /dev/serial/by-id/

# Copy the full path of your Zigbee dongle and paste it into domotics/.env:
#   ZIGBEE_DEVICE=/dev/serial/by-id/usb-Silicon_Labs_Sonoff_Zigbee_3.0_...
nano domotics/.env
```

### Step 5 — Create the shared Docker network

```bash
# This bridge network is declared as external in every docker-compose.yml.
# It must exist before launching any module.
docker network create server-pi-net
```

### Step 6 — Create persistent data directories on the host

```bash
# Read DATA_DIR from the root .env (default: /opt/server-pi)
DATA_DIR=$(grep '^DATA_DIR=' .env | cut -d= -f2)

sudo mkdir -p \
    "${DATA_DIR}/portainer" \
    "${DATA_DIR}/mosquitto/data" \
    "${DATA_DIR}/mosquitto/log" \
    "${DATA_DIR}/zigbee2mqtt" \
    "${DATA_DIR}/homeassistant" \
    "${DATA_DIR}/telesco-pi"

# Set ownership to the PUID/PGID defined in .env
PUID=$(grep '^PUID=' .env | cut -d= -f2)
PGID=$(grep '^PGID=' .env | cut -d= -f2)
sudo chown -R "${PUID}:${PGID}" "${DATA_DIR}"
```

### Step 7 — Integrate the telesco-pi submodule (astronomy module)

```bash
# Register and fetch the submodule (skip if you used --recurse-submodules above)
git submodule add https://github.com/dot-gabriel-ferrer/telesco-pi \
    astronomy/telesco-pi
git submodule update --init --recursive

# Optional (only if you're contributing changes back to this repository):
# git add .gitmodules astronomy/telesco-pi
# git commit -m "feat(astronomy): add telesco-pi as a git submodule"
```

> See [`astronomy/README.md`](astronomy/README.md) for full submodule management
> instructions (update, troubleshooting, hardware notes).

### Step 8 — Build the astronomy image

```bash
# Build the Docker image from the telesco-pi submodule source
cd astronomy
docker compose build
cd ..
```

### Step 9 — Launch all modules

```bash
# Management (Portainer, Glances, Homepage)
cd management && docker compose up -d && cd ..

# Domotics (Mosquitto, Zigbee2MQTT, Home Assistant)
cd domotics && docker compose up -d && cd ..

# Astronomy (telesco-pi)
cd astronomy && docker compose up -d && cd ..
```

### Step 10 — Verify

```bash
# Check all running containers across modules
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Tail logs for a specific service (replace <name> with portainer, glances, etc.)
docker logs -f <name>
```

---

## Service URLs (default ports)

| Service | URL |
|---------|-----|
| Homepage | http://\<pi-ip\>:3000 |
| Portainer | http://\<pi-ip\>:9000 |
| Glances | http://\<pi-ip\>:61208 |
| Zigbee2MQTT | http://\<pi-ip\>:8080 |
| Home Assistant | http://\<pi-ip\>:8123 |
| telesco-pi | http://\<pi-ip\>:5000 |

---

## Updating a module

```bash
cd <module-directory>
docker compose pull          # pull latest images
docker compose up -d         # recreate containers with new images
```

## Stopping everything

```bash
for module in management domotics astronomy; do
    cd $module && docker compose down && cd ..
done
```