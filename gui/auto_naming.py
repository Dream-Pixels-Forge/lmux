"""Auto-naming engine for lmux workspaces.

Suggests workspace names based on directory context: git repo names,
package.json names, directory basename, and other heuristics.
"""
import os
import json
import subprocess


# File patterns that indicate a project type
_PROJECT_MARKERS = {
    "package.json": "node",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Gemfile": "ruby",
    "pom.xml": "java",
    "build.gradle": "java",
    "Makefile": "c",
    "CMakeLists.txt": "cpp",
    "mix.exs": "elixir",
}


def _git_repo_name(cwd):
    """Get the name of the git repository root directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.basename(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _git_branch(cwd):
    """Get the current git branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _package_json_name(cwd):
    """Get the name field from package.json."""
    pkg_path = os.path.join(cwd, "package.json")
    if not os.path.isfile(pkg_path):
        return None
    try:
        with open(pkg_path, "r") as f:
            data = json.load(f)
        name = data.get("name", "")
        if name:
            # Strip scope prefix
            if name.startswith("@") and "/" in name:
                name = name.split("/", 1)[1]
            return name
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _detect_project_type(cwd):
    """Detect project type from file markers."""
    for filename, ptype in _PROJECT_MARKERS.items():
        if os.path.isfile(os.path.join(cwd, filename)):
            return ptype
    return None


def suggest_names(cwd):
    """Suggest workspace names for a given directory.

    Returns a list of name suggestions ordered by confidence:
    1. Git repo name (highest confidence)
    2. package.json name
    3. Directory basename
    4. Directory basename + project type

    Args:
        cwd: The directory path to analyze.

    Returns:
        List of suggested name strings.
    """
    if not cwd or not os.path.isdir(cwd):
        return []

    suggestions = []
    seen = set()

    def _add(name):
        if name and name not in seen and len(name) < 64:
            suggestions.append(name)
            seen.add(name)

    # 1. Git repo name
    repo_name = _git_repo_name(cwd)
    if repo_name:
        _add(repo_name)
        # Also suggest repo + branch
        branch = _git_branch(cwd)
        if branch and branch not in ("main", "master"):
            _add(f"{repo_name}:{branch}")

    # 2. package.json name
    pkg_name = _package_json_name(cwd)
    if pkg_name:
        _add(pkg_name)

    # 3. Directory basename
    basename = os.path.basename(cwd.rstrip(os.sep))
    if basename:
        _add(basename)

    # 4. Directory basename + project type
    ptype = _detect_project_type(cwd)
    if ptype and basename:
        _add(f"{basename} ({ptype})")

    return suggestions


class AutoNamer:
    """Automatic workspace naming based on directory context.

    Usage::

        namer = AutoNamer()
        suggestions = namer.suggest("/path/to/project")
        name = namer.pick_best("/path/to/project")
    """

    def __init__(self):
        self._cache = {}  # cwd -> last suggested name

    def suggest(self, cwd):
        """Get name suggestions for a directory.

        Returns:
            List of suggested name strings.
        """
        return suggest_names(cwd)

    def pick_best(self, cwd):
        """Pick the best name suggestion for a directory.

        Returns:
            Best name string, or None if no suggestions.
        """
        names = self.suggest(cwd)
        if names:
            best = names[0]
            self._cache[cwd] = best
            return best
        return None

    def rename_on_create(self, workspace_id, cwd, daemon=None):
        """Suggest and optionally apply a name for a new workspace.

        Args:
            workspace_id: The workspace ID.
            cwd: The working directory.
            daemon: Optional DaemonClient to apply the rename.

        Returns:
            The suggested name, or None.
        """
        name = self.pick_best(cwd)
        if name and daemon:
            try:
                daemon.send("workspace.rename", {"id": workspace_id, "title": name})
            except Exception:
                pass
        return name
