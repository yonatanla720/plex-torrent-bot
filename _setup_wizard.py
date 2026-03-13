#!/usr/bin/env python3
"""Cross-platform interactive setup for Plex Torrent Bot."""

import getpass
import os
import platform
import select
import shutil
import subprocess
import sys
import venv
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)

# --- Colors (disabled on Windows without ANSI support) ---

if sys.platform == "win32":
    os.system("")  # Enable ANSI on Windows 10+

GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def info(msg):
    print(f"{GREEN}[✓]{NC} {msg}")


def warn(msg):
    print(f"{YELLOW}[!]{NC} {msg}")


def err(msg):
    print(f"{RED}[✗]{NC} {msg}")


def ask(prompt, default=""):
    try:
        return input(f"{YELLOW}[?]{NC} {prompt}").strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def ask_yes_no(prompt, default_yes=True):
    suffix = "[Y/n]" if default_yes else "[y/N]"
    answer = ask(f"{prompt} {suffix} ")
    if default_yes:
        return not answer.lower().startswith("n")
    return answer.lower().startswith("y")


def ask_password(prompt):
    try:
        return getpass.getpass(f"{YELLOW}[?]{NC} {prompt}").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def run(cmd, check=True, capture=False, **kwargs):
    """Run a shell command."""
    if capture:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
        if check and result.returncode != 0:
            return None
        return result.stdout.strip()
    return subprocess.run(cmd, shell=True, check=check, **kwargs).returncode == 0


def has_command(name):
    return shutil.which(name) is not None


def _detect_distro():
    """Detect Linux distro ID."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    return line.strip().split("=")[1].strip('"')
    except FileNotFoundError:
        pass
    return ""


def _install_docker():
    """Try to install Docker. Returns True on success."""
    if sys.platform == "darwin":
        if has_command("brew"):
            print("    Installing Docker Desktop via Homebrew...")
            return run("brew install --cask docker", check=False)
        return False

    if sys.platform == "win32":
        if has_command("winget"):
            print("    Installing Docker Desktop via winget...")
            return run(
                "winget install Docker.DockerDesktop "
                "--accept-package-agreements --accept-source-agreements",
                check=False,
            )
        return False

    # Linux
    distro = _detect_distro()
    if distro in ("ubuntu", "debian", "pop", "linuxmint", "raspbian"):
        print("    Installing Docker via official script...")
        if run("curl -fsSL https://get.docker.com | sudo sh", check=False):
            run("sudo usermod -aG docker $USER", check=False)
            warn("You may need to log out and back in for Docker permissions")
            return True
        return False
    if distro in ("fedora",):
        return run(
            "sudo dnf install -y docker-ce docker-ce-cli containerd.io "
            "&& sudo systemctl start docker && sudo systemctl enable docker "
            "&& sudo usermod -aG docker $USER",
            check=False,
        )
    if distro in ("arch", "manjaro"):
        return run(
            "sudo pacman -Sy --noconfirm docker "
            "&& sudo systemctl start docker && sudo systemctl enable docker "
            "&& sudo usermod -aG docker $USER",
            check=False,
        )

    return False


def _show_install_help(tool):
    """Show install instructions for a missing tool."""
    print()
    if tool == "python":
        if sys.platform == "win32":
            err("Install Python 3.10+ from https://www.python.org/downloads/")
            print("    IMPORTANT: Check 'Add Python to PATH' during installation")
        elif sys.platform == "darwin":
            err("Install Python: brew install python@3 or https://www.python.org/downloads/")
        else:
            err("Install Python: sudo apt install python3  (or your distro's package manager)")
    elif tool == "docker":
        if sys.platform == "win32":
            err("Install Docker Desktop: https://www.docker.com/products/docker-desktop/")
        elif sys.platform == "darwin":
            err("Install Docker Desktop: brew install --cask docker")
            print("    Or: https://www.docker.com/products/docker-desktop/")
        else:
            err("Install Docker: https://docs.docker.com/engine/install/")
    print()


# --- Interactive directory browser ---

BLUE = "\033[0;34m"
BOLD = "\033[1m"
DIM = "\033[2m"
REVERSE = "\033[7m"


def _read_key():
    """Read a single keypress. Returns a string identifier."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {
                "H": "up", "P": "down", "K": "left", "M": "right",
                "I": "pageup", "Q": "pagedown", "G": "home", "O": "end",
            }.get(ch2, "")
        if ch == "\r":
            return "enter"
        if ch == "\x08":
            return "backspace"
        if ch == "\x1b":
            return "escape"
        return ch
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    ch2 = sys.stdin.read(1)
                    if ch2 == "[":
                        ch3 = sys.stdin.read(1)
                        if ch3 in ("5", "6"):
                            sys.stdin.read(1)  # consume trailing '~'
                            return {"5": "pageup", "6": "pagedown"}[ch3]
                        return {
                            "A": "up", "B": "down", "C": "right", "D": "left",
                            "H": "home", "F": "end",
                        }.get(ch3, "")
                return "escape"
            if ch in ("\r", "\n"):
                return "enter"
            if ch in ("\x7f", "\x08"):
                return "backspace"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_browser(prompt_label, current, entries, cursor_idx, scroll_offset,
                    page_size, prev_lines):
    """Render the directory browser UI in-place. Returns number of lines drawn."""
    lines = []
    lines.append(f"  {BOLD}{prompt_label}{NC}")
    lines.append(f"  {DIM}Current: {current}{NC}")
    lines.append("")
    lines.append(
        f"  {DIM}\u2191\u2193:Navigate  Enter:Open  \u2190/Bksp:Up"
        f"  s:Select  n:New folder  /:Type path  q:Cancel{NC}"
    )
    lines.append("")

    visible_end = min(scroll_offset + page_size, len(entries))

    if scroll_offset > 0:
        lines.append(f"  {DIM}  \u2191 {scroll_offset} more{NC}")

    for i in range(scroll_offset, visible_end):
        name = entries[i]
        if i == cursor_idx:
            lines.append(f"  {REVERSE} \u25b8 {name} {NC}")
        elif name == "../":
            lines.append(f"    {DIM}{name}{NC}")
        else:
            lines.append(f"    {BLUE}{name}{NC}")

    if len(entries) == 1:  # only "../"
        lines.append(f"    {DIM}(no subdirectories){NC}")

    remaining = len(entries) - visible_end
    if remaining > 0:
        lines.append(f"  {DIM}  \u2193 {remaining} more{NC}")

    lines.append("")
    lines.append(f"  {DIM}[{cursor_idx + 1}/{len(entries)}]{NC}")

    # Overwrite previous frame
    if prev_lines[0] > 0:
        sys.stdout.write(f"\033[{prev_lines[0]}A\033[J")

    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()
    prev_lines[0] = len(lines)


