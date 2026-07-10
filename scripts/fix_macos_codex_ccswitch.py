#!/usr/bin/env python3
"""Repair macOS Codex.app + CC-Switch custom provider routing.

This handles the macOS-specific failure where Codex.app's
``com.openai.codex.plist`` stores ``config_toml_base64`` and silently
overrides ``~/.codex/config.toml``. It also keeps CC-Switch's Codex provider
pointed at a reachable upstream relay and installs lightweight LaunchAgents to
preserve NO_PROXY and the Codex plist override.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:15721/v1"
DEFAULT_RELAY_BASE_URL = ""
DEFAULT_MODEL = "cx/gpt-5.5"


def emit(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    raise SystemExit(code)


def run(argv: list[str], timeout: int = 30) -> dict[str, object]:
    proc = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "argv": argv,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


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


def dump_json_object(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def backup_file(path: Path, tag: str, dry_run: bool) -> str | None:
    if not path.exists():
        return None
    backup = Path(str(path) + f".bak-{tag}-" + time.strftime("%Y%m%d-%H%M%S"))
    if not dry_run:
        shutil.copy2(path, backup)
    return str(backup)


def host_from_url(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def expected_config(model: str, base_url: str) -> str:
    return f'''model_provider = "custom"
model = "{model}"
model_reasoning_effort = "xhigh"
disable_response_storage = true

[features]
goals = true

[model_providers]
[model_providers.custom]
name = "OpenAI"
wire_api = "responses"
requires_openai_auth = false
base_url = "{base_url}"
'''


def patch_codex_config(home: Path, config_text: str, dry_run: bool) -> dict[str, object]:
    path = home / ".codex" / "config.toml"
    backup = backup_file(path, "macos-codex-repair", dry_run)
    current = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    changed = current != config_text
    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config_text, encoding="utf-8")
    return {"path": str(path), "backup": backup, "changed": changed}


def patch_codex_plist(home: Path, config_text: str, dry_run: bool) -> dict[str, object]:
    path = home / "Library" / "Preferences" / "com.openai.codex.plist"
    backup = backup_file(path, "macos-codex-repair", dry_run)
    data: dict = {}
    if path.exists():
        try:
            with path.open("rb") as f:
                data = plistlib.load(f)
        except Exception:
            data = {}
    wanted = base64.b64encode(config_text.encode("utf-8")).decode("ascii")
    changed = data.get("config_toml_base64") != wanted
    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        data["config_toml_base64"] = wanted
        with path.open("wb") as f:
            plistlib.dump(data, f, sort_keys=False)
        run(["killall", "cfprefsd"], timeout=10)
    return {"path": str(path), "backup": backup, "changed": changed}


def find_ccswitch_db(home: Path, explicit: str = "") -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        [
            home / ".cc-switch" / "cc-switch.db",
            home / "Library" / "Application Support" / "cc-switch" / "cc-switch.db",
            home / "Library" / "Application Support" / "com.ccswitch.desktop" / "cc-switch.db",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("CC-Switch database not found")


def select_codex_provider(conn: sqlite3.Connection) -> dict:
    cols = table_columns(conn, "providers")
    if not cols:
        raise RuntimeError("providers table not found")
    queries: list[tuple[str, tuple]] = []
    if "app_type" in cols and "is_current" in cols:
        queries.append(("SELECT * FROM providers WHERE app_type=? AND is_current=1 LIMIT 1", ("codex",)))
        queries.append(("SELECT * FROM providers WHERE app_type=? ORDER BY is_current DESC LIMIT 1", ("codex",)))
    if "is_current" in cols:
        queries.append(("SELECT * FROM providers WHERE is_current=1 LIMIT 1", ()))
    queries.append(("SELECT * FROM providers LIMIT 1", ()))
    for sql, params in queries:
        row = conn.execute(sql, params).fetchone()
        if row:
            return dict(row)
    raise RuntimeError("no provider row found")


def patch_ccswitch_db(
    home: Path,
    db_arg: str,
    provider_config: str,
    app_config: str,
    relay_base_url: str,
    dry_run: bool,
) -> dict[str, object]:
    db = find_ccswitch_db(home, db_arg)
    backup = backup_file(db, "macos-codex-repair", dry_run)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    changes: list[str] = []
    try:
        provider = select_codex_provider(conn)
        provider_id = str(provider.get("id"))
        settings = parse_json_object(provider.get("settings_config"))
        settings["config"] = provider_config

        meta = parse_json_object(provider.get("meta")) if "meta" in provider else {}
        meta["commonConfigEnabled"] = False
        meta["endpointAutoSelect"] = True
        meta["apiFormat"] = "openai_responses"

        if not dry_run:
            conn.execute("BEGIN")
            provider_cols = table_columns(conn, "providers")
            parts: list[str] = []
            values: list[object] = []
            if "settings_config" in provider_cols:
                parts.append("settings_config=?")
                values.append(dump_json_object(settings))
            if "meta" in provider_cols:
                parts.append("meta=?")
                values.append(dump_json_object(meta))
            if parts:
                values.append(provider_id)
                conn.execute("UPDATE providers SET " + ", ".join(parts) + " WHERE id=?", values)
                changes.append("providers")

            if table_exists(conn, "provider_endpoints"):
                endpoint_cols = table_columns(conn, "provider_endpoints")
                if "url" in endpoint_cols:
                    if "provider_id" in endpoint_cols:
                        conn.execute("UPDATE provider_endpoints SET url=? WHERE provider_id=?", (relay_base_url, provider_id))
                        changes.append("provider_endpoints")
                    elif "providerId" in endpoint_cols:
                        conn.execute("UPDATE provider_endpoints SET url=? WHERE providerId=?", (relay_base_url, provider_id))
                        changes.append("provider_endpoints")

            if table_exists(conn, "proxy_live_backup"):
                backup_cols = table_columns(conn, "proxy_live_backup")
                if "original_config" in backup_cols:
                    if "app_type" in backup_cols:
                        row = conn.execute(
                            "SELECT original_config FROM proxy_live_backup WHERE app_type=? LIMIT 1",
                            ("codex",),
                        ).fetchone()
                    else:
                        row = conn.execute("SELECT original_config FROM proxy_live_backup LIMIT 1").fetchone()
                    obj = parse_json_object(row["original_config"] if row else "")
                    obj["config"] = provider_config
                    if settings.get("auth") is not None:
                        obj["auth"] = settings.get("auth")
                    raw = dump_json_object(obj)
                    if "app_type" in backup_cols:
                        if row:
                            conn.execute("UPDATE proxy_live_backup SET original_config=? WHERE app_type=?", (raw, "codex"))
                        else:
                            conn.execute(
                                "INSERT INTO proxy_live_backup (app_type, original_config) VALUES (?, ?)",
                                ("codex", raw),
                            )
                    else:
                        conn.execute("UPDATE proxy_live_backup SET original_config=?", (raw,))
                    changes.append("proxy_live_backup")

            if table_exists(conn, "settings"):
                setting_cols = table_columns(conn, "settings")
                key_cols = [c for c in ["key", "name", "setting_key"] if c in setting_cols]
                value_cols = [c for c in ["value", "setting_value", "config"] if c in setting_cols]
                keys = {"common_config_codex", "commonConfigCodex", "common-config-codex"}
                if key_cols and value_cols:
                    rows = conn.execute("SELECT rowid, * FROM settings").fetchall()
                    touched = False
                    for row in rows:
                        data = dict(row)
                        if any(str(data.get(key) or "") in keys for key in key_cols):
                            value_col = value_cols[0]
                            obj = parse_json_object(data.get(value_col))
                            obj["config"] = app_config
                            conn.execute(
                                f"UPDATE settings SET {qident(value_col)}=? WHERE rowid=?",
                                (dump_json_object(obj), data["rowid"]),
                            )
                            touched = True
                    if touched:
                        changes.append("settings.common_config_codex")

            if table_exists(conn, "provider_health"):
                health_cols = table_columns(conn, "provider_health")
                if "provider_id" in health_cols:
                    conn.execute("DELETE FROM provider_health WHERE provider_id=?", (provider_id,))
                else:
                    conn.execute("DELETE FROM provider_health")
                changes.append("provider_health")

            conn.commit()
    except Exception:
        if not dry_run:
            conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "db": str(db),
        "backup": backup,
        "provider": {"id": provider.get("id"), "name": provider.get("name")},
        "changes": changes,
    }


def plist_xml(label: str, program_args: list[str], stdout: Path, stderr: Path, interval: int) -> str:
    args = "\n".join(f"    <string>{arg}</string>" for arg in program_args)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>StandardOutPath</key><string>{stdout}</string>
  <key>StandardErrorPath</key><string>{stderr}</string>
</dict>
</plist>
'''


def load_agent(home: Path, label: str, plist_path: Path, dry_run: bool) -> list[dict[str, object]]:
    if dry_run:
        return []
    uid = str(os.getuid())
    return [
        run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)], timeout=10),
        run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], timeout=10),
        run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], timeout=10),
    ]


def install_no_proxy_agent(home: Path, relay_base_url: str, dry_run: bool) -> dict[str, object]:
    host = host_from_url(relay_base_url)
    values = [v for v in [host, "100.64.0.0/10", "localhost", "127.0.0.1"] if v]
    no_proxy = ",".join(dict.fromkeys(values))
    script = home / ".codex" / "set-codex-no-proxy-env.sh"
    plist = home / "Library" / "LaunchAgents" / "com.codex.no-proxy-env.plist"
    script_text = f'''#!/bin/zsh
VALUE="{no_proxy}"
/bin/launchctl setenv NO_PROXY "$VALUE"
/bin/launchctl setenv no_proxy "$VALUE"
exit 0
'''
    plist_text = plist_xml(
        "com.codex.no-proxy-env",
        [str(script)],
        home / ".codex" / "codex-no-proxy-env.log",
        home / ".codex" / "codex-no-proxy-env.err.log",
        30,
    )
    if not dry_run:
        script.parent.mkdir(parents=True, exist_ok=True)
        plist.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(script_text, encoding="utf-8")
        script.chmod(0o755)
        plist.write_text(plist_text, encoding="utf-8")
        run([str(script)], timeout=10)
    launch = load_agent(home, "com.codex.no-proxy-env", plist, dry_run)
    return {"script": str(script), "plist": str(plist), "NO_PROXY": no_proxy, "launchctl": launch}


def install_plist_guard(home: Path, local_config: str, dry_run: bool) -> dict[str, object]:
    script = home / ".codex" / "guard-codex-plist-local-proxy.py"
    plist = home / "Library" / "LaunchAgents" / "com.codex.plist-proxy-guard.plist"
    guard_source = f'''#!/usr/bin/env python3
import base64, pathlib, plistlib, time
home = pathlib.Path.home()
expected = {local_config!r}
plist = home / "Library" / "Preferences" / "com.openai.codex.plist"
config = home / ".codex" / "config.toml"
changed = False
try:
    data = plistlib.load(open(plist, "rb")) if plist.exists() else {{}}
except Exception:
    data = {{}}
b64 = base64.b64encode(expected.encode()).decode()
if data.get("config_toml_base64") != b64:
    data["config_toml_base64"] = b64
    plist.parent.mkdir(parents=True, exist_ok=True)
    with open(plist, "wb") as f:
        plistlib.dump(data, f, sort_keys=False)
    changed = True
if (not config.exists()) or config.read_text(encoding="utf-8", errors="replace") != expected:
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(expected, encoding="utf-8")
    changed = True
if changed:
    with open(home / ".codex" / "codex-plist-proxy-guard.log", "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " fixed codex local proxy config\\n")
'''
    plist_text = plist_xml(
        "com.codex.plist-proxy-guard",
        ["/usr/bin/python3", str(script)],
        home / ".codex" / "codex-plist-proxy-guard.out.log",
        home / ".codex" / "codex-plist-proxy-guard.err.log",
        20,
    )
    if not dry_run:
        script.parent.mkdir(parents=True, exist_ok=True)
        plist.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(guard_source, encoding="utf-8")
        script.chmod(0o755)
        plist.write_text(plist_text, encoding="utf-8")
        run(["/usr/bin/python3", str(script)], timeout=10)
    launch = load_agent(home, "com.codex.plist-proxy-guard", plist, dry_run)
    return {"script": str(script), "plist": str(plist), "launchctl": launch}


def restart_apps(dry_run: bool) -> dict[str, object]:
    if dry_run:
        return {"skipped": True}
    results = []
    results.append(run(["osascript", "-e", 'tell application "Codex" to quit'], timeout=15))
    time.sleep(2)
    results.append(run(["pkill", "-f", "/Applications/Codex.app"], timeout=10))
    results.append(run(["pkill", "-f", "codex app-server"], timeout=10))
    time.sleep(2)
    results.append(run(["osascript", "-e", 'tell application "CC Switch" to quit'], timeout=15))
    time.sleep(2)
    results.append(run(["pkill", "-f", "/Applications/CC Switch.app"], timeout=10))
    results.append(run(["pkill", "-f", "cc-switch"], timeout=10))
    time.sleep(2)
    results.append(run(["open", "-a", "/Applications/CC Switch.app"], timeout=10))
    time.sleep(8)
    results.append(run(["open", "-a", "/Applications/Codex.app"], timeout=10))
    time.sleep(8)
    return {"commands": results}


def smoke_local_ccswitch(local_base_url: str, model: str) -> dict[str, object]:
    url = local_base_url.rstrip("/") + "/responses"
    body = json.dumps(
        {"model": model, "input": [{"role": "user", "content": "只回复OK"}], "max_output_tokens": 20}
    ).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=45) as response:
        text = response.read(1200).decode("utf-8", "replace")
        return {"status": response.status, "body_prefix": text}


def compact_status() -> dict[str, object]:
    try:
        with urlopen("http://127.0.0.1:15721/status", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        return {
            key: data.get(key)
            for key in ["running", "current_provider", "active_targets", "last_error", "success_rate", "total_requests", "failed_requests"]
            if key in data
        }
    except Exception as exc:
        return {"error": repr(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-base-url", default=DEFAULT_LOCAL_BASE_URL)
    parser.add_argument("--relay-base-url", default=DEFAULT_RELAY_BASE_URL, required=not bool(DEFAULT_RELAY_BASE_URL))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--db", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        emit({"ok": False, "error": "This repair script must run on macOS."}, code=2)

    home = Path.home()
    local_config = expected_config(args.model, args.local_base_url)
    provider_config = expected_config(args.model, args.relay_base_url)
    result: dict[str, object] = {
        "ok": True,
        "dry_run": bool(args.dry_run),
        "local_base_url": args.local_base_url,
        "relay_base_url": args.relay_base_url,
        "model": args.model,
    }

    try:
        result["codex_config"] = patch_codex_config(home, local_config, args.dry_run)
        result["codex_plist"] = patch_codex_plist(home, local_config, args.dry_run)
        result["ccswitch_db"] = patch_ccswitch_db(
            home,
            args.db,
            provider_config,
            local_config,
            args.relay_base_url,
            args.dry_run,
        )
        result["no_proxy_agent"] = install_no_proxy_agent(home, args.relay_base_url, args.dry_run)
        result["plist_guard"] = install_plist_guard(home, local_config, args.dry_run)
        if not args.no_restart:
            result["restart"] = restart_apps(args.dry_run)
        if not args.skip_smoke and not args.dry_run:
            result["smoke"] = smoke_local_ccswitch(args.local_base_url, args.model)
            result["ccswitch_status"] = compact_status()
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        emit(result, code=1)

    emit(result)


if __name__ == "__main__":
    main()
