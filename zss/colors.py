"""Highlight colour config: defaults, validation, and colors.conf read/write.

`colors.conf` is plain data (`key=style` lines, `#` comments) parsed line by
line on the zsh side — never sourced or eval'd — so a malicious value can at
worst be ignored, never executed as shell code.
"""

import os
import re
from pathlib import Path
from typing import Dict, Optional

from zss import store

COLOR_NAMES = (
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white", "default",
)

DEFAULTS: Dict[str, str] = {
    "command": "fg=green",
    "alias": "fg=cyan",
    "builtin": "fg=green,bold",
    "function": "fg=blue",
    "reserved": "fg=yellow",
    "unknown": "fg=red,bold",
    "option": "fg=magenta",
    "string": "fg=yellow",
    "variable": "fg=cyan",
    "path": "underline",
    "glob": "fg=blue",
    "comment": "fg=8",
    "operator": "fg=magenta,bold",
    "redirect": "fg=magenta",
    "assignment": "fg=blue",
    "suggestion": "fg=8",
}

_COLOR = r"(?:{names}|[0-9]{{1,3}}|#[0-9a-fA-F]{{6}})".format(names="|".join(COLOR_NAMES))
_ITEM_RE = re.compile(rf"^(?:fg={_COLOR}|bg={_COLOR}|bold|underline|standout)$")
_NUM_RE = re.compile(r"^(?:fg|bg)=([0-9]{1,3})$")


def validate(style) -> bool:
    """True if `style` is `none` or a valid comma list of style items."""
    if not isinstance(style, str):
        return False
    style = style.strip()
    if style == "none":
        return True
    if not style:
        return False
    for item in style.split(","):
        item = item.strip()
        if not _ITEM_RE.match(item):
            return False
        m = _NUM_RE.match(item)
        if m and int(m.group(1)) > 255:
            return False
    return True


def _normalize(style: str) -> str:
    """Canonical form of an already-validated style: no leading/trailing
    whitespace anywhere, so what gets saved to colors.conf is exactly what
    the zsh-side validator (lib/highlight.zsh's `_zss_hl_style_valid`, which
    rejects spaces) will accept. Without this, `validate()` here strips
    whitespace per item before checking it but `save()` used to persist the
    original unstripped string, so a value like "fg=red, bold" passed the
    Python side but was silently rejected by the shell, leaving the two
    sides disagreeing about the shell's effective color."""
    style = style.strip()
    if style == "none":
        return style
    return ",".join(item.strip() for item in style.split(","))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".zss-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_overrides(p) -> Dict[str, str]:
    try:
        text = p.colors_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    overrides = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in DEFAULTS and validate(value):
            overrides[key] = value
    return overrides


def _write_overrides(p, overrides: Dict[str, str]) -> None:
    lines = [f"{k}={overrides[k]}" for k in DEFAULTS if k in overrides]
    text = "\n".join(lines) + ("\n" if lines else "")
    _atomic_write_text(p.colors_file, text)


def load(p=None) -> Dict[str, str]:
    """Current effective style for every known key: defaults overridden by
    whatever colors.conf validly sets."""
    p = p or store.paths()
    result = dict(DEFAULTS)
    result.update(_read_overrides(p))
    return result


def save(styles: Dict[str, str], p=None) -> None:
    """Validate and persist `styles` (key -> style). Raises ValueError on
    any unknown key or invalid style, and writes nothing in that case."""
    for key, value in styles.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown color key: {key}")
        if not validate(value):
            raise ValueError(f"invalid style for {key!r}: {value!r}")
    p = p or store.paths()
    overrides = _read_overrides(p)
    overrides.update({key: _normalize(value) for key, value in styles.items()})
    _write_overrides(p, overrides)


def reset(key: Optional[str] = None, p=None) -> None:
    """Revert one key (or all, if `key` is None) back to its default."""
    p = p or store.paths()
    if key is None:
        try:
            p.colors_file.unlink()
        except FileNotFoundError:
            pass
        return
    if key not in DEFAULTS:
        raise ValueError(f"unknown color key: {key}")
    overrides = _read_overrides(p)
    overrides.pop(key, None)
    _write_overrides(p, overrides)
