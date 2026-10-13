"""Custom commands in config for lmux."""
import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("lmux.custom_commands")

# ISS-0016: env keys that must never reach a child process.  They alter
# dynamic linking / interpreter startup (LD_PRELOAD, LD_LIBRARY_PATH,
# PYTHONPATH) or inject shell init (BASH_ENV, ENV).  Stripped from the
# ambient base AND blocked from the per-command overlay.
_STRIPPED_ENV_KEYS = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "BASH_ENV", "ENV",
})


@dataclass
class CustomCommand:
    """A custom command defined in config."""
    name: str
    command: str
    description: str = ""
    shortcut: Optional[str] = None
    category: str = "general"
    confirm: bool = False
    env: Dict[str, str] = field(default_factory=dict)
    shell: bool = False


class CustomCommandsManager:
    """Manages custom commands from config."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._config_file = self._config_dir / "config.json"
        self._commands: Dict[str, CustomCommand] = {}
        self._load_commands()

    def _load_commands(self):
        """Load custom commands from config."""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    config = json.load(f)
                    commands_data = config.get("custom_commands", {})
                    for name, cmd_data in commands_data.items():
                        self._commands[name] = CustomCommand(
                            name=name,
                            command=cmd_data.get("command", ""),
                            description=cmd_data.get("description", ""),
                            shortcut=cmd_data.get("shortcut"),
                            category=cmd_data.get("category", "general"),
                            confirm=cmd_data.get("confirm", False),
                            env=cmd_data.get("env", {}),
                            shell=cmd_data.get("shell", False)
                        )
            except Exception as e:
                logger.error(f"Error loading custom commands: {e}")

    def _save_commands(self):
        """Save custom commands to config."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        config = {}
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    config = json.load(f)
            except Exception:
                pass

        commands_data = {}
        for name, cmd in self._commands.items():
            commands_data[name] = {
                "command": cmd.command,
                "description": cmd.description,
                "shortcut": cmd.shortcut,
                "category": cmd.category,
                "confirm": cmd.confirm,
                "env": cmd.env,
                "shell": cmd.shell
            }

        config["custom_commands"] = commands_data

        with open(self._config_file, "w") as f:
            json.dump(config, f, indent=2)

    def add_command(self, command: CustomCommand):
        """Add a custom command."""
        self._commands[command.name] = command
        self._save_commands()

    def remove_command(self, name: str):
        """Remove a custom command."""
        if name in self._commands:
            del self._commands[name]
            self._save_commands()

    def get_command(self, name: str) -> Optional[CustomCommand]:
        """Get a custom command by name."""
        return self._commands.get(name)

    def list_commands(self, category: Optional[str] = None) -> List[CustomCommand]:
        """List all custom commands."""
        commands = list(self._commands.values())
        if category:
            commands = [c for c in commands if c.category == category]
        return commands

    def execute_command(self, name: str, cwd: Optional[str] = None,
                        confirm_callback: Optional[Callable[[CustomCommand], bool]] = None) -> bool:
        """Execute a custom command.

        ISS-0016: config-supplied strings are untrusted input.  Default
        is ``shlex.split`` + ``shell=False`` so shell metacharacters
        (``;``, ``&&``, backticks, ``$()``) are inert argv text.  A
        command opts into a real shell ONLY with
        ``{"shell": true, "confirm": true}`` in config AND a live
        approval from ``confirm_callback`` (the GUI wires a GTK dialog;
        ``None`` or a refusal fails closed).  ``confirm=true`` on a
        non-shell command keeps the legacy log-only behavior.
        ``confirm_callback`` receives the CustomCommand for review.
        """
        command = self._commands.get(name)
        if not command:
            logger.error(f"Custom command not found: {name}")
            return False

        use_shell = False
        if command.shell:
            # Opt-in path: requires confirm=true in config AND a real
            # approval.  shell=true without confirm=true refuses —
            # a compound string must never run on config alone.
            if not command.confirm:
                logger.warning(
                    f"Refusing shell command '{name}': "
                    f"shell=true requires confirm=true")
                return False
            if confirm_callback is None:
                logger.warning(
                    f"Refusing shell command '{name}': "
                    f"no confirmation prompt wired")
                return False
            try:
                approved = confirm_callback(command)
            except Exception as exc:
                logger.warning(
                    f"Confirmation for '{name}' failed ({exc}); refusing")
                return False
            if not approved:
                logger.info(f"Shell command '{name}' declined by user")
                return False
            use_shell = True
        elif command.confirm:
            # Legacy behavior for non-shell commands: note it, run split.
            logger.info(f"Confirmation required for command: {name}")

        try:
            # Build environment: ambient minus dangerous keys, plus the
            # per-command overlay minus dangerous keys (the overlay must
            # not reintroduce what the base strips).
            import os
            env = {k: v for k, v in os.environ.items()
                   if k not in _STRIPPED_ENV_KEYS}
            for k, v in (command.env or {}).items():
                if k in _STRIPPED_ENV_KEYS:
                    logger.warning(
                        f"Ignoring stripped env key '{k}' in command '{name}'")
                    continue
                env[k] = v

            # Execute command
            if use_shell:
                subprocess.Popen(
                    command.command,
                    shell=True,
                    cwd=cwd,
                    env=env
                )
            else:
                subprocess.Popen(
                    shlex.split(command.command),
                    shell=False,
                    cwd=cwd,
                    env=env
                )
            return True
        except Exception as e:
            logger.error(f"Error executing command {name}: {e}")
            return False

    def get_by_shortcut(self, shortcut: str) -> Optional[CustomCommand]:
        """Get a command by its keyboard shortcut."""
        for command in self._commands.values():
            if command.shortcut == shortcut:
                return command
        return None

    def get_categories(self) -> List[str]:
        """Get all unique command categories."""
        return list(set(c.category for c in self._commands.values()))


def setup_custom_commands_config(config_dir: Optional[str] = None):
    """Set up default custom commands in config if not present."""
    manager = CustomCommandsManager(config_dir)

    # Add some default commands if none exist
    if not manager.list_commands():
        defaults = [
            CustomCommand(
                name="build",
                command="make build",
                description="Build the project",
                category="development"
            ),
            CustomCommand(
                name="test",
                command="make test",
                description="Run tests",
                category="development"
            ),
            CustomCommand(
                name="deploy",
                command="make deploy",
                description="Deploy to production",
                category="deployment",
                confirm=True
            ),
            CustomCommand(
                name="git-status",
                command="git status",
                description="Show git status",
                category="git"
            ),
            CustomCommand(
                name="git-push",
                command="git push",
                description="Push to remote",
                category="git"
            ),
        ]

        for cmd in defaults:
            manager.add_command(cmd)

    return manager
