import os
import json
from dataclasses import dataclass, field

SKILLS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "definitions"))


@dataclass
class SkillDefinition:
    command: str = ""
    description: str = ""
    prompt: str = ""
    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "description": self.description,
            "tools": self.tools,
        }


class SkillLoader:
    """Scan ``skills/definitions/`` for JSON skill definitions.

    Each skill file maps a slash command (e.g. ``draw`` for ``/draw``) to:
      - ``prompt``   — system prompt injected for this skill
      - ``tools``    — whitelist of tool names the skill may use
      - ``description`` — shown to the user for discovery
    """

    def __init__(self, skills_dir: str = ""):
        self._skills_dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, SkillDefinition] = {}
        self._load_all()

    def _load_all(self):
        os.makedirs(self._skills_dir, exist_ok=True)
        for fname in sorted(os.listdir(self._skills_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._skills_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                cmd = data.get("command", "")
                if cmd:
                    self._skills[cmd] = SkillDefinition(
                        command=cmd,
                        description=data.get("description", ""),
                        prompt=data.get("prompt", ""),
                        tools=data.get("tools", []),
                    )
            except Exception:
                continue

    def get_skill(self, command: str) -> SkillDefinition | None:
        """Look up a skill by its command keyword (no leading slash)."""
        return self._skills.get(command)

    def detect_skill(self, message: str) -> tuple[str, str, str] | None:
        """Check if ``message`` starts with ``/<command>``.

        Returns ``(command, args, skill_prompt)`` or ``None``.
        """
        if not message.startswith("/"):
            return None
        parts = message[1:].split(None, 1)
        if not parts:
            return None
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        skill = self.get_skill(cmd)
        if skill is None:
            return None
        return cmd, args, skill.prompt

    def list_skills(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def reload(self):
        self._skills.clear()
        self._load_all()
