# Changelog

Versions 2.0.0 and later are the Domestia integration. Earlier versions are the
Domestia Bridge add-on, which it replaces.

## 2.0.0

- First release of the Domestia integration. It connects Home Assistant to the
  controller directly over your local network, without MQTT, a broker or an
  add-on, and replaces the Domestia Bridge add-on.
- Lights are added, changed and removed in the Home Assistant UI. The `lights`
  list from the add-on configuration can be pasted during setup.
- Lights become unavailable after 3 failed polls, and a repair issue reports
  configured relays that the controller does not have.
- Coming from the add-on? Follow
  [Migrating from the add-on](https://github.com/genzov/go-domestia#migrating-from-the-add-on)
  to keep your entity ids and history.

## 1.4.0

- Lights now reflect their real state in Home Assistant after a short MQTT
  outage. Previously a change made while the broker was unreachable could stay
  out of date until the light changed again.
- Commands from Home Assistant no longer hold up other messages while the
  controller is slow or unreachable, and are executed in the order received. A
  command that is not an explicit `ON` or `OFF` is ignored instead of switching
  the light off.
- A garbled reply from the controller is now treated as a failed poll instead
  of a successful one, and replies that arrive in several pieces are read
  completely.
- `refresh_frequency` must now be at least 250 ms (it used to be anything above
  0). A lower value stops the add-on from starting; raise it if you used one.
- The light configuration is now checked at startup, with a clear error. Relay
  numbers must be 1 or higher and used by one light only. Light names must not
  be empty, contain `+`, `#` or `/`, or end up as the same entity as another
  light. Always-on lights are exempt from the name rules.
- Updated Go and dependencies to fix known security vulnerabilities in the
  Go standard library.

## 1.3.2

- Occasional controller timeouts no longer flood the log. A single failed poll
  is retried on the next refresh and only logged at debug level. If the
  controller keeps failing, the lights are still marked unavailable and an error
  is logged, as before.

## 1.3.1

- Log lines now have a level and key=value details, e.g.
  `INFO Light state changed light="Living room" state=on`. The same events are
  logged as before.

## 1.3.0

- New optional `model` option to set the controller model (`DMC-012-003` or
  `DMC-008-001`). It is shown as the model of each light's device in Home
  Assistant and is informational only.
- Images are now published for `armv7` and `amd64` as well as `aarch64`, so the
  add-on can be installed on 32-bit ARM boards and x86-64 machines too.

## 1.2.2

- Retained discovery configs left behind by renaming a light are removed on
  startup. They shared the light's unique ID, which could keep a stale entity
  around and stop the current one from being updated.
- The internal config topic is no longer included in the discovery payload.

After updating, restart Home Assistant once so existing light entities are
attached to their devices.

## 1.2.1

- A slow or unreachable controller no longer restarts the bridge (which
  reconnected MQTT and re-registered every light). Failed polls are retried,
  and lights are marked unavailable after 3 consecutive failures until the
  controller responds again.
- Controller requests now time out after 3 seconds (was 1 second, with no
  connect timeout).

## 1.2.0

- Each light is registered as its own Home Assistant device, named after the
  light and reporting the add-on version. Existing entities keep their IDs and
  are attached to their device automatically.

## 1.1.0

- Correct brightness scaling: Home Assistant's 0–255 brightness is now rounded
  onto the controller's 0–63 scale, and low values dim the light instead of
  switching it off.
- Graceful shutdown with availability reporting (online/offline + MQTT Last
  Will) so lights are marked unavailable when the bridge stops or dies.
- Controller and MQTT errors are logged instead of silently ignored; per-light
  state logging now reports on/off and dim level.
- Configuration is validated at startup.
- Published aarch64 image for Raspberry Pi 3 (64-bit), plus docs, sample
  configuration and store metadata.

## 1.0.0

- Initial release.
