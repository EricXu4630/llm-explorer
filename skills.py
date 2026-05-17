"""
Agent Skills loader — fetches SKILL.md files from GitHub or any raw URL.

Supports:
  - Raw GitHub URLs:   https://raw.githubusercontent.com/user/repo/main/skill/SKILL.md
  - GitHub blob URLs: https://github.com/user/repo/blob/main/skill/SKILL.md
  - GitHub tree URLs: https://github.com/user/repo/tree/main/skill/ (appends SKILL.md)
  - GitHub repo root: https://github.com/user/repo  (tries main/SKILL.md)
  - Any direct URL ending in a .md file

SKILL.md format (agentskills.io open spec):
  ---
  name: my-skill
  description: What this skill does and when to use it.
  ---
  # Instructions here...
"""

import re
import asyncio
import httpx
import yaml


def _to_raw_url(url: str) -> str:
    url = url.strip()

    if "raw.githubusercontent.com" in url:
        return url

    if "github.com" in url:
        if "/blob/" in url:
            return (
                url.replace("github.com", "raw.githubusercontent.com")
                   .replace("/blob/", "/")
            )
        if "/tree/" in url:
            raw = (
                url.replace("github.com", "raw.githubusercontent.com")
                   .replace("/tree/", "/")
                   .rstrip("/")
            )
            return raw if raw.endswith("SKILL.md") else raw + "/SKILL.md"

        # Bare repo URL
        raw = url.replace("github.com", "raw.githubusercontent.com").rstrip("/")
        return f"{raw}/main/SKILL.md"

    return url


def _parse_frontmatter(content: str) -> tuple:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if match:
        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        return fm, match.group(2).strip()
    return {}, content.strip()


def _infer_name(url: str) -> str:
    parts = url.rstrip("/").split("/")
    for part in reversed(parts):
        if part and part.lower() not in ("skill.md", "blob", "tree", "main", "master"):
            return part.lower().replace("_", "-")
    return "skill"


async def load_skill_from_url(url: str) -> dict:
    raw_url = _to_raw_url(url)

    def _fetch():
        with httpx.Client(follow_redirects=True, timeout=15) as c:
            r = c.get(raw_url)
            r.raise_for_status()
            return r.text

    content = await asyncio.to_thread(_fetch)
    fm, body = _parse_frontmatter(content)

    return {
        "name": fm.get("name") or _infer_name(url),
        "description": fm.get("description", ""),
        "content": body,
        "url": url,
        "raw_url": raw_url,
    }
