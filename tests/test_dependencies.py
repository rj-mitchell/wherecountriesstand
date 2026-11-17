"""How the site takes the Where They Stand sites' shared package (wts-core), and that it uses only what the package
makes public."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    [wts_core] = [d for d in project["dependencies"] if canonical(d.split("[")[0].split("@")[0].strip()) == "wts-core"]
    return wts_core


def test_the_shared_package_is_the_wheel_of_a_release_with_the_extras_this_site_uses():
    name, address = (part.strip() for part in requirement().split("@", 1))
    # Typer for the shared commands, httpx for off-site copies, the thread-safe DuckDB connection the vote finder
    # uses, and the libraries behind the model, the embedder and the index
    assert name == "wts-core[cli,offsite,duckdb,ai]"
    # by its address: the Docker image has no git (wts-core docs/adr/0006)
    assert re.fullmatch(r"https://github\.com/rj-mitchell/wts-core/releases/download/v(\d+\.\d+\.\d+)/"
                        r"wts_core-\1-py3-none-any\.whl", address)
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {canonical(p["name"]): p for p in lock["package"]}
    assert locked["wts-core"]["source"]["url"] == address
    assert "git" not in (ROOT / "Dockerfile").read_text().split()  # nothing in the image's build asks for it


def test_ci_calls_the_shared_workflows_at_the_tag_of_the_package_installed():
    """One number to raise: the release of wts-core in pyproject.toml is the tag every shared workflow and action is
    called at in .github/workflows/ci.yml (wts-core docs/adr/0010)."""
    version = re.search(r"/download/(v[\d.]+)/", requirement())[1]
    called = re.findall(r"uses: rj-mitchell/wts-core/\S+@(\S+)", (ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert len(called) >= 3 and set(called) == {version}


def test_the_site_uses_only_what_the_shared_package_makes_public():
    """Everything the site (and its tests) takes from wts-core is in the ``__all__`` of the module it comes from: the
    package may change anything else without notice (wts-core docs/adr/0007)."""
    import ast
    import importlib

    def public(module: str, name: str) -> bool:
        try:
            importlib.import_module(f"{module}.{name}")  # a module of the package
            return True
        except ImportError:
            return name in importlib.import_module(module).__all__

    checked = 0
    for path in (*sorted((ROOT / "src").rglob("*.py")), *sorted((ROOT / "tests").rglob("*.py"))):
        if path.name == "record.py":  # runs only on the code as it was, before the package
            continue
        tree = ast.parse(path.read_text())
        modules: dict[str, str] = {}  # a name in this file -> the module it stands for
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "wts_core":
                for alias in node.names:
                    assert public(node.module, alias.name), f"{path.name}: {node.module}.{alias.name} is private"
                    checked += 1
                    try:
                        importlib.import_module(f"{node.module}.{alias.name}")
                        modules[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                    except ImportError:
                        pass
            elif isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "wts_core" for a in node.names), f"{path.name}: import wts_core"
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules \
                    and not _shadowed(tree, node.value.id):
                assert public(modules[node.value.id], node.attr), f"{path.name}: {modules[node.value.id]}.{node.attr}"
                checked += 1
            # and nothing reached through an object either: no attribute of the package's that starts with "_"
        assert not re.search(r"\b(index|desk|parts|replies|record|db|state)\._[a-z]", path.read_text()), path.name
    assert checked > 100


def _shadowed(tree, name: str) -> bool:
    """Whether a file also gives the name to something of its own (a variable called ``replies``, say)."""
    import ast

    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store) for n in ast.walk(tree)) \
        or any(isinstance(n, ast.arg) and n.arg == name for n in ast.walk(tree))


def test_no_copy_of_a_replaced_module_is_left():
    """What moved into the package is gone from the site: the files, and the functions that were in them."""
    src = ROOT / "src" / "wcs"
    for gone in ("api/usage.py", "api/statements.py", "backup.py", "offsite.py", "rag/verify.py", "rag/neutral.py",
                 "rag/british.py", "rag/typography.py", "embed/base.py", "store/shared.py"):
        assert not (src / gone).exists(), gone
    code = "\n".join(p.read_text() for p in src.rglob("*.py"))
    for name in ("def cost_usd", "def token_counts", "def check_caps", "class InFlight", "class RateLimiter",
                 "def cache_key", "def visitor_key", "def network_of", "def make_backup", "def restore_backup",
                 "def recheck_flags", "def flag_loaded", "def curl_quotes", "def to_british", "def make_client",
                 "def usage_dict", "def cached_system", "class HashingEmbedder", "class FastEmbedEmbedder",
                 "class SharedConnection", "class SearchIndex", "def sse(", "def forget_emails", "def recency_factor"):
        assert name not in code, name
