#!/usr/bin/env python3
"""Sync Codex auth.json from the current CC-Switch Codex provider."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def emit(obj: dict, code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=True, indent=2))
    raise SystemExit(code)


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    if not row:
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({qident(name)})")]


def parse_json_object(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        obj = json.loads(str(value))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def config_from_settings(settings: dict) -> str:
    cfg = settings.get("config")
    return cfg if isinstance(cfg, str) else ""


def extract_base_url(config: str) -> str | None:
    for raw in config.splitlines():
        line = raw.strip()
        if line.startswith("base_url") and "=" in line:
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    return None


def endpoint_url(conn: sqlite3.Connection, provider_id: str) -> str | None:
    cols = table_columns(conn, "provider_endpoints")
    if not cols:
        return None
    select_sql = ", ".join(qident(c) for c in cols)
    rows = conn.execute(f"SELECT {select_sql} FROM provider_endpoints").fetchall()
    for row in rows:
        data = dict(row)
        if str(data.get("provider_id") or "") != str(provider_id):
            continue
        for col in cols:
            value = data.get(col)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    return None


def current_provider(conn: sqlite3.Connection, provider_id: str = "") -> dict:
    cols = table_columns(conn, "providers")
    if not cols:
        raise RuntimeError("providers table not found")
    select_cols = [c for c in ["id", "name", "app_type", "is_current", "settings_config"] if c in cols]
    if "id" not in select_cols or "settings_config" not in select_cols:
        raise RuntimeError("providers table is missing id or settings_config")
    select_sql = ", ".join(qident(c) for c in select_cols)
    queries: list[tuple[str, tuple]] = []
    if provider_id:
        queries.append((f"SELECT {select_sql} FROM providers WHERE id=? LIMIT 1", (provider_id,)))
    if "app_type" in cols and "is_current" in cols:
        queries.append((f"SELECT {select_sql} FROM providers WHERE app_type=? AND is_current=1 LIMIT 1", ("codex",)))
    if "is_current" in cols:
        queries.append((f"SELECT {select_sql} FROM providers WHERE is_current=1 LIMIT 1", ()))
    queries.append((f"SELECT {select_sql} FROM providers LIMIT 1", ()))
    for sql, params in queries:
        row = conn.execute(sql, params).fetchone()
        if row:
            return dict(row)
    raise RuntimeError("no provider row found")


def auth_key_from_provider(provider: dict) -> str:
    settings = parse_json_object(provider.get("settings_config"))
    auth = settings.get("auth") if isinstance(settings.get("auth"), dict) else {}
    for key_name in ["OPENAI_API_KEY", "api_key", "API_KEY"]:
        value = auth.get(key_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise RuntimeError("current provider has no OPENAI_API_KEY in settings_config.auth")


def read_codex_key(auth_path: Path) -> str:
    if not auth_path.exists():
        return ""
    obj = parse_json_object(auth_path.read_text(encoding="utf-8-sig"))
    value = obj.get("OPENAI_API_KEY") or obj.get("api_key") or ""
    return str(value).strip()


def verify_responses(base_url: str, api_key: str, model: str, timeout: int) -> dict:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(f"invalid base_url: {base_url}")
    url = base_url.rstrip("/") + "/responses"
    body = json.dumps(
        {
            "model": model,
            "input": [{"role": "user", "content": "reply OK"}],
            "max_output_tokens": 8,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return {"http": resp.status, "ok": 200 <= resp.status < 300, "body_has_error": '"error"' in text[:500]}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {"http": exc.code, "ok": False, "body_preview": text[:300]}


def install_launch_agent(args: argparse.Namespace) -> dict:
    if sys.platform != "darwin":
        raise RuntimeError("--install-launch-agent is only supported on macOS")
    if args.dry_run:
        return {"installed": False, "reason": "dry_run"}

    codex_home = Path(args.codex_home).expanduser()
    codex_home.mkdir(parents=True, exist_ok=True)
    source_script = Path(__file__).resolve()
    installed_script = codex_home / "sync-codex-auth-from-ccswitch.py"
    same_script = installed_script.exists() and source_script == installed_script.resolve()
    if not same_script:
        shutil.copy2(str(source_script), str(installed_script))
    try:
        os.chmod(str(installed_script), 0o700)
    except Exception:
        pass

    label = "com.codex.ccswitch-auth-sync"
    launch_dir = Path.home() / "Library" / "LaunchAgents"
    launch_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_dir / f"{label}.plist"
    python_bin = sys.executable or "/usr/bin/python3"
    interval = max(10, int(args.interval))
    program_arguments = [
        python_bin,
        str(installed_script),
        "--db",
        str(Path(args.db).expanduser()),
        "--codex-home",
        str(codex_home),
    ]
    if args.provider_id.strip():
        program_arguments.extend(["--provider-id", args.provider_id.strip()])
    if args.base_url.strip():
        program_arguments.extend(["--base-url", args.base_url.strip()])
    if args.model.strip():
        program_arguments.extend(["--model", args.model.strip()])
    program_arguments.append("--quiet-if-unchanged")

    plist = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "StartInterval": interval,
        "StandardOutPath": str(codex_home / "codex-auth-sync.launchd.out.log"),
        "StandardErrorPath": str(codex_home / "codex-auth-sync.launchd.err.log"),
        "WorkingDirectory": str(codex_home),
    }
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)

    uid = os.getuid()
    target = f"gui/{uid}"
    service = f"{target}/{label}"

    def run_launchctl(cmd: list[str]) -> dict:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=20)
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-500:],
            "stderr": proc.stderr[-500:],
        }

    actions = []
    actions.append(run_launchctl(["launchctl", "bootout", target, str(plist_path)]))
    bootstrap = run_launchctl(["launchctl", "bootstrap", target, str(plist_path)])
    actions.append(bootstrap)
    if bootstrap["returncode"] != 0:
        actions.append(run_launchctl(["launchctl", "load", "-w", str(plist_path)]))
    actions.append(run_launchctl(["launchctl", "kickstart", "-k", service]))
    return {
        "installed": True,
        "label": label,
        "plist": str(plist_path),
        "script": str(installed_script),
        "interval_seconds": interval,
        "actions": actions,
    }


def repair(args: argparse.Namespace) -> dict:
    db_path = Path(args.db).expanduser()
    codex_home = Path(args.codex_home).expanduser()
    auth_path = codex_home / "auth.json"
    if not db_path.exists():
        raise RuntimeError(f"CC-Switch database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        provider = current_provider(conn, args.provider_id.strip())
        provider_id = str(provider.get("id") or "")
        settings = parse_json_object(provider.get("settings_config"))
        provider_key = auth_key_from_provider(provider)
        config_base_url = extract_base_url(config_from_settings(settings))
        base_url = args.base_url.strip() or config_base_url or endpoint_url(conn, provider_id) or ""
    finally:
        conn.close()

    old_key = read_codex_key(auth_path)
    changed = old_key != provider_key
    backup_path = None
    if changed and not args.dry_run:
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        if auth_path.exists():
            backup_path = str(auth_path) + ".bak-sync-ccswitch-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(str(auth_path), backup_path)
        tmp_path = auth_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps({"OPENAI_API_KEY": provider_key}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp_path), str(auth_path))
        try:
            os.chmod(str(auth_path), 0o600)
        except Exception:
            pass

    verify = None
    if args.verify:
        if not base_url:
            raise RuntimeError("no upstream base_url found for verification; pass --base-url")
        verify = verify_responses(base_url, provider_key, args.model, args.timeout)

    return {
        "ok": True,
        "dry_run": bool(args.dry_run),
        "db": str(db_path),
        "auth_path": str(auth_path),
        "backup": backup_path,
        "provider": {"id": provider_id, "name": str(provider.get("name") or "")},
        "base_url_host": urlparse(base_url).netloc if base_url else "",
        "old_key_present": bool(old_key),
        "provider_key_present": bool(provider_key),
        "keys_already_matched": not changed,
        "changed": changed and not args.dry_run,
        "verify": verify,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Path.home() / ".cc-switch" / "cc-switch.db"))
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--provider-id", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="cx/gpt-5.5")
    parser.add_argument("--timeout", type=int, default=40)
    parser.add_argument("--install-launch-agent", action="store_true")
    parser.add_argument("--interval", type=int, default=20, help="LaunchAgent sync interval in seconds")
    parser.add_argument("--quiet-if-unchanged", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = repair(args)
        if args.install_launch_agent:
            result["launch_agent"] = install_launch_agent(args)
        if args.quiet_if_unchanged and not result.get("changed") and "launch_agent" not in result:
            return
        emit(result)
    except Exception as exc:
        emit({"ok": False, "error": str(exc)}, code=1)


if __name__ == "__main__":
    main()
