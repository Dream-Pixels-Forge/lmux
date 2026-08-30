"""Hooks setup for auto-installing hooks for 15+ AI agents."""
import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger("lmux.hooks_setup")

# Agent hook configurations
AGENT_HOOKS: Dict[str, Dict[str, Any]] = {
    "claude-code": {
        "name": "Claude Code",
        "config_path": "~/.claude/settings.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- claude --resume",
                "description": "Resume Claude session in lmux"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- claude",
                "description": "Spawn Claude for prompt"
            },
            "stop": {
                "command": "lmux notification create --title 'Claude stopped' --body 'Agent session ended'",
                "description": "Notify when Claude stops"
            }
        }
    },
    "opencode": {
        "name": "OpenCode",
        "config_path": "~/.opencode/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- opencode --resume",
                "description": "Resume OpenCode session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- opencode",
                "description": "Spawn OpenCode"
            }
        }
    },
    "codex": {
        "name": "Codex",
        "config_path": "~/.codex/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- codex --resume",
                "description": "Resume Codex session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- codex",
                "description": "Spawn Codex"
            },
            "stop": {
                "command": "lmux notification create --title 'Codex stopped'",
                "description": "Notify when Codex stops"
            }
        }
    },
    "aider": {
        "name": "Aider",
        "config_path": "~/.aider.conf.yml",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- aider --resume",
                "description": "Resume Aider session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- aider",
                "description": "Spawn Aider"
            }
        }
    },
    "goose": {
        "name": "Goose",
        "config_path": "~/.config/goose/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- goose --resume",
                "description": "Resume Goose session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- goose",
                "description": "Spawn Goose"
            }
        }
    },
    "grok": {
        "name": "Grok",
        "config_path": "~/.grok/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- grok --resume",
                "description": "Resume Grok session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- grok",
                "description": "Spawn Grok"
            }
        }
    },
    "pi": {
        "name": "Pi",
        "config_path": "~/.pi/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- pi --resume",
                "description": "Resume Pi session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- pi",
                "description": "Spawn Pi"
            }
        }
    },
    "amp": {
        "name": "Amp",
        "config_path": "~/.amp/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- amp --resume",
                "description": "Resume Amp session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- amp",
                "description": "Spawn Amp"
            }
        }
    },
    "cursor": {
        "name": "Cursor CLI",
        "config_path": "~/.cursor/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- cursor --resume",
                "description": "Resume Cursor session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- cursor",
                "description": "Spawn Cursor"
            }
        }
    },
    "gemini": {
        "name": "Gemini",
        "config_path": "~/.gemini/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- gemini --resume",
                "description": "Resume Gemini session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- gemini",
                "description": "Spawn Gemini"
            }
        }
    },
    "kiro": {
        "name": "Kiro",
        "config_path": "~/.kiro/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- kiro --resume",
                "description": "Resume Kiro session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- kiro",
                "description": "Spawn Kiro"
            }
        }
    },
    "antigravity": {
        "name": "Antigravity",
        "config_path": "~/.antigravity/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- antigravity --resume",
                "description": "Resume Antigravity session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- antigravity",
                "description": "Spawn Antigravity"
            }
        }
    },
    "rovo-dev": {
        "name": "Rovo Dev",
        "config_path": "~/.rovo-dev/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- rovo-dev --resume",
                "description": "Resume Rovo Dev session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- rovo-dev",
                "description": "Spawn Rovo Dev"
            }
        }
    },
    "hermes": {
        "name": "Hermes Agent",
        "config_path": "~/.hermes/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- hermes --resume",
                "description": "Resume Hermes session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- hermes",
                "description": "Spawn Hermes"
            }
        }
    },
    "copilot": {
        "name": "Copilot",
        "config_path": "~/.copilot/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- copilot --resume",
                "description": "Resume Copilot session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- copilot",
                "description": "Spawn Copilot"
            }
        }
    },
    "codebuddy": {
        "name": "CodeBuddy",
        "config_path": "~/.codebuddy/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- codebuddy --resume",
                "description": "Resume CodeBuddy session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- codebuddy",
                "description": "Spawn CodeBuddy"
            }
        }
    },
    "factory": {
        "name": "Factory",
        "config_path": "~/.factory/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- factory --resume",
                "description": "Resume Factory session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- factory",
                "description": "Spawn Factory"
            }
        }
    },
    "qoder": {
        "name": "Qoder",
        "config_path": "~/.qoder/config.json",
        "hooks": {
            "session-start": {
                "command": "lmux agent.spawn -- qoder --resume",
                "description": "Resume Qoder session"
            },
            "prompt-submit": {
                "command": "lmux agent.spawn -- qoder",
                "description": "Spawn Qoder"
            }
        }
    },
}


@dataclass
class HookInstallResult:
    """Result of hook installation."""
    agent: str
    success: bool
    message: str
    hooks_installed: List[str]


