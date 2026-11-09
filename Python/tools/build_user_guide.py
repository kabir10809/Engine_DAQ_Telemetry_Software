"""Build docs/user_guide.pdf from docs/user_guide/user_guide.tex.

Tables of fault scenarios and interfaces are generated from the code and the
compatibility matrix, so the manual cannot disagree with them. Needs pdflatex
on the system path; all output and caches stay in this folder.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from edaq import APP_VERSION, alarms, sim
from edaq.hw.matrix import CompatibilityMatrix
from edaq.hw.registry import DriverRegistry

GUIDE = ROOT / "docs" / "user_guide"


def esc(text) -> str:
    text = str(text).replace("\\", "\\textbackslash{}")
    for character in "_%&#$":
        text = text.replace(character, "\\" + character)
    return text


def scenario_rows() -> list[str]:
    rows = []
    for s in sim.SCENARIOS:
        classes = ", ".join(alarms.LABELS[c] for c in s["classes"]) or "None"
        rows.append(f'{esc(s["title"])} & \\texttt{{{esc(s["id"])}}} & {s["color"].upper()} & {esc(classes)} \\\\')
    return rows


def interface_rows() -> list[str]:
    registry = DriverRegistry()
    rows = []
    for r in CompatibilityMatrix().data["interfaces"]:
        platforms = ", ".join(registry.entry(r["driver"])["manifest"]["platforms"])
        rows.append(f'{esc(r["interface"])} & \\texttt{{{esc(r["driver"])}}} & \\texttt{{{esc(r["status"])}}} & {esc(platforms)} & {esc(r.get("notes", ""))} \\\\')
    return rows


def main() -> None:
    generated = GUIDE / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "version.tex").write_text(f"\\newcommand{{\\appversion}}{{{APP_VERSION}}}\n", encoding="utf-8")
    (generated / "scenarios.tex").write_text("\n".join(scenario_rows() + ["\\bottomrule"]) + "\n", encoding="utf-8")
    (generated / "interfaces.tex").write_text("\n".join(interface_rows() + ["\\bottomrule"]) + "\n", encoding="utf-8")
    cache = GUIDE / ".texcache"
    environment = dict(os.environ, TEXMFVAR=str(cache), TEXMFCONFIG=str(cache))
    for _ in range(3):      # contents page and cross references need repeated passes
        result = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "user_guide.tex"], cwd=GUIDE, env=environment, capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit("pdflatex failed:\n" + "\n".join(result.stdout.splitlines()[-25:]))
    shutil.move(GUIDE / "user_guide.pdf", ROOT / "docs" / "user_guide.pdf")
    for extension in ("aux", "log", "out", "toc"):
        (GUIDE / f"user_guide.{extension}").unlink(missing_ok=True)
    shutil.rmtree(cache, ignore_errors=True)
    print("Built docs/user_guide.pdf")


if __name__ == "__main__":
    main()
