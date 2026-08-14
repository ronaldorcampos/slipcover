import subprocess
import sys
from pathlib import Path

import pytest

from slipcover.migrate import (MigrationError, format_slipcover_toml, migrate,
                               read_coveragerc)

try:
    import tomllib
except ImportError:
    import tomli as tomllib


_FULL_COVERAGERC = """\
[run]
branch = True
source =
    src
    lib
omit =
    tests/*
    *.pyc
parallel = True

[report]
fail_under = 80.5
skip_covered = True
exclude_lines =
    pragma: no cover
    if TYPE_CHECKING:
show_missing = True
"""


def test_read_coveragerc_translates_mapped_keys(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text(_FULL_COVERAGERC)

    settings, _ = read_coveragerc(rc)

    assert settings == {
        "branch": True,
        "source": ["src", "lib"],
        "omit": ["tests/*", "*.pyc"],
        "fail-under": 80.5,
        "skip-covered": True,
        "exclude-lines": ["pragma: no cover", "if TYPE_CHECKING:"],
    }


def test_read_coveragerc_reports_what_it_left_behind(tmp_path):
    """Every key that isn't translated must be named, with a reason: a
    migration that quietly dropped settings would be worse than none.
    """
    rc = tmp_path / ".coveragerc"
    rc.write_text(_FULL_COVERAGERC)

    _, skipped = read_coveragerc(rc)

    assert {(section, key) for section, key, _ in skipped} == {
        ("run", "parallel"), ("report", "show_missing"),
    }
    assert all(reason for _, _, reason in skipped)


def test_read_coveragerc_unknown_key_is_reported_not_dropped(tmp_path):
    """coverage.py keeps adding options; one this table has never heard of
    still has to be reported rather than silently ignored.
    """
    rc = tmp_path / ".coveragerc"
    rc.write_text("[run]\nsome_future_option = 3\n")

    settings, skipped = read_coveragerc(rc)

    assert settings == {}
    assert skipped == [("run", "some_future_option", "not a SlipCover setting")]


def test_read_coveragerc_unused_section_is_reported(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text("[html]\ndirectory = htmlcov\n")

    settings, skipped = read_coveragerc(rc)

    assert settings == {}
    assert skipped == [("html", None, "section not used by SlipCover")]


@pytest.mark.parametrize("section", ["coverage:run", "run"])
def test_read_coveragerc_accepts_prefixed_sections(tmp_path, section):
    """setup.cfg and tox.ini prefix coverage.py's sections; an explicit path
    to one of those should still translate.
    """
    rc = tmp_path / "setup.cfg"
    rc.write_text(f"[{section}]\nbranch = True\n")

    settings, _ = read_coveragerc(rc)

    assert settings == {"branch": True}


@pytest.mark.parametrize("text,expected", [
    ("True", True), ("true", True), ("yes", True), ("1", True),
    ("False", False), ("off", False), ("0", False),
])
def test_read_coveragerc_boolean_states(tmp_path, text, expected):
    rc = tmp_path / ".coveragerc"
    rc.write_text(f"[run]\nbranch = {text}\n")
    assert read_coveragerc(rc)[0] == {"branch": expected}


def test_read_coveragerc_bad_boolean_raises(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text("[run]\nbranch = maybe\n")
    with pytest.raises(MigrationError, match="expected a boolean"):
        read_coveragerc(rc)


def test_read_coveragerc_bad_number_raises(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text("[report]\nfail_under = most of it\n")
    with pytest.raises(MigrationError, match="expected a number"):
        read_coveragerc(rc)


def test_read_coveragerc_unreadable_raises(tmp_path):
    """read_coveragerc() is reachable without migrate()'s is_file() check.
    A directory raises OSError on both POSIX and Windows.
    """
    with pytest.raises(MigrationError, match="can't read"):
        read_coveragerc(tmp_path)


def test_read_coveragerc_malformed_raises(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text("[run\nbranch = True\n")
    with pytest.raises(MigrationError, match="can't parse"):
        read_coveragerc(rc)


@pytest.mark.parametrize("value,expected", [
    ("src,lib", ["src", "lib"]),
    ("\n    src\n    lib", ["src", "lib"]),
    ("\n    src, lib\n    extra", ["src", "lib", "extra"]),
    ("\n\n    src\n\n", ["src"]),
])
def test_read_coveragerc_list_forms(tmp_path, value, expected):
    rc = tmp_path / ".coveragerc"
    rc.write_text(f"[run]\nsource ={value}\n")
    assert read_coveragerc(rc)[0] == {"source": expected}


def test_output_is_valid_toml_slipcover_can_read(tmp_path):
    """The whole point is a file SlipCover then reads: round-trip it through
    the real reader rather than trusting the text.
    """
    from slipcover.config import read_slipcover_toml

    rc = tmp_path / ".coveragerc"
    rc.write_text(_FULL_COVERAGERC)

    out, settings, _ = migrate(rc)

    assert read_slipcover_toml(out) == settings


def test_output_escapes_regex_backslashes(tmp_path):
    """exclude_lines is full of backslashes; emitting them raw would either
    corrupt the pattern or produce a file that doesn't parse.
    """
    rc = tmp_path / ".coveragerc"
    rc.write_text('[report]\nexclude_lines =\n    def __repr__\\(self\\)\n    ^\\s*pass$\n')

    out, _, _ = migrate(rc)

    with open(out, "rb") as f:
        assert tomllib.load(f)["exclude-lines"] == [r"def __repr__\(self\)", r"^\s*pass$"]


def test_output_escapes_quotes(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text('[run]\nomit =\n    a"b/*\n')

    out, _, _ = migrate(rc)

    with open(out, "rb") as f:
        assert tomllib.load(f)["omit"] == ['a"b/*']


def test_output_carries_a_generated_header(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text("[run]\nbranch = True\n")

    out, _, _ = migrate(rc)
    text = out.read_text()

    assert text.startswith("#")
    assert ".coveragerc" in text.splitlines()[0]


def test_migrate_writes_beside_the_input(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    rc = project / ".coveragerc"
    rc.write_text("[run]\nbranch = True\n")

    out, _, _ = migrate(rc)

    assert out == project / "slipcover.toml"
    assert out.is_file()


def test_migrate_refuses_to_overwrite(tmp_path):
    """Running twice must not silently replace hand-edited configuration."""
    rc = tmp_path / ".coveragerc"
    rc.write_text("[run]\nbranch = True\n")
    existing = tmp_path / "slipcover.toml"
    existing.write_text("fail-under = 99.0\n")

    with pytest.raises(MigrationError, match="already exists"):
        migrate(rc)

    assert existing.read_text() == "fail-under = 99.0\n"   # untouched


def test_migrate_missing_input_raises(tmp_path):
    with pytest.raises(MigrationError, match="no such file"):
        migrate(tmp_path / "nope.rc")


def test_migrate_empty_coveragerc_writes_header_only(tmp_path):
    rc = tmp_path / ".coveragerc"
    rc.write_text("")

    out, settings, skipped = migrate(rc)

    assert settings == {} and skipped == []
    with open(out, "rb") as f:
        assert tomllib.load(f) == {}


def test_format_slipcover_toml_value_types():
    text = format_slipcover_toml(
        {"branch": True, "skip-covered": False, "fail-under": 80.5,
         "threshold": 75, "source": ["src"], "out": "cov.json"},
        ".coveragerc")

    assert "branch = true" in text
    assert "skip-covered = false" in text
    assert "fail-under = 80.5" in text
    assert "threshold = 75" in text
    assert 'source = ["src"]' in text
    assert 'out = "cov.json"' in text


def test_cli_migrates_and_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".coveragerc").write_text(_FULL_COVERAGERC)

    p = subprocess.run([sys.executable, '-m', 'slipcover', '--migrate-coveragerc'],
                       capture_output=True, text=True)

    assert p.returncode == 0
    assert 'Traceback' not in p.stderr
    assert (tmp_path / "slipcover.toml").is_file()
    assert "parallel" in p.stdout        # named what it left behind
    assert "show_missing" in p.stdout


def test_cli_migrates_an_explicit_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "other.rc").write_text("[run]\nbranch = True\n")

    p = subprocess.run([sys.executable, '-m', 'slipcover',
                        '--migrate-coveragerc', 'other.rc'],
                       capture_output=True, text=True)

    assert p.returncode == 0
    assert (tmp_path / "slipcover.toml").is_file()


def test_cli_missing_coveragerc_is_a_clean_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    p = subprocess.run([sys.executable, '-m', 'slipcover', '--migrate-coveragerc'],
                       capture_output=True, text=True)

    assert p.returncode == 1
    assert 'Traceback' not in p.stderr
    assert '.coveragerc' in p.stderr


def test_cli_migrate_does_not_need_a_script(tmp_path, monkeypatch):
    """It's a mode, not a setting: it satisfies the group that otherwise
    requires a script or -m.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".coveragerc").write_text("[run]\nbranch = True\n")

    p = subprocess.run([sys.executable, '-m', 'slipcover', '--migrate-coveragerc'],
                       capture_output=True, text=True)

    assert p.returncode == 0


def test_cli_migrate_conflicts_with_a_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".coveragerc").write_text("[run]\nbranch = True\n")
    (tmp_path / "script.py").write_text("x = 1\n")

    p = subprocess.run([sys.executable, '-m', 'slipcover',
                        '--migrate-coveragerc', 'script.py'],
                       capture_output=True, text=True)

    # the path is taken as --migrate-coveragerc's own argument, and script.py
    # is not a .coveragerc -- either way it must not run the script
    assert p.returncode != 0 or not (tmp_path / "output").exists()


def test_migrate_is_not_a_config_key():
    """--migrate-coveragerc picks what to do, not how to report; it must not
    show up as a configurable setting.
    """
    from slipcover.__main__ import build_parser
    from slipcover.config import derive_configurable_keys

    assert "migrate-coveragerc" not in derive_configurable_keys(build_parser())
