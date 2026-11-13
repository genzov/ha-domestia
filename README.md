# Domestia for Home Assistant

The **Domestia** integration connects Home Assistant directly to a Domestia
lighting controller over your local network. It controls each relay on the
controller as a Home Assistant light, without an MQTT broker, a bridge or an
add-on. It replaces the go-domestia MQTT bridge and the Domestia Bridge add-on
(versions 1.x of this repository); see
[Migrating from the add-on](#migrating-from-the-add-on).

The integration polls the controller over TCP port 52001, shows each configured
relay as a light with its own device, and sends on, off and brightness commands
to the controller. All communication stays on your local network.

## Supported devices

- Domestia **DMC-012-003**
- Domestia **DMC-008-001**

Other Domestia controllers that speak the same protocol on port 52001 may work,
but they have not been tested.

## Supported functions

Each light that is not *always on* becomes a `light` entity, with its own
device that is linked to a "Domestia controller" device.

- **Dimmable relays:** on, off and brightness.
- **Non-dimmable relays:** on and off. They always run at full brightness. If
  one is found dimmed, for example after a wall panel dimmed it, the integration
  sets it back to full brightness.
- **Always-on relays:** these get no entity or device, so they are hidden from
  Home Assistant. On every poll the integration switches them back on at full
  brightness if something turned them off or dimmed them.

Turning a light on can take two commands: on, then full or the requested
brightness. If one fails, the integration still sends the other and then reports
the failure, so the light may end up on even though the action reports
"Failed to control …".

The user interface is available in English, Dutch and French. Home Assistant
uses the language of your profile and falls back to English.

The integration adds no custom actions, triggers or conditions. Use the
standard `light.turn_on`, `light.turn_off` and `light.toggle` actions.

## Prerequisites

- Home Assistant **2026.11.0** or later.
- The controller must have a fixed IP address (or a hostname that resolves),
  and Home Assistant must reach it on **TCP port 52001**.
- The relay number and name of every light you want to control. The controller
  does not report them.

## Installation

**Marketplace:** install it through Home Assistant's built-in Marketplace (HACS
was merged into core in 2026.11). Adding a custom repository needs the Marketplace's GitHub connection.

1. Open the Marketplace and add `https://github.com/genzov/go-domestia` as a
   custom repository of the **Integration** category.
2. Install **Domestia**.
3. Restart Home Assistant.

Updates are offered under **Settings → Updates** when a new release is
published.

**Manual installation:**

1. Copy the `custom_components/domestia` folder from this repository to
   `/config/custom_components/domestia` on your Home Assistant installation, for
   example with the Samba or SSH add-on.
2. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for
   **Domestia**.
2. On **Connect to your Domestia controller**, fill in:

   | Field     | Required | Description                                                                                                   |
   | --------- | -------- | ------------------------------------------------------------------------------------------------------------- |
   | **Host**  | yes      | IP address or hostname of the controller, e.g. `192.168.1.2`. It must be reachable on TCP port 52001.        |
   | **Model** | no       | `DMC-012-003` or `DMC-008-001`. Only shown on the devices.                                                    |

   The integration connects to the controller before it continues. If it can't,
   the form shows "Failed to connect to the controller." and you can correct the
   host and try again. Each controller can be added only once per IP address or hostname.
3. On **Import lights**, you can paste your lights into **Lights**, or leave it
   empty and add lights one by one later. This is the `lights` list from the
   Domestia Bridge add-on's **Configuration** tab, copied in YAML mode:

   ```yaml
   - name: Garden
     relay: 2
   - name: Living room
     relay: 13
     dimmable: true
   - name: Wardrobe
     relay: 1
     always_on: true
   ```

   A JSON array works too, e.g. `[{"name": "Garden", "relay": 2}]`. In YAML,
   quote names that YAML reads as something else, such as `"On"` or `"Yes"`.

   | Key         | Required                   | Description                                                            |
   | ----------- | -------------------------- | ---------------------------------------------------------------------- |
   | `name`      | yes, unless `always_on`    | Name of the light and its device.                                      |
   | `relay`     | yes                        | Relay number on the controller, from 1 to 255. Each relay only once.   |
   | `dimmable`  | no, defaults to `false`    | Whether the relay can dim the light.                                   |
   | `always_on` | no, defaults to `false`    | Keep the relay on at full brightness and hide it from Home Assistant.  |

   Invalid input is reported on the form: "The lights must be a list of lights
   in YAML or JSON.", "Every light needs a relay number between 1 and 255.", "Two lights
   use the same relay." or "Every light that is not always on needs a name."
