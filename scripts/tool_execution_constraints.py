"""Helpers for `tool_execution_constraints.json` lookups."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

SCHEMA_VERSION = "1.0.0"
SUPPORTED_INVOCATION_KINDS = {
    "any",
    "default_scope",
    "explicit_paths",
}


def load_execution_constraints(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"invalid JSON in execution constraints file {path}: {error}"
        ) from error
    if not isinstance(data, dict):
        raise ValueError("execution constraints file must contain a JSON object")
    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            "execution constraints file must use schema_version " f"{SCHEMA_VERSION!r}"
        )
    products = data.get("products")
    if not isinstance(products, dict):
        raise ValueError("execution constraints file must include 'products'")
    return data


def environment_match_is_active(match: Dict[str, object]) -> bool:
    env_all_of = match.get("env_all_of", {})
    if env_all_of is None:
        env_all_of = {}
    if not isinstance(env_all_of, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in env_all_of.items()
    ):
        raise ValueError("match.env_all_of must be a JSON object of strings")
    return all(os.environ.get(key) == value for key, value in env_all_of.items())


def _invocation_kind_matches(configured: object, actual: str) -> bool:
    if configured is None or configured == "any":
        return True
    if not isinstance(configured, str):
        raise ValueError(
            "parallel_safety.applies_when.invocation_kind must be a string"
        )
    if configured not in SUPPORTED_INVOCATION_KINDS:
        raise ValueError(
            "parallel_safety.applies_when.invocation_kind must be one of "
            f"{sorted(SUPPORTED_INVOCATION_KINDS)!r}"
        )
    return configured == actual


def serial_execution_constraint_label(
    constraints: Dict[str, object],
    check_name: str,
    path_count: int,
    invocation_kind: str,
) -> str | None:
    if invocation_kind not in SUPPORTED_INVOCATION_KINDS:
        raise ValueError(
            f"unsupported invocation kind {invocation_kind!r}; expected one of "
            f"{sorted(SUPPORTED_INVOCATION_KINDS)!r}"
        )

    products = constraints.get("products", {})
    if not isinstance(products, dict):
        raise ValueError("execution constraints file must include 'products'")

    for product_name, product_entry in products.items():
        if not isinstance(product_entry, dict):
            continue
        sandboxes = product_entry.get("sandbox_technologies", {})
        if not isinstance(sandboxes, dict):
            continue
        for sandbox_name, sandbox_entry in sandboxes.items():
            if not isinstance(sandbox_entry, dict):
                continue
            environments = sandbox_entry.get("environments", {})
            if not isinstance(environments, dict):
                continue
            for environment_name, environment_entry in environments.items():
                if not isinstance(environment_entry, dict):
                    continue
                match = environment_entry.get("match", {})
                if not isinstance(match, dict):
                    continue
                if not environment_match_is_active(match):
                    continue
                tools = environment_entry.get("tools", {})
                if not isinstance(tools, dict):
                    continue
                tool_entry = tools.get(check_name)
                if not isinstance(tool_entry, dict):
                    continue
                parallel_safety = tool_entry.get("parallel_safety", {})
                if not isinstance(parallel_safety, dict):
                    continue
                if parallel_safety.get("status") != "unsafe":
                    continue
                applies_when = parallel_safety.get("applies_when", {})
                if applies_when is None:
                    applies_when = {}
                if not isinstance(applies_when, dict):
                    continue
                configured_kind = applies_when.get("invocation_kind")
                if not _invocation_kind_matches(configured_kind, invocation_kind):
                    continue
                path_count_gte = applies_when.get("path_count_gte", 1)
                if not isinstance(path_count_gte, int) or path_count_gte < 1:
                    raise ValueError(
                        "parallel_safety.applies_when.path_count_gte must be "
                        "a positive integer"
                    )
                if path_count < path_count_gte:
                    continue
                preferred_workaround = parallel_safety.get(
                    "preferred_workaround",
                    {},
                )
                if not isinstance(preferred_workaround, dict):
                    continue
                if preferred_workaround.get("mode") != "serial_explicit_paths":
                    continue
                return f"{product_name}/{sandbox_name}/{environment_name}"
    return None
