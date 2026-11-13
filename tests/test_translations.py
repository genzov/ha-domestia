"""Check that the flows and translations/en.json agree.

pytest-homeassistant does not validate flow translations the way Home
Assistant core's own test suite does, so missing or stale strings would
otherwise only show up in the UI.
"""

import ast
import inspect
import json
import string
from pathlib import Path
from typing import Any

import probatio
import pytest

from custom_components.domestia import config_flow
from custom_components.domestia.config_flow import (
    LIGHT_SCHEMA,
    LIGHTS_SCHEMA,
    USER_SCHEMA,
)
from custom_components.domestia.const import SUBENTRY_TYPE_LIGHT

TRANSLATIONS: dict[str, Any] = json.loads(
    (Path(config_flow.__file__).parent / "translations" / "en.json").read_text(
        encoding="utf-8"
    )
)
SUBENTRY = TRANSLATIONS["config_subentries"][SUBENTRY_TYPE_LIGHT]


def _keys(schema: probatio.Schema) -> set[str]:
    return {str(key) for key in schema.schema}


@pytest.mark.parametrize(
    ("step", "fields"),
    [
        (TRANSLATIONS["config"]["step"]["user"], _keys(USER_SCHEMA)),
        (TRANSLATIONS["config"]["step"]["lights"], _keys(LIGHTS_SCHEMA)),
        (TRANSLATIONS["config"]["step"]["reconfigure"], _keys(USER_SCHEMA)),
        (TRANSLATIONS["options"]["step"]["init"], {"scan_interval"}),
        (SUBENTRY["step"]["user"], _keys(LIGHT_SCHEMA)),
        (SUBENTRY["step"]["reconfigure"], _keys(LIGHT_SCHEMA)),
    ],
    ids=[
        "user",
        "lights",
        "reconfigure",
        "options",
        "light user",
        "light reconfigure",
    ],
)
def test_step_fields_translated(step: dict[str, Any], fields: set[str]) -> None:
    """Every form field has a label and a description, and nothing stale."""
    assert set(step["data"]) == fields
    assert set(step["data_description"]) == fields


def _string_args(node: ast.AST, call_name: str) -> set[str]:
    """Return the string literals passed to calls of call_name below node."""
    return {
        arg.value
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == call_name
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }


def _literals(node: ast.AST, *, keyword: str | None = None) -> set[str]:
    """Return the abort reasons (keyword) or base error strings below node."""
    found: set[str] = set()
    for child in ast.walk(node):
        if keyword is not None and isinstance(child, ast.keyword):
            if child.arg == keyword and isinstance(child.value, ast.Constant):
                found.add(child.value.value)
        elif (
            keyword is None
            and isinstance(child, ast.Assign)
            and isinstance(child.targets[0], ast.Subscript)
            and isinstance(child.value, ast.Constant)
        ):
            found.add(child.value.value)
    return found


MODULE = ast.parse(inspect.getsource(config_flow))
DEFINITIONS = {
    node.name: node
    for node in MODULE.body
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
}


def test_config_errors_and_aborts_translated() -> None:
    """Errors and aborts of the config flow exist in the translations."""
    errors = (
        _string_args(DEFINITIONS["parse_lights_json"], "InvalidLightsError")
        | _string_args(DEFINITIONS["_parse_light"], "InvalidLightsError")
        | _string_args(DEFINITIONS["_light_subentry"], "InvalidLightsError")
        | _literals(DEFINITIONS["DomestiaConfigFlow"])
    )
    # _abort_if_unique_id_configured aborts with already_configured.
    aborts = _literals(DEFINITIONS["DomestiaConfigFlow"], keyword="reason") | {
        "already_configured"
    }

    assert errors
    assert errors <= set(TRANSLATIONS["config"]["error"])
    assert aborts <= set(TRANSLATIONS["config"]["abort"])


def test_subentry_errors_and_aborts_translated() -> None:
    """Errors and aborts of the light subentry flow exist in the translations."""
    errors = _string_args(
        DEFINITIONS["_light_from_input"], "InvalidLightsError"
    ) | _string_args(DEFINITIONS["_light_subentry"], "InvalidLightsError")
    aborts = _literals(DEFINITIONS["LightSubentryFlow"], keyword="reason")

    assert errors
    assert errors <= set(SUBENTRY["error"])
    assert aborts <= set(SUBENTRY["abort"])


def _translated_calls(section: str) -> list[tuple[str, set[str]]]:
    """Return (translation key, placeholder names) of calls into a section.

    Exceptions are raised with translation_key=...; repair issues are created
    with async_create_issue(..., translation_key=...).
    """
    found = []
    for path in Path(config_flow.__file__).parent.glob("*.py"):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if (name == "async_create_issue") != (section == "issues"):
                continue
            keywords = {kw.arg: kw.value for kw in call.keywords}
            key = keywords.get("translation_key")
            if not isinstance(key, ast.Constant):
                continue
            placeholders = keywords.get("translation_placeholders")
            names = (
                {k.value for k in placeholders.keys if isinstance(k, ast.Constant)}
                if isinstance(placeholders, ast.Dict)
                else set()
            )
            found.append((key.value, names))
    return found


def _fields(*texts: str) -> set[str]:
    return {
        field
        for text in texts
        for _, field, _, _ in string.Formatter().parse(text)
        if field
    }


def test_exceptions_translated() -> None:
    """Every translated exception exists, with exactly its placeholders."""
    calls = _translated_calls("exceptions")
    assert {key for key, _ in calls} == {"cannot_connect", "command_failed"}
    for key, placeholders in calls:
        message = TRANSLATIONS["exceptions"][key]["message"]
        assert _fields(message) == placeholders, key


def test_issues_translated() -> None:
    """Every repair issue exists, with exactly its placeholders."""
    calls = _translated_calls("issues")
    assert {key for key, _ in calls} == {"relay_not_reported"}
    for key, placeholders in calls:
        issue = TRANSLATIONS["issues"][key]
        assert _fields(issue["title"], issue["description"]) == placeholders, key
