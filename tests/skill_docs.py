"""Shared readers for the layered skill documents (v0.6.11).

Skills are split into a small always-loaded layer (``<mode>/SKILL.md`` + ``route-a-workflow.md``)
and on-demand ``skills/reference/*.md`` files.  Contract tests check *semantics* against the text a
coordinator can actually reach for a mode, and check structure only where it matters (ordering
inside a specific reference file), instead of pinning sentences to one physical file.
"""
from __future__ import annotations

from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
SKILLS = PLUGIN / "skills"
MODES = ("boost", "teamwork", "swarm")
SHARED_PATH = SKILLS / "route-a-workflow.md"
REFERENCE_DIR = SKILLS / "reference"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def skill_text(mode: str) -> str:
    """The always-loaded mode file only."""
    return _read(SKILLS / mode / "SKILL.md")


def shared_text() -> str:
    """The always-loaded shared Route A contract only."""
    return _read(SHARED_PATH)


def reference_paths() -> list[Path]:
    return sorted(REFERENCE_DIR.glob("*.md"))


def reference_text(name: str) -> str:
    return _read(REFERENCE_DIR / f"{name}.md")


def shared_corpus() -> str:
    """Shared contract plus every on-demand reference it routes to."""
    return "\n\n".join([shared_text(), *(_read(path) for path in reference_paths())])


def mode_corpus(mode: str) -> str:
    """Everything a coordinator running ``mode`` can reach: SKILL + shared + references."""
    return skill_text(mode) + "\n\n" + shared_corpus()


def section(text: str, heading: str, end: str = "\n## ") -> str:
    """Return the text after ``heading`` up to the next ``end`` marker."""
    assert heading in text, f"missing heading {heading!r}"
    return text.split(heading, 1)[1].split(end, 1)[0]


def body_without_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        return text.split("\n---\n", 1)[1]
    return text
