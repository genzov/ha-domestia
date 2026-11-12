# Migration plan: go-domestia bridge → native Home Assistant integration

Goal: replace the Go MQTT bridge and add-on with a Python custom integration, `custom_components/domestia`, installed through HACS. It talks to the controller directly over TCP port 52001. Target: **Silver complete, Gold wherever feasible**, recorded in `quality_scale.yaml`.

Custom integrations are unranked ("📦 Custom") on the official scale, so the tier is **self-assessed**. It is still checked rule by rule, using the same rule ids core uses.

Status: plan only. Nothing here has been run against the real controller. The Go code, add-on and release pipeline stay in place and working until Phase 5.

---

## 0. Verified facts this plan relies on

| Fact | Verified how |
|---|---|
| The test harness is `pytest-homeassistant-custom-component==0.13.375`. It needs **Python ≥ 3.14** and pulls in **HA 2026.10.4**, `pytest-asyncio`, `pytest-cov`, `syrupy` and `pytest-freezer`. | Installed into a scratch venv with `uv venv -p 3.14`. The local default `python3` is 3.12 and is **too old**, so always use `uv`. |
| Config subentries exist: `ConfigSubentryFlow`, `async_get_supported_subentry_types`, and `async_create_entry(..., subentries=[...])`. A duplicate subentry `unique_id` aborts with `already_configured`. | Read in `homeassistant/config_entries.py` (2026.10.4). |
| Custom integrations can ship brand images locally from a `brand/` folder (`Integration.has_branding`). | Read in `homeassistant/loader.py`. |
| There is no core integration with domain `domestia`. | `ls homeassistant/components`. |
| Adding or removing a subentry (`async_add_subentry`/`async_remove_subentry`) fires the entry's **update listeners**, and removing one clears its devices and entities from the registries. The flow helper `ConfigSubentryFlow.async_update_reload_and_abort` **raises `ValueError` if the entry has update listeners**, and `ConfigFlow.async_update_reload_and_abort` logs a warning in that case. `async_update_subentry(..., unique_id=...)` accepts a new unique_id and checks it for duplicates. | Read in `config_entries.py`. **Consequence:** we use exactly one reload mechanism, the update listener (§1.2). |
| The rule ids per tier are listed in §7. | contributors.home-assistant.io quality-scale checklist. |

---

## 1. Design decisions (frozen in Phase 0)

### 1.1 What maps to what

| Go bridge | Integration |
|---|---|
| `domestia/client.go` | `api.py`: a pure asyncio client with **no HA imports**, so it could later be extracted to PyPI |
| `domestia/light.go` `NewLight` | `api.normalize_brightness(raw, dimmable)` |
| `bridge/light_state.go` | `brightness.py`: pure conversion functions |
| run loop, ticker, `updateChannel` | `DataUpdateCoordinator` plus `async_request_refresh()` after commands. The debouncer has `cooldown=0.3, immediate=True`, which reproduces the "refreshes coalesce" behaviour. |
| 3-strikes `controllerFailures` | The coordinator returns the last good data for failures 1 and 2 and raises `UpdateFailed` on the 3rd. **This must be explicit**, because a stock coordinator goes unavailable on the first failure. |
| Policy enforcement in `publishLightState` | `coordinator._async_update_data`, which also covers `always_on` relays that have no entity |
| `executeCommand` | `DomestiaLight.async_turn_on` / `async_turn_off` |
| `config/` JSON and validation | the config flow, light subentries, and a one-time JSON import step |
| `refresh_frequency` (ms, min 250) | options flow `scan_interval` (seconds, float, min 0.25, default 2.0) |
| MQTT discovery, availability topic, LWT, orphan cleanup, `cmdAction` | **deleted**. The HA entity and device registries and coordinator availability replace them. |
| `slog` | `logging.getLogger(__package__)`, with `%s`-style lazy args |

### 1.2 Config entry data model

- **Entry**, one per controller:
  - `data = {CONF_HOST: str, CONF_MODEL: str | None}`
  - `options = {CONF_SCAN_INTERVAL: float}`
  - `unique_id = host`. The controller exposes no serial or MAC, so the host is the only identity. Reconfigure updates it.
- **Light subentries**, of type `"light"`, one per relay:
  - `title` = light name
  - `unique_id = str(relay)`, which gives duplicate-relay protection for free
  - `data = {CONF_RELAY: int (1-255), CONF_DIMMABLE: bool, CONF_ALWAYS_ON: bool}`
  - **Reload mechanism (one only):**
    - `async_setup_entry` registers an update listener that calls `hass.config_entries.async_schedule_reload(entry.entry_id)`.
    - Subentry add and remove, the options flow and every reconfigure step trigger it through registry updates.
    - Reconfigure steps call `hass.config_entries.async_update_entry` / `async_update_subentry` themselves, then `self.async_abort(reason="reconfigure_successful")`.
    - They **never** call `async_update_reload_and_abort`, which raises `ValueError` (subentry flow) or double-reloads (entry flow) when a listener exists.
  - HA removes a removed subentry's devices and entities automatically.
