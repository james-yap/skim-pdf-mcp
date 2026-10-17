from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-skim",
        action="store_true",
        default=False,
        help="run tests marked live_skim against a running Skim (macOS only)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live-skim"):
        return
    skip = pytest.mark.skip(reason="needs a running Skim; pass --live-skim")
    for item in items:
        if "live_skim" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Two-page US Letter PDF; page 1 has two lines of text at known positions."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Hello world of math", fontsize=14)
    page.insert_text((72, 200), "Second line here", fontsize=14)
    doc.new_page(width=612, height=792).insert_text((72, 100), "Page two text", fontsize=14)
    path = tmp_path / "sample.pdf"
    doc.save(path)
    doc.close()
    return path
