# AGENTS.md

go-domestia connects a Domestia lighting controller (binary protocol over TCP port 52001) to Home Assistant. It is being migrated (see `MIGRATION_PLAN.md`):

- **Home Assistant integration** (`custom_components/domestia`, Python): the new path. HA talks to the controller directly, without MQTT. Targets HA 2026.11.1 and is installed through HA's built-in Marketplace or by copying the folder.
- **Legacy Go bridge and add-on**: bridges the controller to HA over MQTT discovery, shipped as a static Go binary in a `scratch` Docker image and as a Home Assistant add-on (`go_domestia/`). It stays working until Phase 5 of the plan, which removes it.

`domestia.json` is git-ignored and holds real controller IPs and MQTT credentials. Never read or commit it. Use `domestia.sample.json` as the template, and only `192.168.1.x` sample addresses in docs and tests.

## Home Assistant integration

### Commands

```sh
uv sync                                           # set up .venv (uv installs Python 3.14 itself)
uv run pytest                                     # all tests
uv run pytest tests/test_init.py::test_setup_unload  # single test
uv run ruff format .                              # format (CI: ruff format --check .)
uv run ruff check .                               # lint, select = ["ALL"]
uv run mypy                                       # strict, configured in pyproject.toml
uv run pre-commit install                         # ruff, mypy and uv lock --check on commit
```

Every command runs through `uv run`; there are no `requirements*.txt`. Bump dependencies with `uv lock --upgrade-package <name>` and commit `uv.lock`, since CI runs `uv sync --locked`. Runtime requirements belong in `manifest.json` (currently none).

CI (`.github/workflows/integration.yml`) runs ruff, mypy and pytest with at least 95% coverage per module, plus hassfest and the Marketplace (HACS) validator.

### Architecture

- **`api.py`**: `DomestiaClient`, a pure asyncio client with **no HA imports** (tests check this), so it can move to PyPI later. Every request opens a fresh TCP connection, serialized by an `asyncio.Lock`. Connecting, writing and waiting for the first reply bytes each have their own 3 s timeout; later reads wait at most 250 ms, and the buffer is capped at 258 bytes (the 3-byte header plus relay 255). Also `pack_command`, `parse_state`, `read_response`, `normalize_brightness` and the `DomestiaError` / `DomestiaConnectionError` / `DomestiaProtocolError` hierarchy. The wire protocol is the one described under the legacy `domestia/` package below.
- **`brightness.py`**: pure `to_ha`, `to_domestia` and `describe_state`.
- **`const.py`**: `DOMAIN`, `CONF_*`, `MODELS`, `SUBENTRY_TYPE_LIGHT`, scan interval defaults, `MAX_CONSECUTIVE_FAILURES`, `LOGGER`.
- **`coordinator.py`**: `DomestiaCoordinator` (`DataUpdateCoordinator[dict[int, int]]`, relay → normalized 0–64 for configured relays) and the `LightConfig` dataclass built from the entry's light subentries.
  - Failures 1 and 2 return the last good data; the 3rd consecutive `DomestiaError` raises `UpdateFailed`. With no data yet it raises at once, so setup ends in `ConfigEntryNotReady`.
  - Each poll enforces policy: `always_on` relays are forced back to full brightness (they have no entity), and a non-dimmable relay found dimmed is reset to max. The coordinator reports the enforced 64. Policy errors are warnings, never update failures.
  - Configured relays missing from a valid reply raise the `relay_not_reported` repair issue, deleted once they reappear.
  - The refresh debouncer (`cooldown=0.3, immediate=True`) coalesces the refreshes requested after commands.
