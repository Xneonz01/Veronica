"""A tool server as the user sets it up: what may be launched, with what, and how it is
started.

An MCP server is a program the user chose, with the rights of their account (ADR
0003), so the checks here are about not being tricked into running something else:

* **A program, named in full.** An absolute path to an existing `.exe`, or to a `.cmd`
  or `.bat`, read once (`command`'s validator), so the checks and the launch see the same
  path. No shell: nothing is looked up on PATH, and nothing is expanded. Not a network
  path to another computer: looking one up sends the user's Windows sign-in to it.
* **Batch files take plain arguments only.** Windows runs a `.cmd` through cmd.exe,
  which reads `&`, `|`, `%` and quotes in an argument as its own (BatBadBut,
  CVE-2024-24576), so a `.cmd`'s arguments are limited to letters, digits and
  `@._/:=,+\\-`.
* **Little of her own environment.** The server gets a short list of what Windows
  programs need (`INHERITED`), never what would change how a Python or Node program
  starts or anything of hers (`REFUSED`), plus the variables the user set for it.

Arguments and variables can hold keys, so they are sealed at rest (`store.py`) and never
logged; what a screen gets back is the setup with variables' values left out (`view`).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

ID = re.compile(r"[a-z0-9-]{1,32}")
_PROGRAMS = frozenset({".exe", ".cmd", ".bat"})
_BATCH = frozenset({".cmd", ".bat"})
_BATCH_ARGUMENT = re.compile(r"[A-Za-z0-9@._/:=,+\\-]*")
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
MAX_ARGUMENTS = 64
MAX_ARGUMENT = 1_000
MAX_VARIABLES = 32
MAX_VALUE = 4_000

# What a Windows program needs to start and find its files, inherited from hers.
INHERITED = (
    "SystemRoot", "SystemDrive", "windir", "PATH", "PATHEXT", "TEMP", "TMP",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH", "COMSPEC",
    "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
    "CommonProgramFiles(x86)", "ProgramData", "ALLUSERSPROFILE", "PUBLIC",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "USERNAME", "USERDOMAIN", "OS",
)  # fmt: skip
# Never set, by her or by the user: each changes what a Python, Node, Java, Perl or Ruby
# program runs before its own code (PYTHONSTARTUP, NODE_OPTIONS=--require), or what npx
# or uvx fetch and from where, or is hers.
REFUSED = (
    "PYTHON", "NODE_OPTIONS", "NODE_PATH", "VERONICA_", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
    "JDK_JAVA_OPTIONS", "PERL5OPT", "PERL5LIB", "RUBYOPT", "NPM_CONFIG_", "UV_",
)  # fmt: skip


class SetupRefused(ValueError):
    """A setup that can't be used; the message is for the user."""


class ServerSetup(BaseModel):
    """One tool server, as saved and as the Settings screen sends it."""

    model_config = {"extra": "forbid"}

    id: str = Field(description="Lowercase letters, digits and dashes; its tools' prefix.")
    name: str = Field(min_length=1, max_length=60)
    command: str = Field(min_length=1, max_length=1_000)
    args: list[str] = Field(default_factory=list, max_length=MAX_ARGUMENTS)
    env: dict[str, str] = Field(default_factory=dict, max_length=MAX_VARIABLES)
    enabled: bool = True
    # The user's word that the server reaches the internet: shown on its tools' prompts.
    internet: bool = False

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        path = value.strip()
        if len(path) > 1 and path[0] == path[-1] == '"':
            path = path[1:-1].strip()
        if '"' in path or any(ord(char) < 32 for char in path):
            raise ValueError("the program's path has a quote or a control character in it")
        if path.startswith(("\\\\", "//")):
            raise ValueError("the program has to be on this computer, not a network path")
        return path

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not ID.fullmatch(value):
            raise ValueError("use 1 to 32 lowercase letters, digits or dashes")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("give it a name")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > MAX_ARGUMENT or "\x00" in value or "\n" in value:
                raise ValueError(
                    f"an argument is longer than {MAX_ARGUMENT} characters or holds a line break"
                )
        return values

    @field_validator("env")
    @classmethod
    def _env(cls, values: dict[str, str]) -> dict[str, str]:
        if len({name.upper() for name in values}) < len(values):
            raise ValueError("two variables have the same name but for case (Path and PATH)")
        for name, value in values.items():
            if not _VARIABLE.fullmatch(name):
                raise ValueError(f"{name[:64]!r} isn't a variable name")
            if refused(name):
                raise ValueError(f"{name} can't be set for a tool server")
            if len(value) > MAX_VALUE or "\x00" in value:
                raise ValueError(f"{name}'s value is too long")
        return values

    @model_validator(mode="after")
    def _batch_arguments(self) -> ServerSetup:
        if Path(self.command).suffix.lower() in _BATCH:
            for value in self.args:
                if not _BATCH_ARGUMENT.fullmatch(value):
                    raise ValueError(
                        "a .cmd or .bat file only takes arguments made of letters, digits "
                        "and @._/:=,+\\- (no spaces or quotes)"
                    )
        return self

    def view(self) -> dict[str, Any]:
        """What a screen is shown: everything but the variables' values."""
        shown = self.model_dump(exclude={"env"})
        return {**shown, "env": sorted(self.env)}


def refused(name: str) -> bool:
    upper = name.upper()
    return any(upper.startswith(prefix.upper()) for prefix in REFUSED)


@dataclass(frozen=True, slots=True)
class Launch:
    """How to start a server: checked, with its environment built."""

    program: Path
    args: tuple[str, ...]
    env: dict[str, str]
    folder: Path


def program(command: str) -> Path:
    """The program `command` names, or `SetupRefused` saying why it can't be run."""
    path = Path(command)
    if command.startswith(("\\\\", "//")) or not path.is_absolute():
        raise SetupRefused("Give the program's full path, starting with the drive.")
    if path.suffix.lower() not in _PROGRAMS:
        raise SetupRefused("The program has to be an .exe, .cmd or .bat file.")
    if not path.is_file():
        raise SetupRefused("There's no program at that path.")
    return path


def launch(setup: ServerSetup, data_dir: Path) -> Launch:
    """How to start `setup`'s server; its working folder is its own, in her data folder."""
    folder = data_dir / "mcp" / setup.id
    return Launch(program(setup.command), tuple(setup.args), environment(setup.env), folder)


def environment(own: dict[str, str], source: dict[str, str] | None = None) -> dict[str, str]:
    """The server's environment: the inherited names that are set, then its own."""
    found = dict(os.environ if source is None else source)
    if sys.platform == "win32":  # names are case-insensitive there
        found = {name.upper(): value for name, value in found.items()}
        mine = {name.upper() for name in own}  # its own `Path` replaces her `PATH`
        picked = {
            name: found[name.upper()]
            for name in INHERITED
            if name.upper() in found and name.upper() not in mine
        }
    else:
        picked = {name: found[name] for name in INHERITED if name in found}
    return {**picked, **{name: value for name, value in own.items() if not refused(name)}}
