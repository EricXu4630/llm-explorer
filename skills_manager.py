"""
Native Anthropic Skills Manager.

Uploads custom skills (SKILL.md from GitHub) to Anthropic's Skills API
and caches skill_ids locally so we don't re-upload on every request.

How it works:
  1. User loads a SKILL.md from GitHub → we get {name, description, content}
  2. We create a zip containing {skill-name}/SKILL.md
  3. Upload via POST /v1/skills (beta: skills-2025-10-02)
  4. Cache the returned skill_id keyed by content hash
  5. Pass skill_id in container.skills — Anthropic handles progressive disclosure

Progressive disclosure (Anthropic's side, not ours):
  Level 1: skill name + description injected into system prompt (~100 tokens)
  Level 2: model reads full SKILL.md via bash when triggered
  Level 3: model reads bundled resource files (scripts, references) as needed

The container persists within a session via container.id for multi-turn.
"""

import io
import json
import zipfile
import hashlib
import pathlib
import anthropic

CACHE_FILE = pathlib.Path(__file__).parent / "skill_ids_cache.json"
SKILLS_BETA = "skills-2025-10-02"
PRE_BUILT = {"xlsx", "pptx", "pdf", "docx"}  # Anthropic-managed built-in skills


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _content_hash(skill: dict) -> str:
    raw = f"{skill['name']}|{skill['content']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _skill_to_zip(skill: dict) -> bytes:
    """Pack a skill dict into a zip file with the correct SKILL.md structure."""
    name = skill["name"].lower().replace(" ", "-")
    skill_md = (
        f"---\nname: {name}\n"
        f"description: {skill['description']}\n---\n\n"
        f"{skill['content']}"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}/SKILL.md", skill_md)
    return buf.getvalue()


async def upload_skill(client: anthropic.AsyncAnthropic, skill: dict) -> str:
    """
    Upload a skill to Anthropic's Skills API and return the skill_id.
    Uses a local cache so the same skill is only uploaded once.
    """
    key = _content_hash(skill)
    cache = _load_cache()

    if key in cache:
        return cache[key]

    name = skill["name"].lower().replace(" ", "-")
    zip_bytes = _skill_to_zip(skill)

    result = await client.beta.skills.create(
        display_title=name,
        files=[(f"{name}.zip", io.BytesIO(zip_bytes), "application/zip")],
        betas=[SKILLS_BETA],
    )

    # The SDK returns a skill with an `id` field
    skill_id = getattr(result, "id", None) or (result.get("id") if isinstance(result, dict) else None)
    if not skill_id:
        raise ValueError(f"Skills API didn't return an id: {result}")

    cache[key] = skill_id
    _save_cache(cache)
    return skill_id


def build_container(skill_ids: list[str]) -> dict:
    """
    Build the container parameter for a Messages API request.
    Mixes pre-built (Anthropic) and custom skills.
    """
    skills = []
    for sid in skill_ids:
        if sid in PRE_BUILT:
            skills.append({"type": "anthropic", "skill_id": sid, "version": "latest"})
        else:
            skills.append({"type": "custom", "skill_id": sid})
    return {"skills": skills}
