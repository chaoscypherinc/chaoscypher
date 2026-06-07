# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301  -- Click \x08 paragraph escape, intentional non-raw docstring.

"""Shell Completion command - Generate shell completion scripts.

Generates completion scripts for bash, zsh, and fish shells.

Example:
    # Auto-install (recommended)
    chaoscypher completions bash --install
    chaoscypher completions zsh --install
    chaoscypher completions fish --install

    # Manual installation
    chaoscypher completions bash >> ~/.bashrc
    chaoscypher completions zsh >> ~/.zshrc
    chaoscypher completions fish > ~/.config/fish/completions/chaoscypher.fish
"""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel


console = Console()
error_console = Console(stderr=True)

# Marker comments to identify our completion block
_COMPLETION_START = "# >>> chaoscypher completions >>>"
_COMPLETION_END = "# <<< chaoscypher completions <<<"


@click.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.option("--install", is_flag=True, help="Install completions to shell config")
@click.option("--uninstall", is_flag=True, help="Remove completions from shell config")
@click.option("--show-install", "-i", is_flag=True, help="Show installation instructions")
def completions(shell: str, install: bool, uninstall: bool, show_install: bool) -> None:
    """Generate shell completion script.

    SHELL is the shell to generate completions for: bash, zsh, or fish.

    \x08
    Examples:
        chaoscypher completions bash --install    # Auto-install to ~/.bashrc
        chaoscypher completions zsh --install     # Auto-install to ~/.zshrc
        chaoscypher completions fish --install    # Auto-install to fish completions
        chaoscypher completions bash              # Print script to stdout
        chaoscypher completions bash --uninstall  # Remove from shell config
    """
    if show_install:
        _show_install_instructions(shell)
        return

    if uninstall:
        _uninstall_completions(shell)
        return

    # Generate the completion script
    script = _generate_completion_script(shell)
    if script is None:
        sys.exit(1)

    if install:
        _install_completions(shell, script)
    else:
        # Output to stdout (so it can be piped/redirected)
        click.echo(script)


def _generate_completion_script(shell: str) -> str | None:
    """Generate completion script for the given shell."""
    from chaoscypher_cli.__main__ import main

    try:
        from click.shell_completion import get_completion_class

        completion_class = get_completion_class(shell)
        if completion_class is None:
            error_console.print(f"[red]Error:[/red] Unknown shell: {shell}")
            return None

        completion = completion_class(main, {}, "chaoscypher", "_CHAOSCYPHER_COMPLETE")
        return completion.source()

    except Exception as e:
        error_console.print(f"[red]Error generating completions:[/red] {e}")
        return None


def _install_completions(shell: str, script: str) -> None:
    """Install completions to the appropriate shell config file."""
    if shell == "fish":
        _install_fish_completions(script)
    else:
        _install_rc_completions(shell, script)


def _install_rc_completions(shell: str, script: str) -> None:
    """Install completions to .bashrc or .zshrc."""
    rc_file = Path.home() / (".bashrc" if shell == "bash" else ".zshrc")

    # Create the completion block with markers
    completion_block = f"\n{_COMPLETION_START}\n{script}\n{_COMPLETION_END}\n"

    # Check if already installed
    if rc_file.exists():
        content = rc_file.read_text()
        if _COMPLETION_START in content:
            # Already installed - update it
            start_idx = content.index(_COMPLETION_START)
            end_idx = content.index(_COMPLETION_END) + len(_COMPLETION_END)
            new_content = content[:start_idx] + completion_block.strip() + content[end_idx:]
            rc_file.write_text(new_content)
            console.print(f"[green]Updated[/green] completions in {rc_file}")
            console.print(f"[dim]Run: source {rc_file}[/dim]")
            return

    # Append to file
    with rc_file.open("a") as f:
        f.write(completion_block)

    console.print(f"[green]Installed[/green] completions to {rc_file}")
    console.print(f"[dim]Run: source {rc_file}[/dim]")


