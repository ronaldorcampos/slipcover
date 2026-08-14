"""Translates a coverage.py .coveragerc into a slipcover.toml.

INI parsing lives here rather than in config.py on purpose: this is
coverage.py's format, not SlipCover's, and it is read exactly once -- when
migrating -- rather than on every run.
"""

import configparser
from pathlib import Path


# Sections of a .coveragerc that hold settings we can translate. coverage.py
# splits its options across these; the rest ([html], [paths], ...) configure
# outputs SlipCover doesn't produce.
_SECTIONS = ("run", "report")

# coverage.py key -> (slipcover key, kind). Names are matched with
# underscores, as coverage.py writes them.
_MAPPING = {
    "branch": ("branch", "bool"),
    "source": ("source", "list"),
    "omit": ("omit", "list"),
    "fail_under": ("fail-under", "float"),
    "skip_covered": ("skip-covered", "bool"),
    "exclude_lines": ("exclude-lines", "list"),
    "exclude_also": ("exclude-also", "list"),
}

# Keys we recognise but deliberately drop, and why. Anything not named here
# and not in _MAPPING is reported as simply unrecognised, which covers
# coverage.py options added after this table was written.
_UNSUPPORTED = {
    "include": "SlipCover selects files with source/omit only",
    "parallel": "SlipCover merges subprocess coverage automatically",
    "concurrency": "not applicable: SlipCover doesn't hook the tracer",
    "data_file": "SlipCover keeps no intermediate data file",
    "dynamic_context": "contexts aren't supported",
    "relative_files": "paths are already reported relative to the run",
    "show_missing": "missing lines are always reported",
    "precision": "no equivalent; see missing-width for column width",
    "ignore_errors": "no equivalent",
    "sort": "no equivalent",
}


class MigrationError(Exception):
    """A migration that can't proceed: unreadable input, or output in place."""


def _parse_bool(section, key, value):
    try:
        return configparser.ConfigParser.BOOLEAN_STATES[value.strip().lower()]
    except KeyError:
        raise MigrationError(
            f"[{section}] {key}: expected a boolean, got '{value.strip()}'"
        ) from None


def _parse_float(section, key, value):
    try:
        return float(value.strip())
    except ValueError:
        raise MigrationError(
            f"[{section}] {key}: expected a number, got '{value.strip()}'"
        ) from None


def _parse_list(value):
    """coverage.py writes multi-valued settings one per line, and tolerates
    commas; either way the result is a list of non-empty entries.
    """
    return [
        item
        for line in value.splitlines()
        for item in (part.strip() for part in line.split(","))
        if item
    ]


def _toml_str(s):
    # Basic strings need these two escaped; the regexes in exclude_lines are
    # full of backslashes, so getting this wrong would corrupt every one.
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_str(v) for v in value) + "]"
    return _toml_str(value)


def read_coveragerc(path):
    """Returns (settings, skipped) from a coverage.py INI config file.

    'settings' maps SlipCover keys to translated values. 'skipped' lists
    (section, key, reason) for everything left behind, so the caller can
    say what didn't come across rather than dropping it silently.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8-sig") as f:
            parser.read_file(f)
    except OSError as e:
        raise MigrationError(f"can't read {path}: {e}") from None
    except configparser.Error as e:
        raise MigrationError(f"can't parse {path}: {e}") from None

    settings = {}
    skipped = []

    for section in parser.sections():
        # setup.cfg and tox.ini prefix coverage.py's sections; accept both
        # spellings so an explicit path to one of those files still works.
        name = section[len("coverage:"):] if section.startswith("coverage:") else section

        if name not in _SECTIONS:
            skipped.append((section, None, "section not used by SlipCover"))
            continue

        for key, value in parser.items(section):
            if key in _MAPPING:
                sc_key, kind = _MAPPING[key]
                if kind == "bool":
                    settings[sc_key] = _parse_bool(name, key, value)
                elif kind == "float":
                    settings[sc_key] = _parse_float(name, key, value)
                else:
                    settings[sc_key] = _parse_list(value)
            elif key in _UNSUPPORTED:
                skipped.append((name, key, _UNSUPPORTED[key]))
            else:
                skipped.append((name, key, "not a SlipCover setting"))

    return settings, skipped


def format_slipcover_toml(settings, source):
    """Renders 'settings' as the text of a slipcover.toml."""
    lines = [
        f"# Generated by slipcover --migrate-coveragerc from {source}.",
        "# Review before committing: not every coverage.py setting has an",
        "# equivalent, and those that don't were left behind.",
        "",
    ]
    lines += [f"{key} = {_toml_value(value)}" for key, value in settings.items()]
    return "\n".join(lines) + "\n"


def migrate(path, out=None):
    """Writes a slipcover.toml translated from the .coveragerc at 'path'.

    'out' defaults to a slipcover.toml beside the input. An existing file
    is never overwritten -- a migration is a one-off, and silently
    replacing hand-written configuration would be the worst way to learn
    that this ran twice.

    Returns (out_path, settings, skipped).
    """
    path = Path(path)
    if not path.is_file():
        raise MigrationError(f"no such file: {path}")

    out = Path(out) if out is not None else path.parent / "slipcover.toml"
    if out.exists():
        raise MigrationError(f"{out} already exists; move it aside first")

    settings, skipped = read_coveragerc(path)
    out.write_text(format_slipcover_toml(settings, path.name), encoding="utf-8")

    return out, settings, skipped
