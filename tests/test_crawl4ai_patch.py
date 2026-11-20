"""The Chromium settings crawl4ai/patch-crawl4ai.py gives fetch_page's browser.

The script edits Crawl4AI's files at build time, so it is read here, not run.
"""
import ast
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "crawl4ai" / "patch-crawl4ai.py"


def _constants():
    tree = ast.parse(SCRIPT.read_text())
    names = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                names[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                names[node.targets[0].id] = node.value
    # EXTRA_ARGS joins strings with constants; evaluate it with the plain constants in scope.
    expr = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "EXTRA_ARGS")
    scope = {k: v for k, v in names.items() if isinstance(v, (str, list))}
    names["EXTRA_ARGS"] = eval(compile(ast.Expression(expr), str(SCRIPT), "eval"), {}, scope)
    return names


ARGS = _constants()["EXTRA_ARGS"]


def test_the_browser_keeps_its_same_origin_rule():
    assert "--disable-web-security" not in ARGS
    assert "--allow-insecure-localhost" not in ARGS


def test_one_disable_features_list_holds_the_google_calls():
    # Chromium keeps only the last --disable-features; Crawl4AI adds one of its own
    # earlier on the command line, so this one must carry the whole list.
    lists = [a for a in ARGS if a.startswith("--disable-features=")]
    assert len(lists) == 1
    features = set(lists[0].split("=", 1)[1].split(","))
    assert {"NetworkTimeServiceQuerying", "AutofillServerCommunication", "PreconnectToSearch"} <= features
    assert {"Translate", "OptimizationHints", "MediaRouter"} <= features  # Playwright's and Crawl4AI's


def test_googles_sign_in_and_push_services_point_nowhere():
    for switch in ("--gaia-url=", "--google-base-url=", "--gcm-checkin-url=",
                   "--gcm-registration-url=", "--gcm-mcs-endpoint="):
        assert [a for a in ARGS if a.startswith(switch)] == [switch + "http://127.0.0.1:9"]


def test_the_browser_still_accepts_self_signed_certificates():
    # Services on a home network often use self-signed certificates.
    assert "--ignore-certificate-errors" in ARGS


def test_no_proxy_stands_between_the_browser_and_your_network():
    text = SCRIPT.read_text()
    assert "proxy_config" not in text
    assert "proxy_pass" not in text