def _install_fish_completions(script: str) -> None:
    """Install completions to fish completions directory."""
    completions_dir = Path.home() / ".config" / "fish" / "completions"
    completions_file = completions_dir / "chaoscypher.fish"

    # Create directory if needed
    completions_dir.mkdir(parents=True, exist_ok=True)

    # Write the completion script (fish doesn't need markers - it's a standalone file)
    completions_file.write_text(script)

    console.print(f"[green]Installed[/green] completions to {completions_file}")
    console.print("[dim]Start a new fish shell to use completions[/dim]")


def _uninstall_completions(shell: str) -> None:
    """Remove completions from shell config."""
    if shell == "fish":
        completions_file = Path.home() / ".config" / "fish" / "completions" / "chaoscypher.fish"
        if completions_file.exists():
            completions_file.unlink()
            console.print(f"[green]Removed[/green] {completions_file}")
        else:
            console.print("[yellow]No fish completions found[/yellow]")
        return

    # Bash or Zsh
    rc_file = Path.home() / (".bashrc" if shell == "bash" else ".zshrc")

    if not rc_file.exists():
        console.print(f"[yellow]No {rc_file} found[/yellow]")
        return

    content = rc_file.read_text()
    if _COMPLETION_START not in content:
        console.print(f"[yellow]No chaoscypher completions found in {rc_file}[/yellow]")
        return

    # Remove the completion block
    start_idx = content.index(_COMPLETION_START)
    end_idx = content.index(_COMPLETION_END) + len(_COMPLETION_END)

    # Also remove surrounding newlines
    while start_idx > 0 and content[start_idx - 1] == "\n":
        start_idx -= 1
    while end_idx < len(content) and content[end_idx] == "\n":
        end_idx += 1

    new_content = content[:start_idx] + content[end_idx:]
    rc_file.write_text(new_content)

    console.print(f"[green]Removed[/green] completions from {rc_file}")
    console.print(f"[dim]Run: source {rc_file}[/dim]")


def _show_install_instructions(shell: str) -> None:
    """Show installation instructions for shell completions."""
    if shell == "bash":
        instructions = """
[bold]Bash Completion Installation[/bold]

[cyan]Option 1: Direct eval (add to ~/.bashrc)[/cyan]
  eval "$(chaoscypher completions bash)"

[cyan]Option 2: Save to file[/cyan]
  # Create completions directory if needed
  mkdir -p ~/.bash_completion.d

  # Generate completion script
  chaoscypher completions bash > ~/.bash_completion.d/chaoscypher

  # Add to ~/.bashrc:
  source ~/.bash_completion.d/chaoscypher

[dim]After adding, restart your shell or run: source ~/.bashrc[/dim]
"""

    elif shell == "zsh":
        instructions = """
[bold]Zsh Completion Installation[/bold]

[cyan]Option 1: Direct eval (add to ~/.zshrc)[/cyan]
  eval "$(chaoscypher completions zsh)"

[cyan]Option 2: Save to fpath[/cyan]
  # Create function directory
  mkdir -p ~/.zfunc

  # Generate completion script
  chaoscypher completions zsh > ~/.zfunc/_chaoscypher

  # Add to ~/.zshrc (before compinit):
  fpath+=~/.zfunc

  # Make sure compinit is called:
  autoload -Uz compinit && compinit

[dim]After adding, restart your shell or run: source ~/.zshrc[/dim]
"""

    elif shell == "fish":
        instructions = """
[bold]Fish Completion Installation[/bold]

[cyan]Save to completions directory:[/cyan]
  # Create completions directory if needed
  mkdir -p ~/.config/fish/completions

  # Generate completion script
  chaoscypher completions fish > ~/.config/fish/completions/chaoscypher.fish

[dim]Fish automatically loads completions from this directory.[/dim]
[dim]Start a new shell to use completions.[/dim]
"""

    else:
        instructions = f"[red]Unknown shell: {shell}[/red]"

    console.print(
        Panel(instructions.strip(), title=f"{shell.title()} Completions", border_style="cyan")
    )