def browse_directory(start_path=None, prompt_label="Select directory"):
    """Interactive arrow-key directory browser. Returns selected path or None."""
    if start_path and Path(start_path).is_dir():
        current = Path(start_path).resolve()
    elif sys.platform == "win32":
        current = Path.home()
    else:
        current = Path("/")

    # Save terminal state on Unix for safety
    _saved_termios = None
    if sys.platform != "win32":
        import termios
        try:
            _saved_termios = termios.tcgetattr(sys.stdin.fileno())
        except Exception:
            pass

    prev_lines = [0]

    try:
        while True:
            try:
                dirs = sorted(
                    [d for d in current.iterdir()
                     if d.is_dir() and not d.name.startswith(".")],
                    key=lambda d: d.name.lower(),
                )
            except PermissionError:
                current = current.parent
                continue

            entries = ["../"] + [d.name + "/" for d in dirs]
            dir_paths = [current.parent] + dirs
            cursor_idx = min(1, len(entries) - 1)
            scroll_offset = 0
            page_size = max(5, shutil.get_terminal_size().lines - 10)

            _render_browser(prompt_label, current, entries, cursor_idx,
                            scroll_offset, page_size, prev_lines)

            while True:
                key = _read_key()

                if key == "up":
                    cursor_idx = max(0, cursor_idx - 1)
                elif key == "down":
                    cursor_idx = min(len(entries) - 1, cursor_idx + 1)
                elif key == "pageup":
                    cursor_idx = max(0, cursor_idx - page_size)
                elif key == "pagedown":
                    cursor_idx = min(len(entries) - 1, cursor_idx + page_size)
                elif key == "home":
                    cursor_idx = 0
                elif key == "end":
                    cursor_idx = len(entries) - 1
                elif key in ("enter", "right"):
                    target = dir_paths[cursor_idx]
                    if target.is_dir():
                        current = target.resolve()
                        break
                elif key in ("backspace", "left"):
                    current = current.parent.resolve()
                    break
                elif key == "s":
                    print()
                    return str(current)
                elif key == "n":
                    # Create a new directory inside current
                    print()
                    try:
                        name = input(
                            f"  {YELLOW}[?]{NC} New folder name: "
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        name = ""
                    if name:
                        new_dir = current / name
                        try:
                            new_dir.mkdir(parents=True, exist_ok=True)
                            print(f"  {GREEN}[✓]{NC} Created: {new_dir}")
                            current = new_dir.resolve()
                        except Exception as e:
                            print(f"  {RED}[✗]{NC} Failed: {e}")
                    prev_lines[0] = 0
                    break
                elif key == "/":
                    # Switch to text input for direct path entry
                    print()
                    try:
                        path_str = input(
                            f"  {YELLOW}[?]{NC} Type path: "
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        path_str = ""
                    if path_str:
                        target = Path(os.path.expanduser(path_str))
                        if target.is_dir():
                            current = target.resolve()
                        else:
                            print(f"  {YELLOW}[!]{NC} Not a directory: {path_str}")
                    prev_lines[0] = 0
                    break
                elif key in ("q", "escape"):
                    print()
                    return None
                else:
                    continue

                # Adjust scroll to keep cursor visible
                if cursor_idx < scroll_offset:
                    scroll_offset = cursor_idx
                if cursor_idx >= scroll_offset + page_size:
                    scroll_offset = cursor_idx - page_size + 1

                _render_browser(prompt_label, current, entries, cursor_idx,
                                scroll_offset, page_size, prev_lines)
    except Exception:
        print()
        return None
    finally:
        if _saved_termios is not None:
            import termios
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                                  _saved_termios)
            except Exception:
                pass


def ask_directory(label, current_value="", default=""):
    """Ask user for a directory path with option to browse."""
    display = current_value or default
    print(f"  {DIM}Enter a path, or 'b' to browse interactively{NC}")
    choice = ask(f"{label} [{display}]: ")

    if not choice:
        return ""

    if choice.lower() == "b":
        start = current_value or default or str(Path.home())
        result = browse_directory(start_path=start, prompt_label=label)
        if result:
            info(f"Selected: {result}")
            return result
        return ""

    return choice


# --- YAML helpers ---

def _load_yaml():
    """Load config.yaml using only stdlib (before pyyaml is installed)."""
    cfg_path = SCRIPT_DIR / "config.yaml"
    if not cfg_path.exists():
        return None
    # Try pyyaml first, fall back to basic parsing
    try:
        import yaml
        with open(cfg_path) as f:
            return yaml.safe_load(f)
    except ImportError:
        return None


def _ensure_yaml():
    """Ensure pyyaml is available (install into venv if needed)."""
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def read_cfg(key_path):
    """Read a dotted key from config.yaml (e.g. 'telegram.bot_token')."""
    try:
        import yaml
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        keys = key_path.split(".")
        v = cfg
        for k in keys:
            v = (v or {}).get(k, "")
        return str(v) if v else ""
    except Exception:
        return ""


def write_cfg(key_path, value):
    """Write a dotted key to config.yaml."""
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    keys = key_path.split(".")
    d = cfg
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    # Try to cast to int
    try:
        value = int(value)
    except (ValueError, TypeError):
        pass
    d[keys[-1]] = value
    with open("config.yaml", "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)


def write_cfg_list(key_path, values):
    """Write a list value to config.yaml."""
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    keys = key_path.split(".")
    d = cfg
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = values
    with open("config.yaml", "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)


# --- Venv helpers ---

def get_venv_python():
    """Get the path to the venv's python executable."""
    venv_dir = SCRIPT_DIR / "venv"
    if sys.platform == "win32":
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python")


def get_venv_pip():
    """Get the path to the venv's pip executable."""
    venv_dir = SCRIPT_DIR / "venv"
    if sys.platform == "win32":
        return str(venv_dir / "Scripts" / "pip.exe")
    return str(venv_dir / "bin" / "pip")


# =============================================
#   Main setup
# =============================================

def main():
    print("=========================================")
    print("  Plex Torrent Bot — Setup")
    print("=========================================")
    print()

    # --- Check prerequisites ---

    # --- Python version check ---

    if sys.version_info < (3, 10):
        err(f"Python 3.10+ required (found {platform.python_version()})")
        _show_install_help("python")
        sys.exit(1)

    info(f"Python {platform.python_version()}")

    # --- Docker check + auto-install ---

    if not has_command("docker"):
        warn("Docker not found")
        if ask_yes_no("Install Docker automatically?"):
            if _install_docker():
                info("Docker installed")
            else:
                _show_install_help("docker")
                sys.exit(1)
        else:
            _show_install_help("docker")
            sys.exit(1)

    docker_ver = run("docker --version", capture=True) or "Docker"
    info(docker_ver)

    # --- Python venv + dependencies ---

    venv_dir = SCRIPT_DIR / "venv"
    if not venv_dir.exists():
        print()
        info("Creating Python virtual environment...")
        venv.create(str(venv_dir), with_pip=True)
    else:
        info("Virtual environment already exists")

    pip = get_venv_pip()
    info("Installing Python dependencies...")
    pip_flags = ["--disable-pip-version-check", "-q"]
    subprocess.run([pip, "install", *pip_flags, "-r", "requirements.txt"], check=True)
    subprocess.run([pip, "install", *pip_flags, "python-telegram-bot[job-queue]"], check=True)
    subprocess.run([pip, "install", *pip_flags, "watchdog"], check=True)
    info("Python dependencies installed")

    # Add venv's site-packages to current process so we can import yaml
    py = get_venv_python()
    sp_lines = subprocess.run(
        [py, "-c",
         "import site; print('\\n'.join(site.getsitepackages()))"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    for sp in sp_lines:
        if sp and sp not in sys.path:
            sys.path.insert(0, sp)
    # Also add the user site-packages (some Windows setups need this)
    user_sp = subprocess.run(
        [py, "-c", "import site; print(site.getusersitepackages())"],
        capture_output=True, text=True,
    ).stdout.strip()
    if user_sp and user_sp not in sys.path and os.path.isdir(user_sp):
        sys.path.insert(0, user_sp)
    # Verify yaml is now importable
    try:
        import yaml  # noqa: F401
    except ImportError:
        # Last resort: find site-packages directly in the venv
        for sp_dir in venv_dir.rglob("site-packages"):
            if sp_dir.is_dir() and str(sp_dir) not in sys.path:
                sys.path.insert(0, str(sp_dir))
                break

    # --- Config file ---

    print()
    cfg_path = SCRIPT_DIR / "config.yaml"
    if not cfg_path.exists():
        warn("config.yaml not found — creating from template")
        shutil.copy("config.yaml.example", "config.yaml")
    else:
        info("config.yaml exists")

    # =========================================
    #   Step 1: Telegram Bot
    # =========================================

    print()
    print("-----------------------------------------")
    print("  Step 1: Telegram Bot")
    print("-----------------------------------------")
    print()
    print("  To create a Telegram bot:")
    print("    1. Open Telegram and search for @BotFather")
    print("    2. Send /newbot and follow the prompts")
    print("    3. Copy the bot token (looks like 123456:ABC-DEF...)")
    print()

    current_token = read_cfg("telegram.bot_token")
    if current_token and current_token != "YOUR_BOT_TOKEN":
        info(f"Bot token already set ({current_token[:10]}...)")
        if ask_yes_no("Change it?", default_yes=False):
            token = ask("Paste your bot token: ")
            if token:
                write_cfg("telegram.bot_token", token)
                info("Bot token saved")
    else:
        token = ask("Paste your bot token: ")
        if token:
            write_cfg("telegram.bot_token", token)
            info("Bot token saved")
        else:
            warn("Skipped — set telegram.bot_token in config.yaml later")

    print()
    print("  To find your Telegram user ID:")
    print("    1. Search for @userinfobot on Telegram")
    print("    2. Send it any message — it replies with your ID")
    print()

    current_uid = read_cfg("telegram.allowed_users")
    placeholder_uids = {"YOUR_TELEGRAM_ID", "[YOUR_TELEGRAM_ID]", "['YOUR_TELEGRAM_ID']", ""}
    if current_uid not in placeholder_uids:
        info(f"User ID already set ({current_uid})")
        if ask_yes_no("Change it?", default_yes=False):
            uid = ask("Enter your Telegram user ID (number): ")
            if uid:
                write_cfg_list("telegram.allowed_users", [int(uid)])
                info("User ID saved")
    else:
        uid = ask("Enter your Telegram user ID (number): ")
        if uid:
            write_cfg_list("telegram.allowed_users", [int(uid)])
            info("User ID saved")
        else:
            warn("Skipped — set telegram.allowed_users in config.yaml later")

    # =========================================
    #   Step 2: Download paths
    # =========================================

    print()
    print("-----------------------------------------")
    print("  Step 2: Download paths")
    print("-----------------------------------------")
    print()
    print("  Set the directories where movies and TV shows will be saved.")
    print("  These should match your Plex library paths.")
    print()

    default_movies = "/mnt/media/Movies"
    default_tv = "/mnt/media/TV Shows"

    current_movies = read_cfg("paths.movies")
    if current_movies and current_movies != default_movies:
        info(f"Movies path: {current_movies}")
        if ask_yes_no("Change it?", default_yes=False):
            mpath = ask_directory("Movies download path", current_movies)
            if mpath:
                write_cfg("paths.movies", mpath)
                info("Movies path saved")
    else:
        mpath = ask_directory("Movies download path", current_movies, default_movies)
        if mpath:
            write_cfg("paths.movies", mpath)
            info("Movies path saved")

    current_tv = read_cfg("paths.tv")
    if current_tv and current_tv != default_tv:
        info(f"TV path: {current_tv}")
        if ask_yes_no("Change it?", default_yes=False):
            tpath = ask_directory("TV shows download path", current_tv)
            if tpath:
                write_cfg("paths.tv", tpath)
                info("TV path saved")
    else:
        tpath = ask_directory("TV shows download path", current_tv, default_tv)
        if tpath:
            write_cfg("paths.tv", tpath)
            info("TV path saved")

    # --- Read final paths for Docker volumes ---

    movie_path = read_cfg("paths.movies")
    tv_path = read_cfg("paths.tv")

    if movie_path and tv_path:
        media_root = os.path.commonpath([movie_path, tv_path])
    elif movie_path:
        media_root = str(Path(movie_path).parent)
    elif tv_path:
        media_root = str(Path(tv_path).parent)
    else:
        media_root = ""

    # --- Create media directories ---

    print()
    for d in [movie_path, tv_path]:
        if d:
            p = Path(d)
            if p.exists():
                info(f"Media dir exists: {d}")
            elif ask_yes_no(f"Create media directory {d}?"):
                try:
                    p.mkdir(parents=True, exist_ok=True)
                    info(f"Created: {d}")
                except PermissionError:
                    warn(f"Permission denied — create {d} manually")

    # =========================================
    #   Docker containers
    # =========================================

    print()
    print("-----------------------------------------")
    print("  Docker containers")
    print("-----------------------------------------")
    print()

    # --- Docker: shared network ---

    net_check = run("docker network inspect media", check=False, capture=True)
    if net_check is not None:
        info("Docker network 'media' already exists")
    else:
        info("Creating Docker network 'media'...")
        run("docker network create media")

    def _container_status(name):
        """Check if a Docker container exists and is running.
        Returns ('running', 'stopped', or 'missing').
        """
        result = run(f"docker inspect --format={{{{.State.Running}}}} {name}",
                     check=False, capture=True)
        if result is None:
            return "missing"
        return "running" if result.strip() == "true" else "stopped"

    def _ensure_container(name, create_cmd, post_msg="", post_warn=""):
        """Ensure a Docker container exists and is running."""
        status = _container_status(name)
        if status == "running":
            info(f"{name} container is running")
        elif status == "stopped":
            info(f"Starting existing {name} container...")
            run(f"docker start {name}")
        else:
            info(f"Creating {name} container...")
            run(create_cmd, check=False)
            # Check if it actually started
            if _container_status(name) != "missing":
                if post_msg:
                    info(post_msg)
                if post_warn:
                    warn(post_warn)
            else:
                err(f"Failed to create {name} container")
        run(f"docker network connect media {name}", check=False, capture=True)

    # --- Docker: Jackett ---

    print()
    config_dir = Path.home() / ".config" / "jackett"
    config_dir.mkdir(parents=True, exist_ok=True)
    _ensure_container(
        "jackett",
        f'docker run -d --name jackett --network media '
        f'-p 9117:9117 -v "{config_dir}:/config" '
        f'--restart unless-stopped lscr.io/linuxserver/jackett:latest',
        post_msg="Jackett started on port 9117",
    )

    # --- Docker: qBittorrent ---

    print()
    config_dir = Path.home() / ".config" / "qbittorrent"
    config_dir.mkdir(parents=True, exist_ok=True)
    vol = f'-v "{config_dir}:/config"'
    if media_root:
        vol += f' -v "{media_root}:{media_root}"'
    _ensure_container(
        "qbittorrent",
        f'docker run -d --name qbittorrent --network media '
        f'-p 8080:8080 -p 6881:6881 {vol} '
        f'--restart unless-stopped lscr.io/linuxserver/qbittorrent:latest',
        post_msg="qBittorrent started on port 8080",
        post_warn="Default login: admin / adminadmin (check container logs for temp password)",
    )

    # --- Docker: FlareSolverr (optional) ---

    print()
    status = _container_status("flaresolverr")
    if status != "missing":
        _ensure_container("flaresolverr", "")
    elif ask_yes_no("Install FlareSolverr? (bypasses Cloudflare for some indexers)"):
        _ensure_container(
            "flaresolverr",
            'docker run -d --name flaresolverr --network media '
            '-p 8191:8191 --restart unless-stopped '
            'ghcr.io/flaresolverr/flaresolverr:latest',
            post_msg="FlareSolverr started on port 8191",
            post_warn="Set FlareSolverr URL in Jackett to: http://flaresolverr:8191",
        )

    # =========================================
    #   Step 3: Jackett configuration
    # =========================================

    print()
    print("-----------------------------------------")
    print("  Step 3: Jackett (torrent indexer)")
    print("-----------------------------------------")
    print()
    print("  Jackett aggregates torrent search across many indexers.")
    print()
    print("    1. Open Jackett at http://localhost:9117")
    print("    2. Set an admin password if prompted")
    print("    3. Copy the API key from the top-right of the dashboard")
    print("    4. Click 'Add indexer' and add your preferred sites:")
    print("       - 1337x, The Pirate Bay, EZTV, YTS, LimeTorrents, etc.")
    print("       - Some indexers (1337x, EZTV) need FlareSolverr for Cloudflare bypass")
    print("    5. Test each indexer with the 'Test' button")
    print()

    current_jkey = read_cfg("jackett.api_key")
    if current_jkey and current_jkey != "YOUR_JACKETT_API_KEY":
        info("Jackett API key already set")
        if ask_yes_no("Change it?", default_yes=False):
            jkey = ask("Paste Jackett API key: ")
            if jkey:
                write_cfg("jackett.api_key", jkey)
                info("Jackett API key saved")
    else:
        jkey = ask("Paste Jackett API key (or Enter to skip): ")
        if jkey:
            write_cfg("jackett.api_key", jkey)
            info("Jackett API key saved")
        else:
            warn("Skipped — set jackett.api_key in config.yaml after configuring Jackett")

    # =========================================
    #   Step 4: qBittorrent configuration
    # =========================================

    print()
    print("-----------------------------------------")
    print("  Step 4: qBittorrent")
    print("-----------------------------------------")
    print()
    print("  qBittorrent handles the actual torrent downloads.")
    print()
    print("    1. Open qBittorrent Web UI at http://localhost:8080")
    print("    2. Check container logs for the temporary password:")
    print("       docker logs qbittorrent 2>&1 | grep 'temporary password'")
    print("    3. Log in with username 'admin' and the temp password")
    print("    4. Go to Settings > Web UI > change the password")
    print("    5. The bot connects to qBittorrent via its Docker IP.")
    print("       Find it with:")
    print("       docker inspect qbittorrent -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'")
    print()

    current_qb_host = read_cfg("qbittorrent.host")
    if current_qb_host and current_qb_host != "localhost":
        info(f"qBittorrent host: {current_qb_host}")
        if ask_yes_no("Change it?", default_yes=False):
            qbhost = ask("qBittorrent host (IP or hostname): ")
            if qbhost:
                write_cfg("qbittorrent.host", qbhost)
                info("qBittorrent host saved")
    else:
        # Try to auto-detect Docker IP
        qb_ip = run(
            "docker inspect qbittorrent -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}'",
            check=False, capture=True,
        )
        if qb_ip:
            qb_ip = qb_ip.split()[0]
        if qb_ip:
            info(f"Detected qBittorrent Docker IP: {qb_ip}")
            if ask_yes_no(f"Use {qb_ip}?"):
                write_cfg("qbittorrent.host", qb_ip)
                info("qBittorrent host saved")
            else:
                qbhost = ask("qBittorrent host (IP or hostname): ")
                if qbhost:
                    write_cfg("qbittorrent.host", qbhost)
                    info("qBittorrent host saved")
        else:
            qbhost = ask("qBittorrent host [localhost]: ", default="localhost")
            write_cfg("qbittorrent.host", qbhost)
            info("qBittorrent host saved")

    current_qb_user = read_cfg("qbittorrent.username")
    current_qb_pass = read_cfg("qbittorrent.password")
    if current_qb_user != "admin" or current_qb_pass != "adminadmin":
        info("qBittorrent credentials already configured")
        if ask_yes_no("Change them?", default_yes=False):
            qbuser = ask("qBittorrent username: ")
            qbpass = ask_password("qBittorrent password: ")
            if qbuser:
                write_cfg("qbittorrent.username", qbuser)
            if qbpass:
                write_cfg("qbittorrent.password", qbpass)
            info("qBittorrent credentials saved")
    else:
        warn("qBittorrent still using default credentials")
        qbuser = ask("Enter qBittorrent username [admin]: ", default="admin")
        qbpass = ask_password("Enter qBittorrent password: ")
        write_cfg("qbittorrent.username", qbuser)
        if qbpass:
            write_cfg("qbittorrent.password", qbpass)
        info("qBittorrent credentials saved")

    # =========================================
    #   Step 5: Plex (optional)
    # =========================================

    print()
    print("-----------------------------------------")
    print("  Step 5: Plex (optional)")
    print("-----------------------------------------")
    print()
    print("  The bot can trigger a Plex library scan when downloads finish.")
    print()
    print("  To find your Plex token:")
    print("    1. Open Plex Web App and play any media")
    print("    2. Click the '...' menu > 'Get Info' > 'View XML'")
    print("    3. In the URL bar, find: X-Plex-Token=<your_token>")
    print("    Or visit: https://support.plex.tv/articles/204059436/")
    print()
    print("  For the Plex URL, use your server's LAN IP (not localhost).")
    print("  Example: http://192.168.1.100:32400")
    print()

    if ask_yes_no("Set up Plex integration?"):
        current_plex_url = read_cfg("plex.url")
        if current_plex_url:
            info(f"Plex URL: {current_plex_url}")
            if ask_yes_no("Change it?", default_yes=False):
                purl = ask("Plex URL (e.g. http://192.168.1.100:32400): ")
                if purl:
                    write_cfg("plex.url", purl)
                    info("Plex URL saved")
        else:
            purl = ask("Plex URL (e.g. http://192.168.1.100:32400): ")
            if purl:
                write_cfg("plex.url", purl)
                info("Plex URL saved")

        current_plex_token = read_cfg("plex.token")
        if current_plex_token:
            info("Plex token already set")
            if ask_yes_no("Change it?", default_yes=False):
                ptoken = ask("Plex token: ")
                if ptoken:
                    write_cfg("plex.token", ptoken)
                    info("Plex token saved")
        else:
            ptoken = ask("Plex token: ")
            if ptoken:
                write_cfg("plex.token", ptoken)
                info("Plex token saved")
    else:
        info("Plex integration skipped")

    # --- Summary ---

    print()
    print("=========================================")
    print("  Setup complete!")
    print("=========================================")
    print()

    venv_activate = "venv\\Scripts\\activate" if sys.platform == "win32" else "source venv/bin/activate"
    run_cmd = "python bot.py"

    print(f"  Run the bot:")
    print(f"    {venv_activate}")
    print(f"    {run_cmd}")
    print()
    if sys.platform != "win32":
        print("  For development with auto-reload:")
        print("    ./run.sh")
        print()
    print("  To re-run this setup:")
    rerun = "setup.bat" if sys.platform == "win32" else "./setup.sh"
    print(f"    {rerun}")
    print()


if __name__ == "__main__":
    main()
