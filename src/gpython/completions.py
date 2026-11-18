"""Shell completion scripts for the `gpython` and `gpy` commands: `gpython --completions SHELL`.

Each script is plain text with the option names and theme names written in, so a press of
tab in the shell never starts Python.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

SHELLS = ("bash", "zsh", "fish", "powershell")
SHELL_ALIASES = {"pwsh": "powershell"}
COMMANDS = ("gpython", "gpy")
NOTEBOOK_ENDINGS = ("py", "ipynb")

THEME, FOLDER, SHELL, TEXT = "theme", "folder", "shell", "text"

OPTIONS: tuple[tuple[tuple[str, ...], str | None, str], ...] = (
    (("-h", "--help"), None, "show the help and exit"),
    (("-v", "--version"), None, "print the version and exit"),
    (("--no-vim",), None, "plain editing, no modal keys"),
    (("--theme",), THEME, "colour theme"),
    (("--groups",), None, "print which cells may run at the same time and exit"),
    (("--update",), None, "get the newest gpython and exit (--update dev: the newest code on GitHub)"),
    (("--scratchpad",), FOLDER, "set the folder where a new notebook is saved and exit"),
    (("--completions",), SHELL, "print the completion script for a shell and exit"),
    (("--install-completions",), None, "set up tab completion for your shell and exit"),
    (("--extra",), TEXT, "install the optional dependencies of the uv project with this name"),
    (("--all-extras",), None, "install all optional dependencies of the uv project"),
    (("--group",), TEXT, "install a dependency group of the uv project"),
    (("--all-groups",), None, "install all dependency groups of the uv project"),
    (("--with",), TEXT, "a package for this session only"),
    (("--no-sync",), None, "use the environment as it is, install nothing"),
)


def theme_names() -> list[str]:
    """Each name `--theme` takes: the built-in themes, gpython's own, and the short names."""
    from textual.theme import BUILTIN_THEMES

    from .themes import CUSTOM_APP_THEMES, TERMINAL, THEME_ALIASES

    return sorted({*BUILTIN_THEMES, *CUSTOM_APP_THEMES, TERMINAL, *THEME_ALIASES})


def _option_names() -> list[str]:
    return [name for names, _, _ in OPTIONS for name in names]


def _long_names(kind: str) -> list[str]:
    return [names[-1] for names, value, _ in OPTIONS if value == kind]


def bash_script(themes: list[str]) -> str:
    endings = "|".join(NOTEBOOK_ENDINGS)
    return f"""_gpython() {{
    local cur prev IFS=$'\\n'
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    COMPREPLY=()
    case "$prev" in
        {"|".join(_long_names(THEME))})
            COMPREPLY=($(compgen -W "{chr(10).join(themes)}" -- "$cur"))
            return ;;
        {"|".join(_long_names(FOLDER))})
            COMPREPLY=($(compgen -d -- "$cur"))
            return ;;
        {"|".join(_long_names(SHELL))})
            COMPREPLY=($(compgen -W "{chr(10).join(SHELLS)}" -- "$cur"))
            return ;;
        {"|".join(_long_names(TEXT))})
            return ;;
    esac
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "{chr(10).join(_option_names())}" -- "$cur"))
        return
    fi
    local name
    for name in $(compgen -f -- "$cur"); do
        if [[ -d "$name" || "$name" =~ \\.({endings})$ ]]; then
            COMPREPLY+=("$name")
        fi
    done
}}
complete -o filenames -F _gpython {" ".join(COMMANDS)}
"""


def _zsh_spec(names: tuple[str, ...], value: str | None, detail: str, themes: list[str]) -> str:
    actions = {
        THEME: f":theme:({' '.join(themes)})",
        FOLDER: ":folder:_files -/",
        SHELL: f":shell:({' '.join(SHELLS)})",
        TEXT: ":name:",
    }
    repeat = "*" if value == TEXT else ""
    action = actions.get(value or "", "")
    equals = "=" if value else ""
    if len(names) == 1:
        return f"'{repeat}{names[0]}{equals}[{detail}]{action}'"
    return f"'({' '.join(names)})'{{{','.join(names)}}}'[{detail}]{action}'"


def zsh_script(themes: list[str]) -> str:
    specs = [_zsh_spec(names, value, detail, themes) for names, value, detail in OPTIONS]
    specs.append(f"""'*:notebook:_files -g "*.({'|'.join(NOTEBOOK_ENDINGS)})"'""")
    lines = " \\\n        ".join(specs)
    return f"""#compdef {" ".join(COMMANDS)}

_gpython() {{
    _arguments -s \\
        {lines}
}}

if [ "$funcstack[1]" = "_gpython" ]; then
    _gpython "$@"
else
    (( $+functions[compdef] )) || {{ autoload -Uz compinit && compinit }}
    compdef _gpython {" ".join(COMMANDS)}
fi
"""


def _fish_line(command: str, names: tuple[str, ...], value: str | None, detail: str, themes: list[str]) -> str:
    values = {
        THEME: f" -x -a '{' '.join(themes)}'",
        FOLDER: " -x -a '(__fish_complete_directories)'",
        SHELL: f" -x -a '{' '.join(SHELLS)}'",
        TEXT: " -x",
    }
    flags = "".join(f" -l {name[2:]}" if name.startswith("--") else f" -s {name[1:]}" for name in names)
    takes = values.get(value or "", "")
    return f"complete -c {command}{flags}{takes} -d '{detail}'"


