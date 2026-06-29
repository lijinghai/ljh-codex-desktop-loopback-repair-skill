#!/usr/bin/env python3
"""Repair a CC-Switch Codex provider that lost its upstream base_url."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


def emit(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=True, indent=2))
    raise SystemExit(code)


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    if not table_exists(conn, name):
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({qident(name)})")]


def parse_json_object(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    text = str(value).strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except Exception:
        return {"config": text}
    return dict(obj) if isinstance(obj, dict) else {}


def config_from_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        cfg = value.get("config")
        return cfg if isinstance(cfg, str) else ""
    obj = parse_json_object(value)
    if isinstance(obj.get("config"), str):
        return obj["config"]
    return str(value) if isinstance(value, str) else ""


def toml_quote(value: str) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def extract_toml_string(config: str, key: str) -> str | None:
    if not config:
        return None
    pattern = re.compile(r"(?m)^\s*" + re.escape(key) + r"\s*=\s*[\"']([^\"']+)[\"']")
    match = pattern.search(config)
    return match.group(1) if match else None


def extract_base_url(config: str) -> str | None:
    return extract_toml_string(config, "base_url")


def is_local_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def set_root_key(config: str, key: str, rendered_value: str) -> str:
    lines = (config or "").splitlines()
    first_section = len(lines)
    for idx, line in enumerate(lines):
        if line.strip().startswith("["):
            first_section = idx
            break
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    for idx in range(first_section):
        if key_re.match(lines[idx]):
            lines[idx] = f"{key} = {rendered_value}"
            return "\n".join(lines).strip() + "\n"
    lines.insert(first_section, f"{key} = {rendered_value}")
    return "\n".join(lines).strip() + "\n"


def ensure_section(config: str, section: str) -> str:
    lines = (config or "").splitlines()
    target = f"[{section}]"
    if any(line.strip() == target for line in lines):
        return "\n".join(lines).strip() + "\n"
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(target)
    return "\n".join(lines).strip() + "\n"


def set_section_key(config: str, section: str, key: str, rendered_value: str) -> str:
    config = ensure_section(config, section)
    lines = config.splitlines()
    target = f"[{section}]"
    start = next(idx for idx, line in enumerate(lines) if line.strip() == target)
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = idx
            break
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    for idx in range(start + 1, end):
        if key_re.match(lines[idx]):
            lines[idx] = f"{key} = {rendered_value}"
            return "\n".join(lines).strip() + "\n"
    lines.insert(end, f"{key} = {rendered_value}")
    return "\n".join(lines).strip() + "\n"


def normalize_config(template: str, model: str, base_url: str) -> str:
    cfg = template or ""
    provider_name = extract_toml_string(cfg, "name") or "OpenAI"
    reasoning = extract_toml_string(cfg, "model_reasoning_effort") or "xhigh"

    cfg = set_root_key(cfg, "model", toml_quote(model))
    cfg = set_root_key(cfg, "model_provider", toml_quote("custom"))
    cfg = set_root_key(cfg, "model_reasoning_effort", toml_quote(reasoning))
    cfg = ensure_section(cfg, "model_providers")
    cfg = set_section_key(cfg, "model_providers.custom", "name", toml_quote(provider_name))
    cfg = set_section_key(cfg, "model_providers.custom", "wire_api", toml_quote("responses"))
    cfg = set_section_key(cfg, "model_providers.custom", "requires_openai_auth", "false")
    cfg = set_section_key(cfg, "model_providers.custom", "base_url", toml_quote(base_url))
    cfg = set_section_key(cfg, "windows", "sandbox", toml_quote("unelevated"))
    return cfg


def get_current_provider(conn: sqlite3.Connection) -> dict:
    cols = table_columns(conn, "providers")
    if not cols:
        raise RuntimeError("providers table not found")
    select_cols = [c for c in ["id", "name", "app_type", "is_current", "settings_config", "meta"] if c in cols]
    if "id" not in select_cols or "settings_config" not in select_cols:
        raise RuntimeError("providers table is missing id or settings_config")
    select_sql = ", ".join(qident(c) for c in select_cols)
    queries = []
    if "app_type" in cols and "is_current" in cols:
        queries.append((f"SELECT {select_sql} FROM providers WHERE app_type=? AND is_current=1 LIMIT 1", ("codex",)))
        queries.append((f"SELECT {select_sql} FROM providers WHERE app_type=? ORDER BY is_current DESC LIMIT 1", ("codex",)))
    if "is_current" in cols:
        queries.append((f"SELECT {select_sql} FROM providers WHERE is_current=1 LIMIT 1", ()))
    queries.append((f"SELECT {select_sql} FROM providers LIMIT 1", ()))
    for sql, params in queries:
        row = conn.execute(sql, params).fetchone()
        if row:
            return dict(row)
    raise RuntimeError("no provider row found")


def read_proxy_backup(conn: sqlite3.Connection) -> tuple[dict, str]:
    if not table_exists(conn, "proxy_live_backup"):
        return {}, ""
    cols = table_columns(conn, "proxy_live_backup")
    if "original_config" not in cols:
        return {}, ""
    if "app_type" in cols:
        row = conn.execute(
            "SELECT original_config FROM proxy_live_backup WHERE app_type=? LIMIT 1",
            ("codex",),
        ).fetchone()
    else:
        row = conn.execute("SELECT original_config FROM proxy_live_backup LIMIT 1").fetchone()
    if not row:
        return {}, ""
    obj = parse_json_object(row[0])
    return obj, config_from_value(obj)


def read_common_config(conn: sqlite3.Connection) -> str:
    if not table_exists(conn, "settings"):
        return ""
    cols = table_columns(conn, "settings")
    key_cols = [c for c in ["key", "name", "setting_key"] if c in cols]
    value_cols = [c for c in ["value", "setting_value", "config"] if c in cols]
    if not key_cols or not value_cols:
        return ""
    sql = "SELECT " + ", ".join(qident(c) for c in cols) + " FROM settings"
    keys = {"common_config_codex", "commonConfigCodex", "common-config-codex"}
    for row in conn.execute(sql).fetchall():
        data = dict(row)
        if any(str(data.get(c) or "") in keys for c in key_cols):
            for value_col in value_cols:
                cfg = config_from_value(data.get(value_col))
                if cfg:
                    return cfg
    return ""


def endpoint_base_urls(conn: sqlite3.Connection, provider_id: str) -> list[str]:
    if not table_exists(conn, "provider_endpoints"):
        return []
    cols = table_columns(conn, "provider_endpoints")
    sql = "SELECT " + ", ".join(qident(c) for c in cols) + " FROM provider_endpoints"
    candidates = []
    for row in conn.execute(sql).fetchall():
        data = dict(row)
        priority = 1
        for provider_col in ["provider_id", "providerId", "provider", "provider_uuid"]:
            if provider_col in data and str(data.get(provider_col)) == str(provider_id):
                priority = 0
                break
        for col in cols:
            lower = col.lower()
            if "url" in lower or "endpoint" in lower or "base" in lower:
                value = data.get(col)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    candidates.append((priority, value))
    candidates.sort(key=lambda item: item[0])
    return [item[1] for item in candidates]


def choose_base_url(arg_base_url: str, config_candidates: list[tuple[str, str]], endpoint_candidates: list[str]) -> tuple[str | None, str | None]:
    if arg_base_url:
        return arg_base_url, "argument"
    for name, cfg in config_candidates:
        url = extract_base_url(cfg)
        if url and not is_local_url(url):
            return url, name
    for url in endpoint_candidates:
        if url and not is_local_url(url):
            return url, "provider_endpoints"
    return None, None


def update_proxy_backup(conn: sqlite3.Connection, new_config: str, auth, changes: list[str], warnings: list[str]) -> None:
    if not table_exists(conn, "proxy_live_backup"):
        warnings.append("proxy_live_backup table not found; restart restore protection was skipped")
        return
    cols = table_columns(conn, "proxy_live_backup")
    if "original_config" not in cols:
        warnings.append("proxy_live_backup.original_config column not found; restart restore protection was skipped")
        return

    backup_obj, _ = read_proxy_backup(conn)
    if auth is not None and "auth" not in backup_obj:
        backup_obj["auth"] = auth
    backup_obj["config"] = new_config
    original_config = json.dumps(backup_obj, ensure_ascii=False)

    if "app_type" in cols:
        existing = conn.execute("SELECT 1 FROM proxy_live_backup WHERE app_type=? LIMIT 1", ("codex",)).fetchone()
        if existing:
            conn.execute("UPDATE proxy_live_backup SET original_config=? WHERE app_type=?", (original_config, "codex"))
            changes.append("proxy_live_backup.original_config updated")
            return

    if "app_type" in cols:
        insert_cols = ["app_type", "original_config"]
        values = ["codex", original_config]
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for time_col in ["created_at", "updated_at"]:
            if time_col in cols:
                insert_cols.append(time_col)
                values.append(now)
        sql = "INSERT INTO proxy_live_backup (" + ", ".join(qident(c) for c in insert_cols) + ") VALUES (" + ", ".join("?" for _ in insert_cols) + ")"
        conn.execute(sql, values)
        changes.append("proxy_live_backup.original_config inserted")
    else:
        conn.execute("UPDATE proxy_live_backup SET original_config=?", (original_config,))
        changes.append("proxy_live_backup.original_config updated")


def reset_provider_health(conn: sqlite3.Connection, provider_id: str, changes: list[str]) -> None:
    if table_exists(conn, "provider_health") and "provider_id" in table_columns(conn, "provider_health"):
        conn.execute("DELETE FROM provider_health WHERE provider_id=?", (provider_id,))
        changes.append("provider_health reset")


def repair(args: argparse.Namespace) -> dict:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        raise RuntimeError(f"CC-Switch database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    changes: list[str] = []
    warnings: list[str] = []
    backup_path = None
    try:
        provider = get_current_provider(conn)
        provider_id = provider.get("id")
        provider_settings = parse_json_object(provider.get("settings_config"))
        current_config = config_from_value(provider_settings)
        backup_obj, backup_config = read_proxy_backup(conn)
        common_config = read_common_config(conn)

        config_candidates = [
            (name, cfg)
            for name, cfg in [
                ("providers.settings_config", current_config),
                ("proxy_live_backup.original_config", backup_config),
                ("settings.common_config_codex", common_config),
            ]
            if cfg
        ]
        base_url, source = choose_base_url(args.base_url.strip(), config_candidates, endpoint_base_urls(conn, provider_id))
        if not base_url:
            raise RuntimeError("No upstream base_url could be inferred. Re-run with --base-url https://.../v1.")
        if is_local_url(base_url):
            warnings.append("selected base_url points to localhost; normally this should be the upstream API URL")

        model = (
            args.model.strip()
            or extract_toml_string(current_config, "model")
            or extract_toml_string(backup_config, "model")
            or extract_toml_string(common_config, "model")
            or "cx/gpt-5.5"
        )
        template = next((cfg for _, cfg in config_candidates if extract_base_url(cfg) and not is_local_url(extract_base_url(cfg))), "")
        if not template:
            template = current_config or backup_config or common_config
        new_config = normalize_config(template, model, base_url)

        new_settings = dict(provider_settings)
        if "auth" not in new_settings and backup_obj.get("auth") is not None:
            new_settings["auth"] = backup_obj.get("auth")
        new_settings["config"] = new_config

        meta = parse_json_object(provider.get("meta"))
        meta["commonConfigEnabled"] = False
        meta["endpointAutoSelect"] = True
        meta["apiFormat"] = "openai_responses"

        if not args.dry_run:
            backup_path = str(db_path) + ".bak-fix-base-url-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(str(db_path), backup_path)
            conn.execute("BEGIN")
            provider_cols = table_columns(conn, "providers")
            set_parts = ["settings_config=?"]
            params = [json.dumps(new_settings, ensure_ascii=False)]
            if "meta" in provider_cols:
                set_parts.append("meta=?")
                params.append(json.dumps(meta, ensure_ascii=False))
            params.append(provider_id)
            conn.execute("UPDATE providers SET " + ", ".join(set_parts) + " WHERE id=?", params)
            changes.append("providers.settings_config updated")
            if "meta" in provider_cols:
                changes.append("providers.meta updated")
            update_proxy_backup(conn, new_config, new_settings.get("auth"), changes, warnings)
            reset_provider_health(conn, provider_id, changes)
            conn.commit()

        return {
            "ok": True,
            "dry_run": bool(args.dry_run),
            "db": str(db_path),
            "backup": backup_path,
            "provider": {"id": provider_id, "name": provider.get("name") or ""},
            "model": model,
            "base_url": base_url,
            "base_url_source": source,
            "settings_has_base_url": bool(extract_base_url(new_config)),
            "settings_has_custom_provider": "[model_providers.custom]" in new_config,
            "changes": changes,
            "warnings": warnings,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        emit(repair(args))
    except Exception as exc:
        emit({"ok": False, "error": str(exc)}, code=1)


if __name__ == "__main__":
    main()