- **`entity.py`**: `DomestiaEntity`, the `CoordinatorEntity` base with `device_info`.
- **`light.py`**: `DomestiaLight`, one per light subentry that is not `always_on`; setup removes the entity and device of a light switched to `always_on`. `PARALLEL_UPDATES = 1`. A turn-on sends every command (on, then max or the requested brightness) even if one fails, then raises the first error as `HomeAssistantError` with translation key `command_failed`. A refresh is requested in all cases.
- **`config_flow.py`**: the user step (host, model, connection test with `get_state(1)`), the optional `lights` import step (`parse_lights_json`, which takes the add-on's YAML list, indented or under `lights:`, or a JSON array), reconfigure and `LightSubentryFlow` (user, reconfigure).
- **`__init__.py`**: registers the controller device first, then creates the coordinator, runs the first refresh, sets `entry.runtime_data`, adds the update listener, and forwards to `Platform.LIGHT`. `async_remove_config_entry_device` allows removing devices whose subentry is gone.
- **`diagnostics.py`**: entry data with the host redacted, options, lights, coordinator data and failure count.
- **`translations/en.json`**: the only strings file (custom integrations have no `strings.json`). **`brand/`**: local icon and logo. **`quality_scale.yaml`**: the self-assessed status of every quality-scale rule; keep it in step with the code.

Data model: one config entry per controller (`unique_id = host`, `data = {host, model}`), and one subentry of type `"light"` per relay (`title` = name, or `Relay <n>` for a nameless always-on light, `unique_id = str(relay)`, `data = {relay, dimmable, always_on}`).

### Rules

- **Identity:** the light entity's `unique_id` and its device identifier `(DOMAIN, subentry_id)` are the **subentry_id**. Never key on the relay: rewiring a light to another relay through reconfigure must keep its entity, device and history. The controller device is `(DOMAIN, entry.entry_id)`. Changing any of these breaks users' entities.
- **One reload mechanism:** the update listener calls `async_schedule_reload`. Subentry add/remove, options and reconfigure all trigger it by updating the config entry or its subentries. Reconfigure steps call `async_update_entry` / `async_update_subentry`, then `async_abort(reason="reconfigure_successful")`. Never use `async_update_reload_and_abort`: the subentry version raises `ValueError` and the entry version double-reloads when a listener exists.
- **Brightness rounding:** port the Go formulas literally with `math.floor(x + 0.5)` and clamp. Never use `round()` (banker's rounding) or HA's `color_util` brightness helpers; both break the 0–64 round-trip test. `to_ha(raw) = min(floor(raw * 255/64 + 0.5), 255)`, `to_domestia(ha) = clamp(floor(ha * 64/255 + 0.5), 0, 64)`. A non-zero HA brightness is sent as at least 1. A non-dimmable relay reporting `1` is normalized to 64; for dimmable relays `1` is a real level.
- **Logging:** `LOGGER` from `const.py`, with `%s`-style lazy arguments and a capitalized message, e.g. `LOGGER.info("Light state changed: %s is %s", name, state)`.

### HA 2026.11 specifics

These differ from older tutorials:

- **Python ≥ 3.14.2** (`requires-python`), required by HA 2026.10 and later.
- **Flow schemas use `probatio`**, not voluptuous: `probatio.Schema`, `Required`, `Optional`, `All`, `Range`, `Coerce`, `In`. `add_suggested_values_to_schema` is typed for `probatio.Schema`.
- **`DeviceInfo(via_device=...)` is deprecated** (removed in 2027.8). Use `via_device_id=<device registry id>`, which is why the controller device is registered before the coordinator.
- Import `ColorMode` from `homeassistant.components.light.const` under `mypy --strict`.
- Use `typing.override` on overridden methods and properties, as HA core does.
- **Tests run on `pytest-homeassistant`**, HA core's official package (the community `pytest-homeassistant-custom-component` is deprecated). Import helpers from `pytest_homeassistant.common`. Custom integrations load by default, so there is no `enable_custom_integrations` fixture. `addopts = "-p no:respx"` must stay so HA's httpx alias loads first.
- The `pytest-homeassistant` version **is** the HA version it pins. Bump it together with the HA target (and `hacs.json`'s minimum when that changes).
- Ruff ignores are global only in `pyproject.toml`; anything else gets a line-level `# noqa: <code>` with a reason.

### Tests

Pure logic (`api.py` framing and parsing, `brightness.py`, `parse_lights_json`) is tested table-style with `pytest.mark.parametrize`, without HA, following the Go convention. HA-level tests use the fixtures in `tests/conftest.py`: `mock_client` (an autospecced `DomestiaClient` patched in `__init__` and `config_flow`), `mock_config_entry` (3 light subentries: `WARDROBE` relay 1 always_on, `GARDEN` relay 2 dimmable, `HALL` relay 3 non-dimmable) and `init_integration`. Import constants with `from .conftest import ...`. `conftest.py` is shared; add fixtures to your own test file. Diagnostics use syrupy snapshots, and time-based coordinator tests use `freezer`.

## Legacy Go bridge (removed in Phase 5)

### Commands

```sh
go build -o go-domestia .                 # build
go test ./...                             # all tests
go test ./bridge -run TestOrphanedTopics  # single test
go vet ./...
gofmt -l .                                # CI fails if this prints anything
CONFIG_PATH=domestia.json ./go-domestia   # run locally (CONFIG_PATH defaults to ./domestia.json)
docker build --build-arg VERSION=dev -t go-domestia .
```

CI (`.github/workflows/ci.yml`) runs gofmt, `go vet`, `go test`, and a multi-arch Docker build (arm64, arm/v7, amd64). The binary's version is injected with `-ldflags "-X main.version=<version>"` and reported to Home Assistant as the device `sw_version`.

### Architecture

Package dependencies: `main` → `bridge` → `domestia`, `config`, `homeassistant`; `domestia` → `config`; `config` → `homeassistant`.

- **`main.go`**: loads config, then runs `bridge.Run` in a restart loop with a 1s backoff. `Run` returns nil only when the context is cancelled (SIGINT/SIGTERM); any error restarts it.
- **`bridge/`**: the run loop polls the controller on a ticker, or immediately when a command arrives through `updateChannel`. The channel is buffered to 1 and written non-blocking so refreshes coalesce. State is published only when a relay's brightness changes (`relayToBrightness`).
  - Controller errors never return from `Run`. They are retried on the next tick, and after 3 consecutive failures the lights are marked unavailable. MQTT publish errors do return, which triggers a full restart.
  - Discovery registration and command subscriptions happen in the MQTT `OnConnectHandler` so they are redone after auto-reconnect. That handler runs on paho's goroutine: log errors there, never exit.
  - Each poll also enforces policy: `always_on` relays are forced back to full brightness (they are never registered with HA), and a non-dimmable relay found dimmed is reset to max.
  - On startup, `removeOrphanedConfigs` collects retained `homeassistant/light/+/config` messages for 2s and clears any that carry one of our `unique_id`s under a stale topic. This happens after a light is renamed.
- **`domestia/`**: every request opens a fresh TCP connection (3s timeout), serialized by a mutex. Frames are `0xff 0x00 0x00 <len> <cmd...> <checksum>`, where the checksum is the byte sum of cmd. Commands: `0x3c` get state, `0x0e` on, `0x0f` off, `0x10` brightness. Commands reply `"OK"`. Get-state replies `0xff`, then two status bytes, then one byte per relay (relay number = index + 1).
- **`homeassistant/`**: MQTT payload types and topic naming. The entity id is the light name lowercased with spaces replaced by `_`, and it forms the config, command, and state topics. One bridge-wide availability topic, `domestia/bridge/availability`, doubles as the MQTT Last Will. A graceful disconnect suppresses the will, so the bridge publishes `offline` itself on shutdown.
- **`config/`**: JSON loading and validation. `config.Light` builds the discovery payload: `unique_id` is `d_<relay>` and the device identifier is `domestia_relay_<relay>`. Changing either breaks existing entities in users' Home Assistant installs.

### Brightness

The controller scale is 0–`domestia.MaxBrightness` (64; a fully-on relay reads back 64). HA uses 0–255. Conversions in `bridge/light_state.go` round rather than truncate. A non-zero HA brightness is floored to 1 so it dims instead of switching off. A non-dimmable relay reports `1` when on, and `NewLight` (`domestia/light.go`) normalizes that to max. For dimmable relays, `1` is a real dim level.

### Tests

There are no MQTT or controller fakes. Testable logic is extracted into pure functions (`orphanedTopics`, the brightness conversions, `NewLight`, `packCommand`, config loading) and tested table-style. Follow that pattern rather than mocking clients.

### Changing configuration options

A new or changed add-on option has to be updated in all of these places:

- `config/configuration.go` (struct and validation)
- `go_domestia/config.yaml` (`options` and `schema`; HA validates against `schema` and writes `/data/options.json`)
- `go_domestia/translations/en.yaml`
- `go_domestia/DOCS.md`
- the README configuration table
- `domestia.sample.json`

For the integration, a new setup field or option goes in `config_flow.py`, `const.py`, `translations/en.json`, and the README integration section.

### Releasing

To release, bump `version` in `go_domestia/config.yaml` and add a matching `## <version>` section to `go_domestia/CHANGELOG.md`. The release workflow refuses to run without that section. Merging to `main` with a changed `config.yaml` triggers `release.yml`, which tests, pushes `ghcr.io/genzov/go-domestia-<arch>:<version>` and `:latest`, and creates a GitHub release from the changelog section. Already-tagged versions are skipped. Changelog entries are written for add-on users (behavior they will notice), not as commit summaries.

The integration has no release pipeline yet; Phase 5 replaces this one with a `manifest.json` version bump.

### Conventions

Logging uses `log/slog` with a capitalized message and key=value attributes, e.g. `slog.Info("Light state changed", "light", name, "state", s)`.

## Claude Code plugins

Enabled in `.claude/settings.json`. Prefer them over ad-hoc equivalents:

- **pyright-lsp**: use the LSP tool (definitions, references, diagnostics) for Python navigation and renames before grepping or hand-editing.
- **commit-commands**: use `/commit` and `/commit-push-pr` for commits and PRs.
- **code-review**: run `/code-review` on non-trivial changes before opening a PR or releasing.
- **code-simplifier**: run the `code-simplifier:code-simplifier` agent after substantial code edits.
- **claude-md-management**: run `/claude-md-management:revise-claude-md` after a session that taught something new; `claude-md-improver` for audits.

The project `release` skill (`/release`) is user-invoked only.