def fish_script(themes: list[str]) -> str:
    lines = [_fish_line(command, names, value, detail, themes) for command in COMMANDS for names, value, detail in OPTIONS]
    return "\n".join(lines) + "\n"


def _powershell_list(words: list[str]) -> str:
    return "@(" + ", ".join(f"'{word}'" for word in words) + ")"


def powershell_script(themes: list[str]) -> str:
    options = ",\n        ".join(f"@('{name}', '{detail}')" for names, _, detail in OPTIONS for name in names)
    return f"""Register-ArgumentCompleter -Native -CommandName {", ".join(COMMANDS)} -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $options = @(
        {options}
    )
    $words = @($commandAst.CommandElements | ForEach-Object {{ $_.ToString() }})
    $previous = if ($wordToComplete) {{ $words[-2] }} else {{ $words[-1] }}
    $values = $null
    if ($previous -in {_powershell_list(_long_names(THEME))}) {{ $values = {_powershell_list(themes)} }}
    if ($previous -in {_powershell_list(_long_names(SHELL))}) {{ $values = {_powershell_list(list(SHELLS))} }}
    if ($null -ne $values) {{
        $values | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }}
        return
    }}
    if ($wordToComplete -like '-*') {{
        $options | Where-Object {{ $_[0] -like "$wordToComplete*" }} | ForEach-Object {{
            [System.Management.Automation.CompletionResult]::new($_[0], $_[0], 'ParameterName', $_[1])
        }}
    }}
}}
"""


SCRIPTS = {"bash": bash_script, "zsh": zsh_script, "fish": fish_script, "powershell": powershell_script}


def shell_name(shell: str) -> str | None:
    """The name in SHELLS for what was typed (`pwsh` is powershell), or None."""
    name = SHELL_ALIASES.get(shell.lower(), shell.lower())
    return name if name in SHELLS else None


def script(shell: str) -> str:
    """The completion script for `shell` (one of SHELLS)."""
    return SCRIPTS[shell](theme_names())


SCRIPT_NAMES = {"bash": "gpython.bash", "zsh": "gpython.zsh", "fish": "gpython.fish", "powershell": "gpython.ps1"}
POWERSHELL_PROGRAMS = ("pwsh", "powershell")


class CannotInstall(Exception):
    """The completions cannot be set up; the message says why and what to do by hand."""


@dataclass(frozen=True)
class Installed:
    """What install() did: the script it wrote, and the start-up file that loads it (None for fish,
    which reads the script by itself). `added` is False when that file loaded the script already."""

    shell: str
    script: Path
    startup: Path | None
    added: bool


def detected_shell() -> str | None:
    """The shell of this user: PowerShell on Windows, else the name at the end of $SHELL."""
    if os.name == "nt":
        return "powershell"
    return shell_name(Path(os.environ.get("SHELL", "")).name)


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def script_path(shell: str) -> Path:
    """Where the script of `shell` is kept. fish reads every file in its conf.d folder."""
    folder = _config_home() / "fish" / "conf.d" if shell == "fish" else _config_home() / "gpython" / "completions"
    return folder / SCRIPT_NAMES[shell]


def _bash_startup() -> Path:
    """~/.bashrc; on macOS, where a terminal starts bash as a login shell, ~/.bash_profile when there is no ~/.bashrc."""
    home = Path.home()
    if sys.platform == "darwin" and not (home / ".bashrc").exists():
        return home / ".bash_profile"
    return home / ".bashrc"


def _powershell_startup() -> Path:
    """The profile file of PowerShell, asked from PowerShell itself: its place differs by version and system."""
    import shutil
    import subprocess

    for program in POWERSHELL_PROGRAMS:
        if shutil.which(program) is None:
            continue
        try:
            answer = subprocess.run([program, "-NoProfile", "-Command", "$PROFILE"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if answer:
            return Path(answer)
    raise CannotInstall("PowerShell was not found. In PowerShell, run: gpython --completions powershell >> $PROFILE")


def startup_file(shell: str) -> Path | None:
    """The file `shell` reads when it starts, which must load the script. None for fish."""
    if shell == "fish":
        return None
    if shell == "zsh":
        return Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"
    return _bash_startup() if shell == "bash" else _powershell_startup()


def load_line(shell: str, script: Path) -> str:
    """The line in the start-up file that loads `script`, and does nothing when the script is gone."""
    if shell == "powershell":
        return f"if (Test-Path '{script}') {{ . '{script}' }}"
    return f'[ -f "{script}" ] && source "{script}"'


def _add_line(startup: Path, line: str) -> bool:
    """Put `line` at the end of `startup` unless it is there. True when the file changed."""
    text = startup.read_text(encoding="utf-8", errors="replace") if startup.exists() else ""
    if line in text:
        return False
    startup.parent.mkdir(parents=True, exist_ok=True)
    gap = "" if not text or text.endswith("\n") else "\n"
    with open(startup, "a", encoding="utf-8") as out:
        out.write(f"{gap}{line}\n")
    return True


def install(shell: str) -> Installed:
    """Write the script of `shell` and make the shell load it at start-up. Safe to run again:
    the script is written again (a newer gpython has more options), the line is added once."""
    from .config import write_atomically

    startup = startup_file(shell)
    target = script_path(shell)
    try:
        write_atomically(target, script(shell))
        added = startup is not None and _add_line(startup, load_line(shell, target))
    except OSError as error:
        raise CannotInstall(f"cannot write the completions: {error}") from error
    return Installed(shell, target, startup, added)
