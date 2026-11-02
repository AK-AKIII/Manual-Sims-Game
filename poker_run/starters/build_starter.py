"""Rebuild starter.py from the package, so the starter's simulator is always
the judge's. Run after changing rules.py, instance.py or sim.py:

    python3 poker_run/starters/build_starter.py
"""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).with_name("starter_template.py")
OUT = Path(__file__).with_name("starter.py")


def body(src):
    """A module's code without its docstring and package-relative imports."""
    src = re.sub(r'^""".*?"""\n', "", src, count=1, flags=re.S)
    src = re.sub(r"^from \.\w+ import \((?:.|\n)*?\)\n", "", src, flags=re.M)
    src = re.sub(r"^from \.\w+ import .*\n", "", src, flags=re.M)
    src = re.sub(r"^from dataclasses import .*\n", "", src, flags=re.M)
    return src.strip() + "\n"


def parser_part(src):
    """From instance.py, only what reading an input needs (not the generator)."""
    src = body(src)
    return (src[src.index("@dataclass"):src.index("def targets_for")] + src[src.index("class BadInput"):]).strip()


def main():
    template = TEMPLATE.read_text().split("\n", 1)[1]  # drop the template's own comment line
    code = template.replace("# @RULES@", body((PKG / "rules.py").read_text()).strip())
    code = code.replace("# @INPUT@", parser_part((PKG / "instance.py").read_text()))
    code = code.replace("# @SIM@", body((PKG / "sim.py").read_text()).strip())
    OUT.write_text(code)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
