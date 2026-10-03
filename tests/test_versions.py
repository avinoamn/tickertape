"""Versioning conventions: every component has a SemVer version, and the chart pins an explicit tag per service."""
import re

import pytest
import yaml
from conftest import ROOT

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")
SERVICES = ["poller", "ner", "laya"]


@pytest.mark.parametrize("service", SERVICES)
def test_service_version_file_is_semver(service):
    version = (ROOT / "services" / service / "VERSION").read_text()
    assert version.endswith("\n") and SEMVER.match(version.strip()), f"services/{service}/VERSION: {version!r}"


def test_chart_version_is_semver():
    chart = yaml.safe_load((ROOT / "charts" / "tickertape" / "Chart.yaml").read_text())
    assert SEMVER.match(chart["version"])
    assert "appVersion" not in chart, "services are versioned independently: there is no single app version"


@pytest.mark.parametrize("service", SERVICES)
def test_chart_pins_an_explicit_image_tag(service):
    values = yaml.safe_load((ROOT / "charts" / "tickertape" / "values.yaml").read_text())
    image = values[service]["image"]
    assert image["repository"] and SEMVER.match(str(image["tag"])), f"{service}: {image}"


def test_changelog_headings_follow_the_release_format():
    """`## [Unreleased]` or `## <component> <version> - <date>`, which the release workflow looks up."""
    heading = re.compile(r"^## (\[Unreleased\]|(poller|ner|laya|chart) (\S+) - \d{4}-\d{2}-\d{2})$")
    for line in (ROOT / "CHANGELOG.md").read_text().splitlines():
        if line.startswith("## "):
            m = heading.match(line)
            assert m, f"unexpected changelog heading: {line!r}"
            if m.group(3):
                assert SEMVER.match(m.group(3)), line