4. Submit. Each light becomes a light entry under the controller. Lights that
   are not always on also get their own device and entity. The entity id is
   `light.<name>` with the name turned into an id the usual Home Assistant way,
   e.g. `Living room` becomes `light.living_room`. If that id is already taken,
   Home Assistant adds a suffix such as `_2`. An always-on light without a name
   is called `Relay <number>`. Pasting the list together with its `lights:`
   line also works.

## Managing lights

Every light is a **light entry** (a config subentry) of the controller's
integration entry, under **Settings → Devices & services → Domestia**.

- **Add a light:** select **Add light** and fill in **Name**, **Relay**,
  **Dimmable** and **Always on**, which have the same meaning as the JSON keys
  above. A relay that already has a light is refused with "This relay already
  has a light."
- **Edit a light:** open the light entry's menu and select **Reconfigure**. You
  can rename it, move it to another relay, or change dimmable and always on.
  The entity, device and history are kept, even when the relay changes. Turning
  on **Always on** is the exception: the light's entity and device are removed,
  because always-on lights are hidden.
- **Remove a light:** open the light entry's menu and select **Delete**. Its
  device and entity are removed.

The integration reloads after each change.

**Change the controller's host or model:** open the integration entry's menu
and select **Reconfigure**. The new host is tested before it is saved.

## Data updates

The controller cannot push changes, so the integration **polls** it every 2
seconds. Changes made at a wall panel therefore show up in Home Assistant
within 2 seconds.

To poll at another rate, open the integration entry's menu, select **System
options**, turn off **Enable polling for changes**, and create an automation
that calls the `homeassistant.update_entity` action on one of the Domestia
lights as often as you like. One update refreshes all lights of the
controller.

- After a command from Home Assistant, the integration polls again right away,
  so the new state shows without waiting for the next poll. Further commands
  within 0.3 seconds share one extra poll after that.
- A single failed poll does not make the lights unavailable. They keep their
  last known state, and only after **3 failed polls in a row** do they become
  unavailable. They recover on the next successful poll. This is logged once
  when it happens (as an error) and once when it recovers.
- If the controller can't be reached when Home Assistant starts, setup is
  retried in the background until it can.

## Use cases

- Control the lights wired to the Domestia controller from dashboards, voice
  assistants and the Home Assistant app.
- Switch lights from automations, e.g. at sunset, on presence or on a motion
  sensor, alongside lights from other brands.
- Keep relays that must never go off, such as a technical room or a ventilation
  circuit, always on and out of sight.

## Examples

Turn the living room lights on at 60% at sunset, and off at midnight:

```yaml
automation:
  - alias: Living room lights at sunset
    triggers:
      - trigger: sun
        event: sunset
    actions:
      - action: light.turn_on
        target:
          entity_id: light.living_room
        data:
          brightness_pct: 60

  - alias: Living room lights off at midnight
    triggers:
      - trigger: time
        at: "00:00:00"
    actions:
      - action: light.turn_off
        target:
          entity_id: light.living_room
```

## Known limitations

- **No discovery.** The controller announces nothing on the network, and its
  network chip (Espressif) is the same one many other smart devices use, so it
  cannot be told apart. Enter its host by hand, and give it a DHCP reservation
  in your router. If its address changes anyway, use **Reconfigure**.
- **The controller reports no names.** It only reports a brightness per relay,
  so every light has to be configured by hand, or imported from the add-on
  configuration.
- **One request at a time.** The controller handles one TCP client at a time.
  The integration sends one request at a time and opens a new connection for
  each, so other software polling the same controller can cause failed polls.
- **64 brightness steps.** The controller has 64 brightness levels, while Home
  Assistant has 255. The brightness you set is rounded to the nearest level, so
  it can read back slightly different. A non-zero brightness always keeps the
  light on at the lowest level instead of switching it off.
