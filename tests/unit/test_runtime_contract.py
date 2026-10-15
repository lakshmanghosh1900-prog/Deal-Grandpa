from pathlib import Path
import tomllib


def test_project_runtime_is_exactly_python_31211():
    assert Path(".python-version").read_text().strip() == "3.12.11"
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    lock = tomllib.loads(Path("uv.lock").read_text())
    assert project["requires-python"] == "==3.12.11"
    assert lock["requires-python"] == "==3.12.11"
    assert "python:3.12.11-slim" in Path("Dockerfile").read_text()


def test_docker_and_ci_use_locked_runtime():
    dockerfile = Path("Dockerfile").read_text()
    ci = Path(".github/workflows/ci.yml").read_text()
    assert "uv sync --locked" in dockerfile
    assert "python-version-file: .python-version" in ci
    assert "uv sync --locked" in ci
    assert "3, 12, 11" in ci


def test_fixture_links_are_not_rendered_as_clickable_anchors():
    javascript = Path("src/deal_grandpa/static/app.js").read_text()
    assert "item.url" not in javascript
    assert "href=" not in javascript
    assert "<a " not in javascript
    assert "<a>" not in javascript
