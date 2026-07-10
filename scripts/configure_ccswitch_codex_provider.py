#!/usr/bin/env python3
"""Configure the current CC-Switch Codex provider with an upstream key, URL, and model.

This helper is intended for Windows CC-Switch databases. Stop CC-Switch before running
it so SQLite writes are not overwritten by Live Takeover restore logic.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path


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


def parse_settings(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw).strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except Exception:
        return {"config": text}
    return dict(value) if isinstance(value, dict) else {}


def toml_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


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


def normalize_config(config: str, *, provider_name: str, model: str, base_url: str) -> str:
    cfg = config or ""
    cfg = set_root_key(cfg, "model_provider", toml_quote("custom"))
    cfg = set_root_key(cfg, "model", toml_quote(model))
    cfg = ensure_section(cfg, "model_providers")
    cfg = set_section_key(cfg, "model_providers.custom", "name", toml_quote(provider_name or "custom"))
    cfg = set_section_key(cfg, "model_providers.custom", "wire_api", toml_quote("responses"))
    cfg = set_section_key(cfg, "model_providers.custom", "requires_openai_auth", "true")
    cfg = set_section_key(cfg, "model_providers.custom", "base_url", toml_quote(base_url.rstrip("/")))
    return cfg


def patch_settings(raw, *, provider_name: str, model: str, base_url: str, api_key: str | None) -> dict:
    data = parse_settings(raw)
    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    if api_key:
        auth["OPENAI_API_KEY"] = api_key
    if auth:
        data["auth"] = auth
    data["config"] = normalize_config(
        str(data.get("config") or ""),
        provider_name=provider_name,
        model=model,
        base_url=base_url,
    )
    data["commonConfigEnabled"] = False
    data["endpointAutoSelect"] = True
    data["apiFormat"] = "openai_responses"
    return data


def read_api_key(args: argparse.Namespace) -> str | None:
    if args.api_key:
        return args.api_key.strip()
    if args.api_key_file:
        return Path(args.api_key_file).expanduser().read_text(encoding="utf-8").strip()
    if args.api_key_env:
        return os.environ.get(args.api_key_env, "").strip() or None
    return os.environ.get("CODEX_PROVIDER_KEY", "").strip() or None


def select_provider(conn: sqlite3.Connection, args: argparse.Namespace) -> dict:
    cols = table_columns(conn, "providers")
    if not cols:
        raise RuntimeError("providers table not found")
    select_cols = [c for c in ["id", "app_type", "name", "settings_config", "meta", "is_current"] if c in cols]
    sql_cols = ", ".join(qident(c) for c in select_cols)
    queries: list[tuple[str, tuple]] = []
    if args.provider_id:
        queries.append((f"SELECT {sql_cols} FROM providers WHERE app_type=? AND id=? LIMIT 1", ("codex", args.provider_id)))
    if args.provider_name:
        queries.append((f"SELECT {sql_cols} FROM providers WHERE app_type=? AND name=? LIMIT 1", ("codex", args.provider_name)))
    if "is_current" in cols:
        queries.append((f"SELECT {sql_cols} FROM providers WHERE app_type=? AND is_current=1 LIMIT 1", ("codex",)))
    queries.append((f"SELECT {sql_cols} FROM providers WHERE app_type=? LIMIT 1", ("codex",)))
    for sql, params in queries:
        row = conn.execute(sql, params).fetchone()
        if row:
            return dict(row)
    raise RuntimeError("no Codex provider row found")


def update_provider_endpoints(conn: sqlite3.Connection, provider_id: str, base_url: str, changes: list[str]) -> None:
    if not table_exists(conn, "provider_endpoints"):
        return
    cols = table_columns(conn, "provider_endpoints")
    if not {"provider_id", "app_type", "url"}.issubset(cols):
        return
    conn.execute(
        "DELETE FROM provider_endpoints WHERE app_type=? AND provider_id=?",
        ("codex", provider_id),
    )
    insert_cols = ["provider_id", "app_type", "url"]
    values = [provider_id, "codex", base_url.rstrip("/")]
    if "added_at" in cols:
        insert_cols.append("added_at")
        values.append(int(time.time() * 1000))
    sql = (
        "INSERT INTO provider_endpoints ("
        + ", ".join(qident(c) for c in insert_cols)
        + ") VALUES ("
        + ", ".join("?" for _ in insert_cols)
        + ")"
    )
    conn.execute(sql, values)
    changes.append("provider_endpoints replaced")


def update_proxy_live_backup(conn: sqlite3.Connection, settings: dict, changes: list[str]) -> None:
    if not table_exists(conn, "proxy_live_backup"):
        return
    cols = table_columns(conn, "proxy_live_backup")
    if "original_config" not in cols:
        return
    original_config = json.dumps(settings, ensure_ascii=False, separators=(",", ":"))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if "app_type" in cols:
        exists = conn.execute("SELECT 1 FROM proxy_live_backup WHERE app_type=? LIMIT 1", ("codex",)).fetchone()
        if exists:
            if "backed_up_at" in cols:
                conn.execute(
                    "UPDATE proxy_live_backup SET original_config=?, backed_up_at=? WHERE app_type=?",
                    (original_config, now, "codex"),
                )
            else:
                conn.execute(
                    "UPDATE proxy_live_backup SET original_config=? WHERE app_type=?",
                    (original_config, "codex"),
                )
            changes.append("proxy_live_backup.original_config updated")
            return
        insert_cols = ["app_type", "original_config"]
        values = ["codex", original_config]
        if "backed_up_at" in cols:
            insert_cols.append("backed_up_at")
            values.append(now)
        sql = (
            "INSERT INTO proxy_live_backup ("
            + ", ".join(qident(c) for c in insert_cols)
            + ") VALUES ("
            + ", ".join("?" for _ in insert_cols)
            + ")"
        )
        conn.execute(sql, values)
        changes.append("proxy_live_backup.original_config inserted")


def update_common_config(conn: sqlite3.Connection, settings: dict, changes: list[str]) -> None:
    if not table_exists(conn, "settings"):
        return
    cols = table_columns(conn, "settings")
    if not {"key", "value"}.issubset(cols):
        return
    value = json.dumps(settings, ensure_ascii=False, separators=(",", ":"))
    cur = conn.execute("UPDATE settings SET value=? WHERE key=?", (value, "common_config_codex"))
    if cur.rowcount:
        changes.append("settings.common_config_codex updated")


def reset_health(conn: sqlite3.Connection, provider_id: str, changes: list[str]) -> None:
    if not table_exists(conn, "provider_health"):
        return
    cols = table_columns(conn, "provider_health")
    if "provider_id" not in cols:
        return
    if "app_type" in cols:
        conn.execute("DELETE FROM provider_health WHERE provider_id=? AND app_type=?", (provider_id, "codex"))
    else:
        conn.execute("DELETE FROM provider_health WHERE provider_id=?", (provider_id,))
    changes.append("provider_health reset")


def configure(args: argparse.Namespace) -> dict:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        raise RuntimeError(f"database not found: {db_path}")
    api_key = read_api_key(args)
    if args.require_api_key and not api_key:
        raise RuntimeError("API key required. Set CODEX_PROVIDER_KEY or pass --api-key-file.")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    changes: list[str] = []
    backup_path = None
    try:
        provider = select_provider(conn, args)
        provider_id = str(provider["id"])
        provider_name = str(provider.get("name") or args.provider_name or "custom")
        settings = patch_settings(
            provider.get("settings_config"),
            provider_name=provider_name,
            model=args.model,
            base_url=args.base_url,
            api_key=api_key,
        )

        if not args.dry_run:
            backup_path = str(db_path) + ".bak-configure-provider-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(str(db_path), backup_path)
            conn.execute("BEGIN")
            provider_cols = table_columns(conn, "providers")
            if "is_current" in provider_cols:
                conn.execute("UPDATE providers SET is_current=0 WHERE app_type=? AND id<>?", ("codex", provider_id))
            conn.execute(
                "UPDATE providers SET settings_config=?, is_current=1 WHERE app_type=? AND id=?",
                (json.dumps(settings, ensure_ascii=False, separators=(",", ":")), "codex", provider_id),
            )
            changes.append("providers.settings_config updated")
            update_provider_endpoints(conn, provider_id, args.base_url, changes)
            update_proxy_live_backup(conn, settings, changes)
            update_common_config(conn, settings, changes)
            reset_health(conn, provider_id, changes)
            conn.commit()

        return {
            "ok": True,
            "dry_run": bool(args.dry_run),
            "db": str(db_path),
            "backup": backup_path,
            "provider": {"id": provider_id, "name": provider_name},
            "base_url": args.base_url.rstrip("/"),
            "model": args.model,
            "api_key_present": bool(api_key),
            "changes": changes,
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
    parser.add_argument("--db", required=True, help="Path to cc-switch.db")
    parser.add_argument("--base-url", required=True, help="Upstream OpenAI-compatible base URL, e.g. https://host/v1")
    parser.add_argument("--model", default="cx/gpt-5.5", help="Exact model id from /v1/models")
    parser.add_argument("--provider-id", default="")
    parser.add_argument("--provider-name", default="")
    parser.add_argument("--api-key", default="", help="API key. Prefer --api-key-file or CODEX_PROVIDER_KEY to avoid shell history.")
    parser.add_argument("--api-key-file", default="")
    parser.add_argument("--api-key-env", default="CODEX_PROVIDER_KEY")
    parser.add_argument("--require-api-key", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        emit(configure(args))
    except Exception as exc:
        emit({"ok": False, "error": str(exc)}, code=1)


if __name__ == "__main__":
    main()
