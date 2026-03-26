import sys
import os
import shutil
from pathlib import Path


def get_resource_path(relative_path: str) -> str:
    """Get absolute path to a bundled read-only resource.
    Works for dev and PyInstaller frozen builds."""
    base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
    return os.path.join(base_path, relative_path)


def get_user_data_path(relative_path: str = "") -> Path:
    """Get path for writable user data (configs, logs).
    
    Points to %APPDATA%\\TrajectoryTools in frozen builds,
    or the current working directory in development.
    """
    if getattr(sys, 'frozen', False):
        base = Path(os.environ.get('APPDATA', Path.home())) / 'TrajectoryTools'
    else:
        base = Path(os.path.abspath("."))
    base.mkdir(parents=True, exist_ok=True)
    return base / relative_path if relative_path else base


def initialize_user_config() -> None:
    """Seed the user's config directory from bundled defaults on first run.
    
    Copies all *.json files from the bundled config directory to
    %APPDATA%\\TrajectoryTools\\config\\ if the destination is empty.
    Existing user configs are never overwritten.
    """
    user_config_dir = get_user_data_path("config")
    user_config_dir.mkdir(parents=True, exist_ok=True)

    # Only seed if no JSON files exist yet
    if any(user_config_dir.glob("*.json")):
        return

    bundled_config_dir = Path(get_resource_path("config"))
    if not bundled_config_dir.exists():
        return

    for src in bundled_config_dir.glob("*.json"):
        dst = user_config_dir / src.name
        shutil.copy2(src, dst)