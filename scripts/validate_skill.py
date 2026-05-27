#!/usr/bin/env python3
# Author: 算个文科生吧
# Contact: lijinghailjh@163.com
# Project: ljh_codex-desktop-loopback-repair_skill
"""Lightweight validation for the ljh-codex-desktop-loopback-repair-skill skill."""

from __future__ import annotations

import re
import sys
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        fail("SKILL.md must start with YAML frontmatter")

    end = text.find("\n---\n", 4)
    if end == -1:
        fail("SKILL.md frontmatter must end with ---")

    fields: dict[str, str] = {}
    for raw_line in text[4:end].splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            fail(f"Invalid frontmatter line: {raw_line}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")

    return fields


def main() -> None:
    root = (Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()).resolve()
    skill_md = root / "SKILL.md"
    openai_yaml = root / "agents" / "openai.yaml"

    if not skill_md.exists():
        fail("Missing SKILL.md")
    if not openai_yaml.exists():
        fail("Missing agents/openai.yaml")

    text = skill_md.read_text(encoding="utf-8-sig")
    fields = parse_frontmatter(text)

    name = fields.get("name", "")
    description = fields.get("description", "")

    if not name:
        fail("Missing frontmatter field: name")
    if not NAME_RE.match(name):
        fail(f"Invalid skill name: {name}")

    normalized_root_name = root.name.replace("_", "-")
    if name != normalized_root_name:
        fail(f"Skill name '{name}' must match normalized folder name '{normalized_root_name}'")
    if not description or len(description) < 80:
        fail("description should clearly explain what the skill does and when to use it")
    if "TODO" in text:
        fail("SKILL.md still contains TODO")

    yaml_text = openai_yaml.read_text(encoding="utf-8-sig")
    if f"${name}" not in yaml_text:
        fail("agents/openai.yaml default_prompt should mention the skill name with $skill-name")

    print(f"OK: {name}")


if __name__ == "__main__":
    main()


