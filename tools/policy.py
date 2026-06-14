import re
from enum import Enum
from typing import Callable


class PolicyDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ToolPolicy:
    """Pure synchronous policy evaluation for tool-call permissions.

    Classification tiers (checked in order):

      1. Blocklist / allowlist  — explicit admin rules
      2. Read-only tools        — always ``ALLOW``
      3. CWD escape (bash)      — force ``ASK`` (not deny)
      4. Side-effect tools      — ``ASK`` unless cached
      5. Custom callback        — override for any tool
      6. Default                — ``ASK`` (unknown tool)

    ``matches_outside_cwd()`` is a best-effort regex detection of commands
    that escape the project working directory.
    """

    # Tools with no side effects — always allowed.
    READ_ONLY = frozenset({
        "read_file", "search_text", "list_dir",
        "task_list", "task_get",
        "note_save",
        "search_vector_asset", "list_asset_candidates",
        "draw_circle", "draw_rect", "draw_line", "draw_text", "draw_ellipse",
        "draw_polygon", "draw_polyline", "draw_path", "draw_concentric_circles",
        "draw_vector_composition", "draw_perspective_vehicle",
    })

    # Canvas editing tools — auto-allow (user explicitly asked for drawing)
    CANVAS_EDIT = frozenset({
        "delete_object", "move_object", "change_color", "resize_object",
        "rotate_object", "arrange_object", "align_object", "distribute_objects",
        "duplicate_object", "group_objects", "ungroup_objects",
        "change_opacity", "change_stroke",
        "select_object", "select_by_region", "select_by_lasso", "select_similar",
        "crop_object", "apply_clip_mask",
        "change_blend_mode", "apply_image_filter",
        "undo", "redo", "clear_canvas",
        "import_vector_asset", "replace_vector_asset",
    })

    # Tools that modify external state — ask user unless already approved.
    SIDE_EFFECT = frozenset({
        "bash", "write_file", "patch_file",
        "task_create", "task_update",
    })

    # Bash CWD-escape patterns — if matched, force ASK.
    _CWD_ESCAPE_PATTERNS = [
        r"\.\.[/\\]",          # ../  ..\
        r"\bcd\s+\.\.",        # cd ..
        r"\bcd\s+/",           # cd /
        r">\s*/",              # redirect to absolute path (e.g. > /tmp/x)
        r"\bsudo\b",           # sudo
        r"\brm\s+-rf\b",       # rm -rf
        r"/etc/", r"/usr/", r"/bin/", r"/boot/",
        r"/proc/", r"/sys/", r"/dev/",
        r"~[/\\\s]",           # ~/ ~\ ~ (tilde expansion)
        r"\$\{?HOME\}?",       # $HOME or ${HOME}
        r"\$\{?PWD\}?",        # $PWD manipulation
    ]
    _CWD_RE = re.compile("|".join(_CWD_ESCAPE_PATTERNS))

    def __init__(self, allow_only: list[str] | None = None):
        self._allowed: set[str] | None = set(allow_only) if allow_only else None
        self._blocked: set[str] = set()
        self._callback: Callable[[str, dict], bool | None] | None = None

    # ── configuration ──────────────────────────────────────────────────

    def block(self, *tool_names: str):
        self._blocked.update(tool_names)

    def unblock(self, *tool_names: str):
        self._blocked.difference_update(tool_names)

    @property
    def allow_only(self) -> set[str] | None:
        return self._allowed

    @allow_only.setter
    def allow_only(self, names: list[str] | None):
        self._allowed = set(names) if names else None

    def set_callback(self, cb: Callable[[str, dict], bool | None] | None):
        self._callback = cb

    # ── CWD escape detection ───────────────────────────────────────────

    @classmethod
    def matches_outside_cwd(cls, command: str) -> bool:
        """Return ``True`` if the shell command escapes the project dir."""
        return bool(cls._CWD_RE.search(command))

    # ── evaluation ─────────────────────────────────────────────────────

    def evaluate(self, tool_name: str, args: dict | None = None) -> PolicyDecision:
        """Synchronous policy decision — no I/O, no user interaction."""
        args = args or {}

        # 1. Allowlist / blocklist
        if self._allowed is not None and tool_name not in self._allowed:
            return PolicyDecision.DENY
        if tool_name in self._blocked:
            return PolicyDecision.DENY

        # 2. Read-only — auto allow
        if tool_name in self.READ_ONLY:
            return PolicyDecision.ALLOW

        # 3. Canvas editing — auto allow (user explicitly asked for drawing)
        if tool_name in self.CANVAS_EDIT:
            return PolicyDecision.ALLOW

        # 4. Bash CWD escape — force ASK (not deny)
        if tool_name == "bash":
            command = args.get("command", "")
            if self.matches_outside_cwd(command):
                return PolicyDecision.ASK
            return PolicyDecision.ASK  # normal bash also needs approval

        # 5. Other side-effect tools — ASK
        if tool_name in self.SIDE_EFFECT:
            return PolicyDecision.ASK

        # 6. Custom callback
        if self._callback is not None:
            cb = self._callback(tool_name, args)
            if cb is True:
                return PolicyDecision.ALLOW
            if cb is False:
                return PolicyDecision.DENY

        # 7. Default for unknown tools
        return PolicyDecision.DENY
