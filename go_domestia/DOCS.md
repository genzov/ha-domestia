# Domestia Bridge

> [!WARNING]
> This add-on is deprecated and gets no further updates. It is replaced by the
> Domestia integration; see
> [Migrating from the add-on](https://github.com/genzov/go-domestia#migrating-from-the-add-on).

Bridges a Domestia lighting controller to Home Assistant over MQTT, exposing
each relay as a light through MQTT discovery — no manual entity configuration
required.

## Prerequisites

- A Domestia controller reachable over TCP on port **52001**.
- An MQTT broker that Home Assistant is connected to (e.g. the **Mosquitto
  broker** add-on, hostname `core-mosquitto`).

## Configuration

| Option              | Required | Description                                                       |
| ------------------- | -------- | ----------------------------------------------------------------- |
| `ip_address`        | yes      | IP address of the Domestia controller.                            |
| `model`             | no       | `DMC-012-003` or `DMC-008-001`, shown on each device in HA.       |
| `refresh_frequency` | no       | Controller poll interval in ms (default `2000`, minimum `250`).   |
| `mqtt.ip_address`   | yes      | Hostname/IP of the MQTT broker (port `1883`).                     |
| `mqtt.username`     | no       | MQTT username.                                                    |
| `mqtt.password`     | no       | MQTT password.                                                    |
| `lights[].name`     | yes      | Display name in Home Assistant.                                   |
| `lights[].relay`    | yes      | Relay number on the controller.                                   |
| `lights[].dimmable` | no       | `true` if the relay supports brightness (default `false`).        |
| `lights[].always_on`| no       | Keep the relay forced on at full brightness and hide it from HA.  |

### Example

```yaml
ip_address: "192.168.1.2"
model: "DMC-012-003"
refresh_frequency: 2000
mqtt:
  ip_address: core-mosquitto
  username: "username"
  password: "password"
lights:
  - name: Garden
    relay: 2
    dimmable: false
  - name: Living room
    relay: 13
    dimmable: true
  - name: Wardrobe
    relay: 1
    always_on: true
```

## Usage

1. Fill in the controller IP, MQTT details and your lights.
2. Start the add-on. Your lights appear automatically in Home Assistant, each
   as its own device named after the light.
3. Check the **Log** tab for connection status and per-light state changes.

The add-on shuts down cleanly on stop, marking all lights unavailable in Home
Assistant.