class HooksSetup:
    """Auto-install hooks for AI agents."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._hooks_file = self._config_dir / "hooks.json"
        self._installed_hooks = self._load_installed()

    def _load_installed(self) -> Dict[str, List[str]]:
        """Load installed hooks from file."""
        if self._hooks_file.exists():
            try:
                with open(self._hooks_file, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_installed(self):
        """Save installed hooks to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._hooks_file, "w") as f:
            json.dump(self._installed_hooks, f, indent=2)

    def setup_agent(self, agent: str, force: bool = False) -> HookInstallResult:
        """Set up hooks for a specific agent."""
        if agent not in AGENT_HOOKS:
            return HookInstallResult(
                agent=agent,
                success=False,
                message=f"Unknown agent: {agent}",
                hooks_installed=[]
            )

        agent_config = AGENT_HOOKS[agent]
        installed = []

        for event, hook_config in agent_config["hooks"].items():
            if not force and event in self._installed_hooks.get(agent, []):
                continue

            try:
                # Create the hook via lmux hooks.add
                cmd = [
                    "lmux", "hooks", "add",
                    event,
                    hook_config["command"]
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result.returncode == 0:
                    installed.append(event)
                else:
                    logger.warning(f"Failed to install hook {event} for {agent}: {result.stderr}")
            except Exception as e:
                logger.error(f"Error installing hook {event} for {agent}: {e}")

        # Update installed list
        if agent not in self._installed_hooks:
            self._installed_hooks[agent] = []
        self._installed_hooks[agent].extend(installed)
        self._save_installed()

        return HookInstallResult(
            agent=agent,
            success=len(installed) > 0,
            message=f"Installed {len(installed)} hooks" if installed else "No hooks installed",
            hooks_installed=installed
        )

    def setup_all(self, force: bool = False) -> List[HookInstallResult]:
        """Set up hooks for all known agents."""
        results = []
        for agent in AGENT_HOOKS:
            result = self.setup_agent(agent, force=force)
            results.append(result)
        return results

    def list_agents(self) -> List[Dict[str, Any]]:
        """List all known agents and their status."""
        agents = []
        for agent_id, config in AGENT_HOOKS.items():
            installed = self._installed_hooks.get(agent_id, [])
            agents.append({
                "id": agent_id,
                "name": config["name"],
                "config_path": config["config_path"],
                "hooks_count": len(config["hooks"]),
                "installed_count": len(installed),
                "installed_hooks": installed
            })
        return agents

    def uninstall_agent(self, agent: str) -> bool:
        """Uninstall hooks for a specific agent."""
        if agent not in self._installed_hooks:
            return False

        for event in self._installed_hooks[agent]:
            try:
                cmd = ["lmux", "hooks", "remove", event]
                subprocess.run(cmd, capture_output=True, timeout=5)
            except Exception as e:
                logger.error(f"Error removing hook {event} for {agent}: {e}")

        del self._installed_hooks[agent]
        self._save_installed()
        return True

    def get_setup_report(self) -> str:
        """Generate a human-readable setup report."""
        lines = ["# lmux Hooks Setup Report\n"]

        agents = self.list_agents()
        total_hooks = sum(a["hooks_count"] for a in agents)
        installed_hooks = sum(a["installed_count"] for a in agents)

        lines.append(f"Total agents: {len(agents)}")
        lines.append(f"Total hooks available: {total_hooks}")
        lines.append(f"Total hooks installed: {installed_hooks}\n")

        lines.append("## Agent Status\n")
        for agent in agents:
            status = "✅" if agent["installed_count"] == agent["hooks_count"] else "⚠️"
            lines.append(f"- {status} **{agent['name']}** ({agent['id']})")
            lines.append(f"  - Config: {agent['config_path']}")
            lines.append(f"  - Hooks: {agent['installed_count']}/{agent['hooks_count']}")

            if agent["installed_hooks"]:
                for hook in agent["installed_hooks"]:
                    lines.append(f"    - ✅ {hook}")

        return "\n".join(lines)


# CLI integration
def setup_hooks_cli(args):
    """CLI handler for hooks setup."""
    setup = HooksSetup()

    if args.agent:
        result = setup.setup_agent(args.agent, force=args.force)
        if result.success:
            print(f"✅ Installed {len(result.hooks_installed)} hooks for {result.agent}")
            for hook in result.hooks_installed:
                print(f"  - {hook}")
        else:
            print(f"❌ {result.message}")
    elif args.all:
        results = setup.setup_all(force=args.force)
        success_count = sum(1 for r in results if r.success)
        print(f"✅ Installed hooks for {success_count}/{len(results)} agents")
        for result in results:
            if result.success:
                print(f"  - {result.agent}: {len(result.hooks_installed)} hooks")
    elif args.report:
        print(setup.get_setup_report())
    elif args.list:
        agents = setup.list_agents()
        for agent in agents:
            print(f"{agent['id']}: {agent['installed_count']}/{agent['hooks_count']} hooks")
    elif args.uninstall:
        if setup.uninstall_agent(args.uninstall):
            print(f"✅ Uninstalled hooks for {args.uninstall}")
        else:
            print(f"❌ No hooks installed for {args.uninstall}")
