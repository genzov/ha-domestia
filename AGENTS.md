# AGENTS.md

This repository holds **Domestia**, a Home Assistant custom integration (`custom_components/domestia`, Python) that connects a Domestia lighting controller (binary protocol over TCP port 52001) directly to Home Assistant. It targets HA 2026.11.1 and is installed through HA's built-in Marketplace (formerly HACS) or by copying the folder.

Use only `192.168.1.x` sample addresses in docs and tests.

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

- **`api.py`**: `DomestiaClient`, a pure asyncio client with **no HA imports** (tests check this), so it can move to PyPI later. Every request opens a fresh TCP connection, serialized by an `asyncio.Lock`. Connecting, writing and waiting for the first reply bytes each have their own 3 s timeout; later reads wait at most 250 ms, and the buffer is capped at 258 bytes (the 3-byte header plus relay 255). Also `pack_command`, `parse_state`, `read_response`, `normalize_brightness` and the `DomestiaError` / `DomestiaConnectionError` / `DomestiaProtocolError` hierarchy. Wire protocol: frames are `0xff 0x00 0x00 <len> <cmd...> <checksum>`, where the checksum is the byte sum of cmd. Commands: `0x3c` get state, `0x0e` on, `0x0f` off, `0x10` brightness. Commands reply `"OK"`. Get-state replies `0xff`, then two status bytes, then one byte per relay (relay number = index + 1).
- **`brightness.py`**: pure `to_ha`, `to_domestia` and `describe_state`.
- **`const.py`**: `DOMAIN`, `CONF_*`, `MODELS`, `SUBENTRY_TYPE_LIGHT`, scan interval defaults, `MAX_CONSECUTIVE_FAILURES`, `LOGGER`.
- **`coordinator.py`**: `DomestiaCoordinator` (`DataUpdateCoordinator[dict[int, int]]`, relay → normalized 0–64 for configured relays) and the `LightConfig` dataclass built from the entry's light subentries.
  - Failures 1 and 2 return the last good data; the 3rd consecutive `DomestiaError` raises `UpdateFailed`. With no data yet it raises at once, so setup ends in `ConfigEntryNotReady`.
  - Each poll enforces policy: `always_on` relays are forced back to full brightness (they have no entity), and a non-dimmable relay found dimmed is reset to max. The coordinator reports the enforced 64. Policy errors are warnings, never update failures.
  - Configured relays missing from a valid reply raise the `relay_not_reported` repair issue, deleted once they reappear.
  - The refresh debouncer (`cooldown=0.3, immediate=True`) coalesces the refreshes requested after commands.
- **`entity.py`**: `DomestiaEntity`, the `CoordinatorEntity` base with `device_info`.
- **`light.py`**: `DomestiaLight`, one per light subentry that is not `always_on`; setup removes the entity and device of a light switched to `always_on`. `PARALLEL_UPDATES = 1`. A turn-on sends every command (on, then max or the requested brightness) even if one fails, then raises the first error as `HomeAssistantError` with translation key `command_failed`. A refresh is requested in all cases.
- **`config_flow.py`**: the user step (host, model, connection test with `get_state(1)`), reconfigure and `LightSubentryFlow` (user, reconfigure).
- **`__init__.py`**: registers the controller device first, then creates the coordinator, runs the first refresh, sets `entry.runtime_data`, adds the update listener, and forwards to `Platform.LIGHT`. `async_remove_config_entry_device` allows removing devices whose subentry is gone.
- **`diagnostics.py`**: entry data with the host redacted, options, lights, coordinator data and failure count.
- **`translations/`**: `en.json` is the source strings file (custom integrations have no `strings.json`); `nl.json` and `fr.json` mirror its keys and placeholders exactly, and `tests/test_translations.py` fails when they drift. **`brand/`**: local icon and logo. **`quality_scale.yaml`**: the self-assessed status of every quality-scale rule; keep it in step with the code.

Data model: one config entry per controller (`unique_id = host`, `data = {host, model}`), and one subentry of type `"light"` per relay (`title` = name, or `Relay <n>` for a nameless always-on light, `unique_id = str(relay)`, `data = {relay, dimmable, always_on}`).

### Rules

- **Identity:** the light entity's `unique_id` and its device identifier `(DOMAIN, subentry_id)` are the **subentry_id**. Never key on the relay: rewiring a light to another relay through reconfigure must keep its entity, device and history. The controller device is `(DOMAIN, entry.entry_id)`. Changing any of these breaks users' entities.
- **One reload mechanism:** the update listener calls `async_schedule_reload`. Subentry add/remove, options and reconfigure all trigger it by updating the config entry or its subentries. Reconfigure steps call `async_update_entry` / `async_update_subentry`, then `async_abort(reason="reconfigure_successful")`. Never use `async_update_reload_and_abort`: the subentry version raises `ValueError` and the entry version double-reloads when a listener exists.
- **Brightness rounding:** use `math.floor(x + 0.5)` and clamp. Never use `round()` (banker's rounding) or HA's `color_util` brightness helpers; both break the 0–64 round-trip test. `to_ha(raw) = min(floor(raw * 255/64 + 0.5), 255)`, `to_domestia(ha) = clamp(floor(ha * 64/255 + 0.5), 0, 64)`. A non-zero HA brightness is sent as at least 1. A non-dimmable relay reporting `1` is normalized to 64; for dimmable relays `1` is a real level.
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

Pure logic (`api.py` framing and parsing, `brightness.py`) is tested table-style with `pytest.mark.parametrize`, without HA. HA-level tests use the fixtures in `tests/conftest.py`: `mock_client` (an autospecced `DomestiaClient` patched in `__init__` and `config_flow`), `mock_config_entry` (3 light subentries: `WARDROBE` relay 1 always_on, `GARDEN` relay 2 dimmable, `HALL` relay 3 non-dimmable) and `init_integration`. Import constants with `from .conftest import ...`. `conftest.py` is shared; add fixtures to your own test file. Diagnostics use syrupy snapshots, and time-based coordinator tests use `freezer`.

## Changing setup fields

A new or changed setup field goes in `config_flow.py`, `const.py`, `translations/en.json`, `translations/nl.json`, `translations/fr.json`, and the README. Update `diagnostics.py` redaction if the field is sensitive, and `quality_scale.yaml` if a rule's status changes.

## Releasing

Run `/release`. To release by hand, bump `version` in `custom_components/domestia/manifest.json` and add a matching `## <version>` section to `CHANGELOG.md`. Pushing that to `main` triggers `release.yml`, which checks the changelog section (`.github/scripts/release-notes.sh`), runs the integration checks (it calls `integration.yml`), tags the bare version and creates a GitHub release from the section. The Marketplace offers the latest release as an update. Already-tagged versions are skipped. Changelog entries are written for users (behavior they will notice), not as commit summaries.

## Claude Code plugins

Enabled in `.claude/settings.json`. Prefer them over ad-hoc equivalents:

- **pyright-lsp**: use the LSP tool (definitions, references, diagnostics) for Python navigation and renames before grepping or hand-editing.
- **commit-commands**: use `/commit` and `/commit-push-pr` for commits and PRs.
- **code-review**: run `/code-review` on non-trivial changes before opening a PR or releasing.
- **code-simplifier**: run the `code-simplifier:code-simplifier` agent after substantial code edits.
- **claude-md-management**: run `/claude-md-management:revise-claude-md` after a session that taught something new; `claude-md-improver` for audits.

The project `release` skill (`/release`) is user-invoked only.
