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
    assert "PORT:-8000" in dockerfile


def test_public_fixture_demo_contract_is_visible_and_safe():
    page = Path("src/deal_grandpa/static/index.html").read_text()
    compose = Path("docker-compose.yml").read_text()
    readme = Path("README.md").read_text()
    assert "FIXTURE DEMO" in page
    assert "not clickable purchase links" in page
    assert "How it works" in page
    assert "Privacy and feedback" in page
    assert "image: deal-grandpa:demo" in compose
    assert "DEAL_GRANDPA_NOW" in compose
    assert "not live shopping data" in readme


def test_fixture_links_are_not_rendered_as_clickable_anchors():
    javascript = Path("src/deal_grandpa/static/app.js").read_text()
    assert "item.url" not in javascript
    assert "href=" not in javascript
    assert "<a " not in javascript
    assert "<a>" not in javascript
    assert "The server returned an unexpected decision" in javascript
    assert "could not reach its local API" in javascript
