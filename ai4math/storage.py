"""Read-only storage report: where the bytes of a workspace actually go.

A long autonomous run accumulates ledgers, raw streams and build output that
dwarf the mathematical sources.  This report makes that split explicit so a
retention decision can be made on numbers instead of impressions.
"""

from pathlib import Path

from .store import ResearchError

GROUPS = {".json": "bookkeeping", ".jsonl": "bookkeeping", ".raw": "raw-streams",
          ".log": "logs", ".olean": "build", ".lean": "source", ".py": "source",
          ".md": "prose", ".txt": "prose"}


def survey(root):
    base = Path(root).resolve()
    if not base.is_dir():
        raise ResearchError("workspace is not a directory: " + str(base))
    by_area, by_group = {}, {}
    files = 0
    for path in base.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files += 1
        area = path.relative_to(base).parts[0] if path.relative_to(base).parts else "."
        group = GROUPS.get(path.suffix.lower(), "other")
        by_area[area] = by_area.get(area, 0) + size
        by_group[group] = by_group.get(group, 0) + size
    total = sum(by_group.values())
    bookkeeping = by_group.get("bookkeeping", 0) + by_group.get("raw-streams", 0)
    return {"root": str(base), "files": files, "bytes": total,
            "by_area": dict(sorted(by_area.items(), key=lambda item: -item[1])),
            "by_group": dict(sorted(by_group.items(), key=lambda item: -item[1])),
            "bookkeeping_bytes": bookkeeping,
            "bookkeeping_share": round(bookkeeping / total, 3) if total else 0.0}


def _mb(value):
    return "{:.1f} MB".format(value / 1e6)


def panel(state):
    lines = ["workspace: " + state["root"],
             "files: " + str(state["files"]) + " | total: " + _mb(state["bytes"]) +
             " | bookkeeping+raw streams: " + _mb(state["bookkeeping_bytes"]) +
             " (" + "{:.0%}".format(state["bookkeeping_share"]) + ")"]
    lines.append("")
    for name, value in state["by_group"].items():
        lines.append("{:<14} {}".format(name, _mb(value)))
    lines.append("")
    for name, value in state["by_area"].items():
        lines.append("{:<20} {}".format(name, _mb(value)))
    return "\n".join(lines) + "\n"
