"""Parse and serialize zsh history files.

Handles both formats zsh writes (plain `<cmd>` lines and extended
`: <start>:<elapsed>;<cmd>` lines, detected per entry), multi-line entries
(a trailing backslash before the newline continues the entry onto the next
physical line) and metafication (zsh escapes NUL and bytes 0x83-0xA2 as
0x83 followed by the byte XOR 0x20 so they cannot collide with its internal
token values).
"""

from dataclasses import dataclass
from typing import List, Optional

META = 0x83
META_LO = 0x83
META_HI = 0xA2


def unmetafy(data: bytes) -> bytes:
    """Reverse zsh's history-file byte escaping."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == META and i + 1 < n:
            out.append(data[i + 1] ^ 0x20)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def metafy(data: bytes) -> bytes:
    """Apply zsh's history-file byte escaping."""
    out = bytearray()
    for b in data:
        if b == 0x00 or (META_LO <= b <= META_HI):
            out.append(META)
            out.append(b ^ 0x20)
        else:
            out.append(b)
    return bytes(out)


@dataclass(frozen=True)
class Entry:
    raw: bytes            # exact original bytes for this entry (no trailing newline)
    text: str              # decoded command: unmetafied, continuation backslashes resolved to real newlines
    ts: Optional[int]      # start timestamp, extended format only
    elapsed: Optional[int]  # elapsed seconds, extended format only
    extended: bool


def _split_physical_lines(data: bytes) -> List[bytes]:
    if not data:
        return []
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def _group_logical_lines(physical: List[bytes]) -> List[List[bytes]]:
    """Group physical lines into logical entries using backslash-newline
    continuation: zsh continues on ANY trailing backslash, not just an odd
    count of them. zsh appends exactly one continuation backslash when it
    writes a multi-line entry, regardless of what already precedes it, so a
    command whose first line already ends in its own literal backslash (e.g.
    `echo a \` continued onto `b`) is written with two trailing backslash
    bytes, not one, and must still be read back as a single entry."""
    groups: List[List[bytes]] = []
    current: List[bytes] = []
    for line in physical:
        current.append(line)
        if line.endswith(b"\\"):
            continue  # continues on the next physical line
        groups.append(current)
        current = []
    if current:
        groups.append(current)
    return groups


_EXT_PREFIX = b": "


def _parse_extended_header(first_line: bytes):
    """Return (ts, elapsed, rest_of_first_line) or None if not an extended-format entry."""
    if not first_line.startswith(_EXT_PREFIX):
        return None
    body = first_line[len(_EXT_PREFIX):]
    colon = body.find(b":")
    if colon <= 0:
        return None
    ts_bytes = body[:colon]
    if not ts_bytes.isdigit():
        return None
    rest = body[colon + 1:]
    semi = rest.find(b";")
    if semi < 0:
        return None
    elapsed_bytes = rest[:semi]
    if not elapsed_bytes.isdigit():
        return None
    return int(ts_bytes), int(elapsed_bytes), rest[semi + 1:]


def _decode_command(lines: List[bytes]) -> str:
    """Join physical lines of a command into decoded text with real newlines."""
    parts = []
    for idx, line in enumerate(lines):
        if idx < len(lines) - 1:
            # trailing odd backslashes marked continuation; strip exactly one
            # (the escaping backslash), keep the rest, add a real newline.
            parts.append(line[:-1])
        else:
            parts.append(line)
    joined = b"\n".join(parts)
    return unmetafy(joined).decode("utf-8", errors="surrogateescape")


def parse(data: bytes) -> List[Entry]:
    physical = _split_physical_lines(data)
    groups = _group_logical_lines(physical)
    entries = []
    for group in groups:
        raw = b"\n".join(group)
        header = _parse_extended_header(group[0])
        if header is not None:
            ts, elapsed, rest = header
            cmd_lines = [rest] + group[1:]
            text = _decode_command(cmd_lines)
            entries.append(Entry(raw=raw, text=text, ts=ts, elapsed=elapsed, extended=True))
        else:
            text = _decode_command(group)
            entries.append(Entry(raw=raw, text=text, ts=None, elapsed=None, extended=False))
    return entries


def split_logical_records(data: bytes) -> List[bytes]:
    """Group raw bytes into logical records using the same backslash-newline
    continuation rule as history entries, without extended-format parsing.
    Used for the block-list file, which is a plain list of escaped prefixes."""
    physical = _split_physical_lines(data)
    groups = _group_logical_lines(physical)
    return [b"\n".join(g) for g in groups]


def serialize(entries: List[Entry]) -> bytes:
    if not entries:
        return b""
    return b"\n".join(e.raw for e in entries) + b"\n"


def encode_block_rule(text: str) -> bytes:
    """Encode a block-rule prefix as plain UTF-8 with embedded real newlines
    re-escaped as backslash-newline (same continuation rule as the history
    file). Deliberately NOT metafied: metafication is zsh's own internal
    string representation, applied transparently whenever zsh reads or
    writes any file, so a plain UTF-8 block-list line compares equal to a
    `$history` value with no decode step needed on the zsh side."""
    raw_text = text.encode("utf-8", errors="surrogateescape")
    return raw_text.replace(b"\n", b"\\\n")


def decode_block_rule(line: bytes) -> str:
    unescaped = line.replace(b"\\\n", b"\n")
    return unescaped.decode("utf-8", errors="surrogateescape")