- **Import step:** the user flow has an optional second step, `lights`. It has one multiline text field that accepts the `lights` JSON array from the current add-on options. `parse_lights_json()` validates it, and each light becomes a subentry passed to `async_create_entry(subentries=...)`. This is how existing users migrate without retyping 16 relays.

### 1.3 Identity

The registry scopes `unique_id`s per integration, and device identifiers are `(domain, id)` tuples. Reusing the Go `d_<relay>` and `domestia_relay_<relay>` values would therefore **not** carry anything over from the MQTT entities. Continuity comes from **entity_id**, which the recorder keys on (see the runbook in §6). The new identifiers also support more than one controller:

| Thing | Identifier |
|---|---|
| Controller device | `(DOMAIN, entry.entry_id)`. Name "Domestia controller", manufacturer "Domestia", model = `CONF_MODEL`. |
| Light device | `(DOMAIN, subentry_id)`. Name = subentry title, `via_device` = controller, same manufacturer and model. |
| Light entity | `unique_id = subentry_id`, `has_entity_name = True`, `name = None` (the entity is its device's main feature). |

Identity is keyed on **`subentry_id`, not the relay**. Rewiring a light to another relay through subentry reconfigure therefore keeps its entity, device and history. Keying on the relay would orphan the old registry entries.

The entity_id becomes `light.<slugified light name>`. That matches the Go entity id (lowercase, spaces → `_`) for plain ASCII names.

### 1.4 Behaviour that must be preserved exactly

1. **Brightness rounding:** port the Go formulas literally. Use `math.floor(x + 0.5)` and clamp. Do **not** use Python `round()` (banker's rounding) or HA's `color_util` brightness helpers, because both break the round-trip test.
   - `to_ha(raw) = min(floor(raw * 255/64 + 0.5), 255)`
   - `to_domestia(ha) = clamp(floor(ha * 64/255 + 0.5), 0, 64)`
2. **Turn on:**
   1. Send `turn_on(relay)`.
   2. Non-dimmable: send `set_max_brightness(relay)`.
   3. Dimmable with `ATTR_BRIGHTNESS`: send `set_brightness(relay, max(1, to_domestia(b)))`, so a non-zero HA brightness is floored to 1.
   4. Dimmable without brightness: send only the on command.
3. **Turn off:** `turn_off(relay)`.
4. **Command failure:** catch `DomestiaError` and raise `HomeAssistantError(translation_domain=DOMAIN, translation_key="command_failed", translation_placeholders={"name": ..., "error": ...})`. Unlike Go, it does not just log. A refresh is requested in all cases.
5. **Normalization:** a non-dimmable relay reporting `1` is normalized to 64. For dimmable relays, `1` is a real level.
6. **Policy, per poll:**
   - An `always_on` relay that is not at 64 gets `turn_on` plus `set_max_brightness`.
   - A non-dimmable relay with `0 < b < 64` gets `set_max_brightness`.
   - In both cases the coordinator reports the **enforced target** (64) for that relay instead of the raw value, mirroring Go's "don't publish".
   - Policy command errors are logged as warnings and never fail the update.
7. **Availability:** after 3 consecutive `DomestiaError`s, raise `UpdateFailed` with translation key `cannot_connect`. Below that threshold, log at debug and return `self.data`.
   - **If `self.data is None`** (first refresh, or no successful poll yet), raise `UpdateFailed` immediately. `async_config_entry_first_refresh` turns that into `ConfigEntryNotReady`, so setup never completes without data. HA's coordinator logs unavailable and recovered once each, which covers `log-when-unavailable`.
8. **Transport:**
   - Fresh TCP connection per request.
   - `asyncio.Lock` serializes requests.
   - The 3 s timeout covers connect and the first reply bytes.
   - After the first bytes, each further read waits at most 250 ms.
   - EOF or timeout with partial data returns that partial data; with no data it is an error.
   - The read buffer is capped at 256 bytes.
   - Get-state `min_length = 3 + highest configured relay`, including `always_on` relays.
9. **Short state reply (new, Gold `repair-issues`):** if configured relays are missing from a valid reply (the controller has fewer relays than configured), create repair issue `relay_not_reported` with the missing relays listed, and delete it once they reappear. Go silently skipped them. Entities for missing relays become unavailable through `available`.

---

## 2. Target layout

```
custom_components/domestia/
  __init__.py        async_setup_entry / async_unload_entry / update listener, runtime_data
  api.py             DomestiaClient, pack_command, parse_state, read_response, normalize_brightness, errors (no HA imports)
  brightness.py      to_ha, to_domestia, describe_state (pure)
  const.py           DOMAIN, CONF_*, MODELS, SUBENTRY_TYPE_LIGHT, DEFAULT/MIN_SCAN_INTERVAL, MAX_CONSECUTIVE_FAILURES, LOGGER
  coordinator.py     DomestiaCoordinator, LightConfig dataclass
  entity.py          DomestiaEntity (CoordinatorEntity base, device_info)
  light.py           DomestiaLight, PARALLEL_UPDATES = 1
  config_flow.py     DomestiaConfigFlow (user, lights import, reconfigure), OptionsFlow, LightSubentryFlow (user, reconfigure), parse_lights_json
  diagnostics.py     async_get_config_entry_diagnostics (redact host)
  manifest.json      domain, name, version, codeowners ["@genzov"], config_flow true, integration_type "hub", iot_class "local_polling", requirements [], documentation, issue_tracker
  quality_scale.yaml every rule: done / todo / exempt + comment
  translations/en.json   the single source of strings (config, options, config_subentries, exceptions, issues)
  brand/icon.png, brand/logo.png   copied from go_domestia/
tests/
  __init__.py
  conftest.py        auto enable_custom_integrations, mock_client fixture, mock_config_entry fixture (with 3 subentries: dimmable, non-dimmable, always_on)
  test_api.py  test_brightness.py  test_coordinator.py  test_config_flow.py  test_init.py  test_light.py  test_diagnostics.py
  snapshots/         syrupy
hacs.json            {"name": "Domestia", "homeassistant": "2026.10.0", "render_readme": true}
pyproject.toml       uv-managed dev project (see below) + tool config: pytest (asyncio_mode=auto), coverage (fail_under=95), ruff, mypy strict
uv.lock              committed; CI uses `uv sync --locked`
.python-version      3.14
.pre-commit-config.yaml  ruff check --fix, ruff format, mypy, uv-lock check
```

**Tooling: uv-managed, no `requirements*.txt`.**
- `pyproject.toml` contains:
  - `requires-python = ">=3.14"`
  - `[tool.uv] package = false`, because HA loads the integration from `custom_components/`; it is not a wheel
  - `[dependency-groups] dev = ["pytest-homeassistant-custom-component==0.13.375", "mypy", "ruff", "pre-commit"]`
- Runtime dependencies stay in `manifest.json` `requirements`, which is empty.
- Every command runs as `uv run …`. Setup is a single `uv sync`, and `uv` downloads Python 3.14 itself.
- Dependency bumps are `uv lock --upgrade-package …`. Add a Renovate or Dependabot config with the `uv` ecosystem.
- Ruff handles both linting and formatting, replacing black, isort and flake8. Rules: `select = ["ALL"]` minus a short, documented ignore list, aligned with HA core's ruleset.

`translations/en.json` is the only strings file, because custom integrations don't use `strings.json`. Its keys are frozen in Phase 0. After that, only WP-C edits the `config`, `options` and `config_subentries` sections. Any other key change goes through the lead.

---

## 3. Frozen contracts (written in Phase 0, then read-only for streams)

```python
# api.py
CONTROLLER_PORT = 52001
MAX_BRIGHTNESS = 64
REQUEST_TIMEOUT = 3.0
CONTINUATION_TIMEOUT = 0.25


class DomestiaError(Exception): ...


class DomestiaConnectionError(DomestiaError): ...  # connect/timeout/OSError/empty reply


class DomestiaProtocolError(
    DomestiaError
): ...  # bad reply ("OK" expected, bad state header)


def pack_command(cmd: bytes) -> bytes: ...
def parse_state(
    response: bytes,
) -> dict[int, int]: ...  # relay (1-based) -> raw byte, all relays in reply
def normalize_brightness(raw: int, dimmable: bool) -> int: ...
async def read_response(
    reader: asyncio.StreamReader,
    min_length: int,
    first_timeout: float = REQUEST_TIMEOUT,
    continuation_timeout: float = CONTINUATION_TIMEOUT,
) -> bytes: ...


class DomestiaClient:
    def __init__(self, host: str, port: int = CONTROLLER_PORT) -> None: ...

    host: str

    async def get_state(self, highest_relay: int) -> dict[int, int]: ...
    async def turn_on(self, relay: int) -> None: ...
    async def turn_off(self, relay: int) -> None: ...
    async def set_brightness(self, relay: int, brightness: int) -> None: ...
    async def set_max_brightness(self, relay: int) -> None: ...


# brightness.py
def to_ha(raw: int) -> int: ...
def to_domestia(ha: int) -> int: ...
def describe_state(
    raw: int, dimmable: bool
) -> str: ...  # "off" | "on" | "on at 75% (brightness 48/64)"


# coordinator.py
@dataclass(frozen=True)
class LightConfig:
    subentry_id: str
    name: str
    relay: int
    dimmable: bool
    always_on: bool


class DomestiaCoordinator(
    DataUpdateCoordinator[dict[int, int]]
):  # relay -> normalized 0-64, configured relays only
    client: DomestiaClient
    lights: dict[int, LightConfig]
    controller_device_id: str  # registry id of the controller device (via_device_id)

    def __init__(
        self,
        hass,
        entry: DomestiaConfigEntry,
        client: DomestiaClient,
        controller_device_id: str,
    ) -> None: ...


type DomestiaConfigEntry = ConfigEntry[DomestiaCoordinator]


# config_flow.py
def parse_lights_json(
    text: str,
) -> list[ConfigSubentryData]: ...  # raises InvalidLights(translation_key)
```

Translation keys frozen in Phase 0:
- config errors: `cannot_connect`, `invalid_lights_json`, `invalid_relay`, `duplicate_relay`, `name_required`
- aborts: `already_configured`, `reconfigure_successful`
- subentry abort: `already_configured`
- exceptions: `cannot_connect`, `command_failed`
- issue: `relay_not_reported`

---

## 4. Work packages

```
Phase 0 (lead, serial) ──► WP-A ┐
                          WP-B ┤
                          WP-C ├──► Phase 2 gate (lead, serial) ──► Phase 3 audit ──► Phase 4 hardware (user) ──► Phase 5 removal
                          WP-D ┤
                          WP-E ┘
```

Run each WP as its own subagent with `isolation: "worktree"`. File ownership is disjoint, so the merges are mechanical. Each prompt is the WP section below plus §1 and §3, and a reminder that the Go sources named in the WP are the behavioural spec and must be read in full. Every WP must leave `uv run pytest tests/<its files>` green in its worktree before it reports back.

### Phase 0 findings (binding for all WPs)

Phase 0 is done on branch `ha-integration`. HA 2026.10 differs from older tutorials and from parts of this plan:

- **Python:** `requires-python = ">=3.14.2"`, because HA 2026.10.4 needs it.
- **Flow schemas use `probatio`, not voluptuous:** `import probatio`, then `probatio.Schema`, `probatio.Required`, `probatio.Optional`, `probatio.All`, `probatio.Range`, `probatio.Coerce` and `probatio.In`. `add_suggested_values_to_schema` is typed for `probatio.Schema`.
- **`DeviceInfo(via_device=...)` is deprecated** (removed in 2027.8). Use `via_device_id=<device registry id>`. The controller device is therefore registered in `async_setup_entry` *before* the coordinator, and its id is passed to the coordinator (§3).
- **`ColorMode` import:** under `mypy --strict`, import it from `homeassistant.components.light.const`, because the package doesn't re-export it explicitly.
- **`@override`:** HA core uses `typing.override` on overridden methods and properties. Do the same.
- **Ruff:** `select = ["ALL"]` with the ignores in `pyproject.toml`. If a WP needs an additional ignore, it uses a line-level `# noqa: <code>` with a reason, not a new global ignore.
- **Commands every WP must pass before reporting back:**
  ```sh
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy
  uv run pytest
  ```

### Phase 0: skeleton and contracts (lead, serial, about 1 h)

1. Create the §2 layout. Write `const.py`, `manifest.json`, `hacs.json`, `pyproject.toml`, `uv.lock` (via `uv lock`), `.python-version`, `.pre-commit-config.yaml`, and the full `translations/en.json` with every frozen key.
2. Write the §3 signatures with a **minimal working body** so downstream WPs can run real HA tests early:
   - `api.py`: the client is functional but naive (no continuation logic).
   - `coordinator.py`: poll and normalize only.
   - `light.py`: on/off only.
   - `config_flow.py`: a single user step.
3. Write `tests/conftest.py` with:
   - `mock_client`: patches `custom_components.domestia.DomestiaClient` and `...config_flow.DomestiaClient`. It is an `AsyncMock` whose `get_state` returns `{1: 64, 2: 32, 3: 0}`.
   - `mock_config_entry`: host `192.168.1.2`, with subentries relay 1 always_on, relay 2 dimmable "Garden", and relay 3 non-dimmable "Hall".
   - `init_integration`, a helper fixture.
4. Write one smoke test, `test_init.py::test_setup_unload`, and get it green:
   ```sh
   uv sync
   uv run pytest -q
   ```
5. Commit the skeleton on branch `ha-integration`. All WPs branch from it.
6. After this commit, **`tests/conftest.py` and `const.py` are read-only**. WP-specific fixtures live in that WP's own test files. A WP that needs a shared change asks the lead.

### WP-A: protocol client (`api.py`, `tests/test_api.py`)

Spec: `domestia/client.go`, `domestia/light.go`, `domestia/client_test.go`, `domestia/light_test.go`.

- Implement §1.4.8 exactly. Map `OSError` and `TimeoutError` to `DomestiaConnectionError`, and wrong replies to `DomestiaProtocolError`.
- Close the writer in `finally`, then `await writer.wait_closed()` with errors suppressed.
- Tests:
  - every Go case, ported (see §5)
  - a `read_response` fake: an `asyncio.StreamReader()` fed by a task that does `feed_data(chunk)` and `await asyncio.sleep(0.02)` per chunk, then `feed_eof()`. This replaces `net.Pipe`.
  - a timeout case: no EOF and no data, with `first_timeout=0.05`, raises.
  - end-to-end tests against `asyncio.start_server("127.0.0.1", 0)`:
    - the frame bytes received for each command
    - an `OK` reply passes and a `NO` reply raises `DomestiaProtocolError`
    - a refused connection raises `DomestiaConnectionError`
    - concurrent calls are serialized: the fake server asserts at most one open connection at a time
- No HA imports in `api.py`; the tests assert this by importing the file standalone.

### WP-B: brightness, coordinator, repair issue (`brightness.py`, `coordinator.py`, `tests/test_brightness.py`, `tests/test_coordinator.py`)

Spec: `bridge/light_state.go`, `bridge/light_state_test.go`, and `bridge/bridge.go` (`Run`, `handleController*`, `publishLightState`).

- Implement §1.4.1, §1.4.6, §1.4.7 and §1.4.9. `lights` is built from `entry.subentries` filtered on `SUBENTRY_TYPE_LIGHT`.
- `update_interval = timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT))`.
- Use the debouncer from §1.1.
- Log "Light state changed" at info with `describe_state` when a value differs from `self.data`.
- Tests use `mock_client` and `freezer` for interval ticks. They cover:
  - 3-strike availability: 2 failures stay available with old data, the 3rd makes it unavailable, and recovery comes back on the next success
  - a failure with no prior data raises immediately, so setup ends in `ConfigEntryNotReady`
  - always_on enforcement: calls made, reported value is 64
  - non-dimmable dimmed reset
  - enforcement errors don't fail the update
  - `relay_not_reported` issue created and then cleared (use the `issue_registry` fixture)
  - normalization of a non-dimmable 1

### WP-C: config, reconfigure, options and subentry flows (`config_flow.py`, the flow sections of `translations/en.json`, `tests/test_config_flow.py`)

Spec: `config/configuration.go`, `config/configuration_test.go`, and `go_domestia/config.yaml` (for the fields).

- **`user` step:**
  - fields: host (`TextSelector`), model (`SelectSelector` over `MODELS`, optional)
  - `async_set_unique_id(host)` and `_abort_if_unique_id_configured()`
  - test-before-configure: `DomestiaClient(host).get_state(1)`, with `cannot_connect` recoverable. Use `1`, not `0`: a minimum length of 3 could accept a split header-only reply, which `parse_state` rejects.
  - then the `lights` step
- **`lights` step:** an optional multiline JSON field. Empty means no lights. Run `parse_lights_json`, then create the entry with subentries. Validation rules:
  - the value must be a JSON array of objects
  - `relay` is an int from 1 to 255
  - relays must be unique
  - `name` is required unless `always_on`
  - `dimmable` and `always_on` are optional bools
  - names of always_on lights may repeat
- **`reconfigure` step:**
  - covers host and model, with the connection re-tested
  - Don't use `_abort_if_unique_id_mismatch`, because the host is the id. Instead:
    1. Abort with `already_configured` if another entry already has the new host.
    2. Call `hass.config_entries.async_update_entry(entry, data=..., unique_id=new_host)`.
    3. Return `async_abort(reason="reconfigure_successful")`. The update listener reloads (§1.2).
  - Subentry reconfigure works the same way: `async_update_subentry(entry, subentry, title=..., data=..., unique_id=str(relay))`, then abort.
- **Options flow:** `scan_interval`, a number of at least 0.25.
- **`LightSubentryFlow`:**
  - `user` step: fields name, relay, dimmable, always_on. Sets `unique_id=str(relay)`, so a duplicate relay aborts. Name is required unless always_on.
  - `reconfigure` step: a relay change must not collide with another subentry.
- 100% coverage of this module (Bronze `config-flow-test-coverage`), including every error → recovery path.

### WP-D: setup, entity, light platform, diagnostics (`__init__.py`, `entity.py`, `light.py`, `diagnostics.py`, `tests/test_init.py`, `tests/test_light.py`, `tests/test_diagnostics.py`)

Spec: `bridge/bridge.go` (`executeCommand`), `config/light.go`, `config/light_test.go`.

- **Setup and unload (`__init__.py`):**
  - `async_setup_entry`:
    1. Create the client and coordinator.
    2. Call `await coordinator.async_config_entry_first_refresh()` (failure → `ConfigEntryNotReady`, `test-before-setup`).
    3. Set `entry.runtime_data`, forward to `Platform.LIGHT`, and add the update listener for reload.
  - Unload with `async_unload_platforms`.
  - `async_remove_config_entry_device` returns True only for light devices whose subentry no longer exists (`stale-devices`).
- **Entities:**
  - `light.py` creates one `DomestiaLight` per non-always_on light, using `async_add_entities([...], config_subentry_id=light.subentry_id)` once per subentry.
  - Color mode: `ColorMode.BRIGHTNESS` if dimmable, otherwise `ONOFF`.
  - `is_on` is `raw > 0`. `brightness` is `to_ha(raw)`.
  - `available` requires `super().available` and the relay being in `coordinator.data`.
  - Commands follow §1.4.2–§1.4.4.
  - `PARALLEL_UPDATES = 1`.
- **Device info** follows §1.3 (`devices` rule).
- **Diagnostics:**
  - contents: entry data with the host redacted via `async_redact_data`, options, the lights list, `coordinator.data`, `last_update_success`, and the failure count
  - tested with a syrupy snapshot
- **Tests:**
  - setup, unload and not-ready
  - entities created with the correct entity_ids, and none for always_on
  - device registry fields (manufacturer, model, via_device, and model `None` when unset)
  - turn_on/turn_off/brightness call sequences: brightness 1 → `set_brightness(…, 1)`, brightness 255 → 64, non-dimmable → `set_max_brightness`
  - command error raises `HomeAssistantError` with the translation key
  - refresh after a command
  - adding or removing a subentry reloads and adds or removes the entity and device
  - stale-device removal

### WP-E: docs, CI, metadata (`README.md` integration section, `quality_scale.yaml`, `.github/workflows/integration.yml`, `AGENTS.md` integration section)

This WP doesn't depend on the code, so it can start right after Phase 0.

- **README:** a new "Home Assistant integration" section that covers every Bronze, Silver and Gold docs rule:
  - high-level description
  - HACS installation (custom repository) and step-by-step setup
  - setup fields (installation parameters)
  - options (configuration parameters)
  - lights import format
  - removal
  - data update (2 s polling, 3-strike availability)
  - supported devices (DMC-012-003, DMC-008-001)
  - supported functions
  - known limitations: no discovery; the controller reports no names, so lights are configured by hand; a single TCP client at a time
  - use cases and an automation example
  - troubleshooting: `cannot_connect`, the repair issue, port 52001 reachability, debug logging snippet
  - the migration runbook from §6

  The Go/add-on docs stay until Phase 5.
- **`quality_scale.yaml`:** every rule from §7 with its status and an exemption comment.
- **CI workflow:** `astral-sh/setup-uv` (pinned by SHA like the existing actions, with cache enabled), then `uv sync --locked`, then each step below via `uv run`:
  - `ruff check` and `ruff format --check`
  - `mypy --strict custom_components/domestia`
  - `pytest --cov=custom_components.domestia --cov-report=term-missing --cov-report=json --cov-fail-under=95`
  - a per-module check: a ~10-line Python step that reads `coverage.json` and fails if any file is below 95%, because `--cov-fail-under` only checks the total
  - `home-assistant/actions/hassfest@master`
  - `hacs/action@main` (category `integration`)

  Keep `ci.yml` (Go) untouched.
- **AGENTS.md:** add the Python commands (`uv sync`, `uv run pytest`, `uv run ruff check`/`format`, `uv run mypy`, `uv run pre-commit install`) and the architecture of `custom_components/domestia`. Keep the Go sections until Phase 5.

### Phase 2: integration gate (lead, serial)

1. Merge WP-A to WP-E into `ha-integration`. Replace the Phase 0 stubs with the WP versions; there should be no conflicts.
2. Run the full `uv run pytest --cov ... --cov-fail-under=95`, then check the **per-module** coverage in `term-missing`, since Silver requires >95% per module.
3. Run `ruff` and `mypy --strict` clean, and the Go `go test ./...` still green.
4. Run hassfest locally:
   ```sh
   docker run --rm -v "$PWD/custom_components":/github/workspace/custom_components ghcr.io/home-assistant/hassfest
   ```
   Or push the branch and let CI run it.
5. Run `/code-review` on the branch, then the `code-simplifier` agent on the Python sources, then re-run step 2.

### Phase 3: quality-scale audit (one subagent, read-only)

The subagent walks every rule in §7 against the code and tests and fixes `quality_scale.yaml` statuses. It reports any rule marked `done` without evidence.

---

## 5. Test migration (all 16 Go test functions)

| Go test | Python test | Notes |
|---|---|---|
| `domestia.TestParseState` | `test_api.py::test_parse_state` + `test_coordinator.py::test_unconfigured_relays_skipped` | `parse_state` now returns all relays. Filtering and normalization move to the coordinator, so the assertion splits across the two tests. |
| `domestia.TestParseStateRejectsMalformedResponses` | `test_api.py::test_parse_state_rejects_malformed` (parametrized: empty, `OK`, header only, wrong first byte) | Same 4 cases. |
| `domestia.TestReadResponse` | `test_api.py::test_read_response` (parametrized, the same 5 cases) | `net.Pipe` replaced by a fed `StreamReader`. |
| `domestia.TestReadResponseEmpty` | `test_api.py::test_read_response_empty` | EOF without data raises `DomestiaConnectionError`. |
| `domestia.TestNewLightBrightness` | `test_api.py::test_normalize_brightness` (the same 6 cases) | |
| `domestia.TestPackCommand` | `test_api.py::test_pack_command` | Read the Go body for the exact frames. |
| `bridge.TestOrphanedTopics` | **dropped** | Retained MQTT discovery topics no longer exist. The registry handles renames. |
| `bridge.TestDomestiaBrightness` | `test_brightness.py::test_to_domestia` (the same 8 cases) | |
| `bridge.TestHomeAssistantBrightness` | `test_brightness.py::test_to_ha` (the same 5 cases) | |
| `bridge.TestBrightnessRoundTrip` | `test_brightness.py::test_round_trip` (0–64) | |
| `bridge.TestCmdAction` | **dropped**, behaviour covered by `test_light.py` turn_on/turn_off tests | HA calls typed methods, so there is no `"ON"`/`"OFF"` string to parse. |
| `config.TestLoadConfigurationModel` | `test_config_flow.py::test_user_model` (valid model stored, omitted → `None`) | Invalid values are now rejected by the selector schema, so the test asserts that the schema rejects them. |
| `config.TestLoadConfigurationRefreshFrequency` | `test_config_flow.py::test_options_scan_interval` (default, 0.25 ok, 0.24/0/−1 rejected) | Unit changes from ms to s. |
| `config.TestLoadConfigurationLights` | `test_config_flow.py::test_parse_lights_json` (parametrized) + subentry duplicate/name tests | Ported: valid, relay 0, relay missing, duplicate relay, empty name, null light, always-on names may repeat. **Dropped:** `+`, `#`, `/` and entity-id collision, because names are no longer MQTT topics and HA de-duplicates entity_ids itself. |
| `config.TestHomeAssistantRegistrationJSONRegistersDevice` | `test_light.py::test_device_info` | Asserts the registry device: name, manufacturer, model, identifiers, via_device. |
| `config.TestHomeAssistantRegistrationJSONOmitsEmptyModelAndVersion` | `test_light.py::test_device_info_without_model` | `sw_version` is dropped: it was the bridge version, and the controller reports no firmware. |

The new tests have no Go equivalent: 3-strike availability, policy enforcement, the repair issue, the flows, setup/unload, diagnostics, end-to-end TCP framing, and lock serialization.

The Go convention "no fakes, pure functions" still applies to `api.py` and `brightness.py`. For HA-level tests, the standard pattern is a patched `AsyncMock` client, which is unavoidable there.

---

## 6. Cutover runbook (user, on real hardware: Phase 4)

1. Note the current entity_ids, e.g. `light.garden`.
2. Install the integration by **copying** `custom_components/domestia` to `/config/custom_components/`, then restart HA.
   - This is the primary path for the trial. HACS installs from the latest GitHub release when one exists, and this repo's existing releases are add-on releases without `custom_components/`.
   - HACS becomes the install path only after Phase 5 publishes an integration release.
3. Stop the **Domestia Bridge** add-on and disable "Start on boot". It publishes `offline`.
4. Remove the MQTT entities so their entity_ids are freed:
   1. Clear the retained discovery configs:
      ```sh
      mosquitto_pub -h <broker> -u … -P … -r -n -t homeassistant/light/<entity>/config
      ```
      Do this for each light, plus its `domestia/light/<entity>/state` topic.
   2. Confirm the devices disappeared under MQTT → Devices. Delete any leftovers.
5. Go to Settings → Devices & services → Add integration → Domestia. Enter the host and model, then paste the `lights` array from the add-on configuration.
6. Check that entity_ids match step 1 and rename any that don't. History continues because the recorder keys on entity_id. Check automations and dashboards.
7. Test on/off, dimming, a non-dimmable light, and an always_on relay (switch it off at a wall panel and confirm it comes back). Unplug the controller and confirm entities go unavailable after about 3 polls and recover afterwards.
8. Run for about a week, then uninstall the add-on.

---

## 7. Quality scale status (target)

`exempt` entries need a comment in `quality_scale.yaml`.

**Bronze**

| Rule | Status |
|---|---|
| action-setup, docs-actions | exempt: no custom actions |
| docs-conditions, docs-triggers | exempt: none provided |
| appropriate-polling | done: 2 s default. The controller has no push, and the user can configure the interval. |
| brands | done: local `brand/` folder |
| common-modules | done: `coordinator.py`, `entity.py` |
| config-flow, config-flow-test-coverage | done (WP-C) |
| dependency-transparency | exempt: no external requirements. The protocol lives in `api.py` without HA imports and can be extracted to PyPI if this ever goes to core. |
| docs-high-level-description, docs-installation-instructions, docs-removal-instructions | done (WP-E) |
| entity-event-setup | done: `CoordinatorEntity` |
| entity-unique-id, has-entity-name, runtime-data, test-before-configure, test-before-setup, unique-config-entry | done |

**Silver**

| Rule | Status |
|---|---|
| action-exceptions | done: `HomeAssistantError` with a translation key |
| config-entry-unloading | done |
| docs-configuration-parameters, docs-installation-parameters | done |
| entity-unavailable, log-when-unavailable | done: 3-strike coordinator |
| integration-owner | done: `codeowners` |
| parallel-updates | done: `PARALLEL_UPDATES = 1` |
| reauthentication-flow | exempt: the controller has no authentication |
| test-coverage | done: >95% per module, enforced in CI |

**Gold**

| Rule | Status |
|---|---|
| devices | done |
| diagnostics | done |
| discovery, discovery-update-info | **todo: verify on hardware.** Check the controller's MAC OUI and DHCP hostname in the router. If they are stable, add a `dhcp` matcher to `manifest.json` with `async_step_dhcp` (which also gives discovery-update-info by updating the host). Otherwise mark exempt. |
| docs-data-update, docs-examples, docs-known-limitations, docs-supported-devices, docs-supported-functions, docs-troubleshooting, docs-use-cases | done (WP-E) |
| dynamic-devices | exempt: the controller doesn't report which relays exist. Devices come only from user-added subentries, which apply on reload. |
| entity-category, entity-device-class, entity-disabled-by-default | exempt: only primary light entities, and lights have no device class |
| entity-translations | exempt: entities use the device name (`name = None`) |
| exception-translations | done |
| icon-translations | exempt: default light icons |
| reconfiguration-flow | done |
| repair-issues | done: `relay_not_reported` |
| stale-devices | done: subentry removal plus `async_remove_config_entry_device` |

**Platinum (stretch, nearly free)**

| Rule | Status |
|---|---|
| async-dependency | done: pure asyncio |
| inject-websession | exempt: raw TCP, no HTTP |
| strict-typing | done if `mypy --strict` passes in CI |

Expected result: Silver complete, and Gold complete apart from the exempt rules.

---

## 8. Phase 5: retire the bridge (only after Phase 4 succeeds)

This runs as one subagent.

1. **Delete the Go and add-on files:**
   - `main.go`, `bridge/`, `domestia/`, `config/`, `homeassistant/`
   - `go.mod`, `go.sum`, `Dockerfile`
   - `go_domestia/`, `repository.yaml`
   - `.github/workflows/ci.yml` Go/Docker jobs
   - `domestia.sample.json`

   Keep `domestia.json` local only; it is git-ignored and must never be committed.
2. **Replace the release pipeline.** `release.yml` becomes: bump `manifest.json` `version` and add a `CHANGELOG.md` section, which creates a GitHub release that HACS picks up. Update the `/release` skill and the AGENTS.md "Releasing" and "Changing configuration options" sections to match.
3. **Rewrite README and AGENTS.md** for the integration only. Optionally rename the repo (e.g. `ha-domestia`); GitHub redirects old URLs.
4. **Publish a final add-on release** whose changelog points users at the integration and the runbook.

---

## 9. Estimate

| Step | Wall time |
|---|---|
| Phase 0 | about 1 h, serial |
| WP-A to WP-E | about 1–2 h each, in parallel |
| Phase 2 and 3 | about 1–2 h |
| Phase 4 | an evening on the real controller |
| Phase 5 | about 1 h |
