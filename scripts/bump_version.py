#!/usr/bin/env python3
"""
Semantic Version Bumping Script

Usage:
    python scripts/bump_version.py [major|minor|patch|VERSION]

Examples:
    python scripts/bump_version.py patch      # 0.1.0 -> 0.1.1
    python scripts/bump_version.py minor      # 0.1.0 -> 0.2.0
    python scripts/bump_version.py major      # 0.1.0 -> 1.0.0
    python scripts/bump_version.py 1.0.0      # Set explicit version
    python scripts/bump_version.py 1.0.0-rc1  # Pre-release version
"""

import re
import sys
from pathlib import Path

# Files that contain version strings to update
VERSION_FILE = Path("VERSION")
PYPROJECT_FILE = Path("pyproject.toml")
INIT_FILE = Path("src/__init__.py")
CONFIG_FILE = Path("src/config.py")


def read_version() -> str:
    """Read current version from VERSION file."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "0.0.0"


def parse_version(version: str) -> tuple[int, int, int, str]:
    """Parse semantic version string into components."""
    # Match: major.minor.patch[-prerelease]
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", version)
    if not match:
        raise ValueError(f"Invalid version format: {version}")

    major, minor, patch, prerelease = match.groups()
    return int(major), int(minor), int(patch), prerelease


def bump_version(current: str, bump_type: str) -> str:
    """Bump version according to semantic versioning rules."""
    major, minor, patch, _ = parse_version(current)

    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        # Explicit version provided
        # Validate it's a valid semver
        parse_version(bump_type.lstrip("v"))
        return bump_type.lstrip("v")


def update_version_file(new_version: str) -> None:
    """Update the VERSION file."""
    VERSION_FILE.write_text(f"{new_version}\n")
    print(f"  Updated {VERSION_FILE}")


def update_pyproject(new_version: str) -> None:
    """Update version in pyproject.toml."""
    if not PYPROJECT_FILE.exists():
        return

    content = PYPROJECT_FILE.read_text()
    updated = re.sub(
        r'^version\s*=\s*"[^"]*"',
        f'version = "{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE
    )
    PYPROJECT_FILE.write_text(updated)
    print(f"  Updated {PYPROJECT_FILE}")


def update_init_file(new_version: str) -> None:
    """Update __version__ in src/__init__.py."""
    if not INIT_FILE.exists():
        return

    content = INIT_FILE.read_text()
    updated = re.sub(
        r'^__version__\s*=\s*"[^"]*"',
        f'__version__ = "{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE
    )
    INIT_FILE.write_text(updated)
    print(f"  Updated {INIT_FILE}")


def update_config_file(new_version: str) -> None:
    """Update app_version in src/config.py."""
    if not CONFIG_FILE.exists():
        return

    content = CONFIG_FILE.read_text()
    updated = re.sub(
        r'app_version:\s*str\s*=\s*"[^"]*"',
        f'app_version: str = "{new_version}"',
        content,
        count=1
    )
    CONFIG_FILE.write_text(updated)
    print(f"  Updated {CONFIG_FILE}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    bump_type = sys.argv[1].lower()

    if bump_type in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    current_version = read_version()
    print(f"Current version: {current_version}")

    try:
        new_version = bump_version(current_version, bump_type)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    print(f"New version: {new_version}")
    print()
    print("Updating files:")

    update_version_file(new_version)
    update_pyproject(new_version)
    update_init_file(new_version)
    update_config_file(new_version)

    print()
    print(f"Version bumped to {new_version}")
    print()
    print("Next steps:")
    print(f"  1. git add -A")
    print(f"  2. git commit -m 'chore: bump version to {new_version}'")
    print(f"  3. git tag v{new_version}")
    print(f"  4. git push origin main --tags")

    return 0


if __name__ == "__main__":
    sys.exit(main())
