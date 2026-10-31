"""Build Whisper's initial_prompt from campaign names so they are spelled correctly."""

from __future__ import annotations

from pathlib import Path

PREFIX = "Curse of Strahd D&D session."


def collect_names(campaign: Path, extra: list[str] = []) -> list[str]:
    """Foundry actor names first, then glossary.md lines, then NPC and place note names.
    Deduplicated case-insensitively, keeping the first occurrence."""
    campaign = Path(campaign)
    names = list(extra)
    glossary = campaign / "glossary.md"
    if glossary.exists():
        for line in glossary.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line)
    for folder in ("NPCs", "Places"):
        d = campaign / folder
        if d.is_dir():
            names.extend(sorted(p.stem for p in d.glob("*.md")))
    seen: set[str] = set()
    out = []
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def build_prompt(names: list[str], max_chars: int = 800) -> str:
    if not names:
        return PREFIX
    head = f"{PREFIX} Names: "
    kept: list[str] = []
    for name in names:
        if len(head) + len(", ".join(kept + [name])) + 1 > max_chars:
            break
        kept.append(name)
    return head + ", ".join(kept) + "." if kept else PREFIX