- **Only tested with the DMC-012-003 and DMC-008-001.**

## Troubleshooting

**"Failed to connect to the controller." during setup or reconfigure**

- Check the host or IP address, and that the controller is powered and on the
  network.
- Check that Home Assistant can reach TCP port 52001, for example from the SSH
  add-on with `nc -vz 192.168.1.2 52001`. Firewalls and VLANs between Home
  Assistant and the controller must allow it.
- Stop other software that talks to the controller, such as the Domestia Bridge
  add-on, because the controller handles one client at a time.

**The lights are unavailable**

The controller didn't answer 3 polls in a row. Check the points above. The
lights come back by themselves once the controller answers again.

**Repair: "Configured relays missing on the controller"**

The controller's reply does not contain some configured relays, which usually
means the controller has fewer relays than configured, or a relay number is
wrong. The repair lists the missing relays, and their lights are unavailable.
Fix the relay number of these lights or remove them (see
[Managing lights](#managing-lights)). The repair disappears once the controller
reports all configured relays.

**A command fails with "Failed to control" and the light's name**

The controller did not accept the command. The error after the colon tells why,
usually a connection problem.

**Debug logging**

Add this to `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.domestia: debug
```

Or select **Enable debug logging** on the integration's page, reproduce the
problem, and select **Disable debug logging** to download the log. When you
report an issue, also attach the integration's **Download diagnostics** file.
The controller's host is redacted from it.

## Removal

1. Go to **Settings → Devices & services → Domestia**, open the integration
   entry's menu and select **Delete**. This removes the controller, all lights,
   their devices and entities.
2. Remove the files:
   - **Marketplace:** uninstall Domestia in the Marketplace.
   - **Manual installation:** delete `/config/custom_components/domestia`.
3. Restart Home Assistant.

## Migrating from the add-on

The integration replaces the Domestia Bridge add-on. Run one of the two, never
both, because the controller handles one client at a time. History carries
over when the new entities get the same entity ids as the old ones.

1. Note the entity ids of your Domestia lights, e.g. `light.garden`, and copy
   the `lights` list from the add-on's **Configuration** tab (in YAML mode).
   The import step accepts it as is; see [Setup](#setup).
2. Install the integration as described in [Installation](#installation) and
   restart Home Assistant. Don't add it yet.
3. Stop the **Domestia Bridge** add-on and turn off **Start on boot**. Its
   lights become unavailable.
4. Remove the old MQTT lights so their entity ids are freed. The bridge
   published its discovery configs as retained messages, so clear them on the
   broker, for every light, with the entity part being the light name
   lowercased with spaces replaced by `_`:

   ```sh
   mosquitto_pub -h <broker> -u <username> -P <password> -r -n -t homeassistant/light/<entity>/config
   mosquitto_pub -h <broker> -u <username> -P <password> -r -n -t domestia/light/<entity>/state
   ```

   Once for all lights, clear the bridge's availability topic too:

   ```sh
   mosquitto_pub -h <broker> -u <username> -P <password> -r -n -t domestia/bridge/availability
   ```

   Then check under **Settings → Devices & services → MQTT → Devices** that the
   Domestia devices are gone, and delete any that are left.
5. Add the Domestia integration as described in [Setup](#setup), and paste the
   `lights` array in the import step.
6. Check that the entity ids match the ones from step 1, and rename any that
   don't. Check your automations, scripts and dashboards.
7. Test switching on and off, dimming, a non-dimmable light and an always-on
   relay: switch it off at a wall panel and check that it comes back on. Then
   unplug the controller, check that the lights become unavailable after about
   3 polls, and that they recover once it is back.
8. After a week or so without problems, uninstall the add-on.

## Development

The integration is developed with [uv](https://docs.astral.sh/uv/), which also
installs the Python version it needs.

```sh
uv sync                        # create .venv with the dev dependencies
uv run pytest                  # tests
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy                    # strict type checking
uv run pre-commit install      # run the checks on each commit
```


Releases are published from GitHub when the `version` in
`custom_components/domestia/manifest.json` changes on `main`; see
[CHANGELOG.md](CHANGELOG.md).
