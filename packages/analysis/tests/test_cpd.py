"""Duplicate code with PMD's CPD (ADD-A3), on the pinned PMD: copies across files of one language
become one finding per copy naming the others; nothing of the duplicated code is stored."""

from __future__ import annotations

from pathlib import Path

import pytest

from crp_analysis.engines.base import CancelToken
from crp_analysis.engines.cpd import RULE_ID, CpdAdapter, parse_report
from crp_core.domain.states import EngineState
from crp_devtools.engines import pmd_home

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[3]

JAVA = """package shop;

public class {name} {{
    public int total(int[] prices, int discount) {{
        int sum = 0;
        for (int i = 0; i < prices.length; i++) {{
            if (prices[i] > 100) {{
                sum += prices[i] - discount;
            }} else {{
                sum += prices[i];
            }}
        }}
        if (sum > 1000) {{
            sum = sum - (sum / 10);
        }}
        return sum;
    }}
}}
"""
TS = """export function {name}(items: {{ price: number; qty: number }}[], rate: number): number {{
  let total = 0;
  for (const item of items) {{
    if (item.qty > 10) {{
      total += item.price * item.qty * (1 - rate);
    }} else {{
      total += item.price * item.qty;
    }}
  }}
  if (total > 500) {{
    total = Math.round(total * 0.95);
  }}
  return total;
}}
"""


def _noop(_: str) -> None:
    return None


@pytest.fixture(scope="module")
def home() -> Path:
    found = pmd_home(REPO / ".local" / "engines")
    if not (found / "bin" / "pmd").is_file():
        pytest.fail("PMD is not installed; run `make engines` (engine tests are never skipped)")
    return found


def test_copies_across_files_are_reported_per_copy(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "src"
    (root / "shop").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "shop" / "Cart.java").write_text(JAVA.format(name="Cart"))
    (root / "shop" / "Order.java").write_text(JAVA.format(name="Order").replace("    ", "\t"))
    (root / "shop" / "Small.java").write_text("class Small { int a() { return 1; } }\n")
    (root / "web" / "cart.ts").write_text(TS.format(name="cartTotal"))
    (root / "web" / "order.ts").write_text(TS.format(name="orderTotal"))
    files = ["shop/Cart.java", "shop/Order.java", "shop/Small.java", "web/cart.ts", "web/order.ts"]
    outcome = CpdAdapter(
        home,
        java_heap="512m",
        timeout_seconds=120,
        max_output_bytes=10_000_000,
        minimum_tokens=50,
    ).run(root, files, cancel=CancelToken(), heartbeat=_noop)
    assert outcome.state is EngineState.SUCCEEDED, (outcome.error_message, outcome.problems)
    assert outcome.attempted == sorted(files) and outcome.problems == []
    java = sorted((f for f in outcome.findings if f.path.endswith(".java")), key=lambda f: f.path)
    assert [f.path for f in java] == ["shop/Cart.java", "shop/Order.java"]  # Small: unique
    assert {f.rule_id for f in java} == {RULE_ID} and java[0].start_line is not None
    assert java[0].identity == java[1].identity  # the same block, whitespace aside
    assert java[0].details and java[0].details["others"][0][0] == "shop/Order.java"
    assert "also in shop/Order.java" in java[0].message and java[0].severity == "low"
    assert {f.path for f in outcome.findings if f.path.endswith(".ts")} == {
        "web/cart.ts",
        "web/order.ts",
    }
    assert outcome.diagnostics["duplications"] == 2
    assert outcome.diagnostics["languages"] == ["java", "typescript"]
    report = (outcome.raw_report or b"").decode()
    assert "shop/Cart.java" in report and "prices" not in report  # no code is stored
    assert str(tmp_path) not in report


def test_without_pmd_the_engine_is_unavailable(tmp_path: Path) -> None:
    outcome = CpdAdapter(None, java_heap="256m", timeout_seconds=5, max_output_bytes=1000).run(
        tmp_path, ["A.java"], cancel=CancelToken(), heartbeat=_noop
    )
    assert outcome.state is EngineState.UNAVAILABLE and "CRP_PMD_HOME" in (
        outcome.error_message or ""
    )
    assert CpdAdapter(None, java_heap="1m", timeout_seconds=1, max_output_bytes=1).is_eligible(
        "a.py", "python"
    )


def test_report_parsing_keeps_locations_and_unreadable_files(tmp_path: Path) -> None:
    root = tmp_path / "src"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<pmd-cpd xmlns="https://pmd-code.org/schema/cpd-report">
  <duplication lines="45" tokens="300">
    <file line="10" endline="54" path="{root}/a/A.java"/>
    <file line="3" endline="47" path="{root}/b/B.java"/>
    <file line="1" endline="45" path="/elsewhere/C.java"/>
    <codefragment><![CDATA[secret code]]></codefragment>
  </duplication>
  <error filename="{root}/b/Broken.java" msg="Lexical error at {root}/b/Broken.java"/>
</pmd-cpd>""".encode()
    found, failed, report = parse_report(xml, root, {"a/A.java", "b/B.java", "b/Broken.java"})
    ((copies, lines, tokens, _),) = found
    assert [(c.path, c.line, c.end_line) for c in copies] == [
        ("a/A.java", 10, 54),
        ("b/B.java", 3, 47),
    ]  # a path outside the upload is ignored
    assert (lines, tokens) == (45, 300)
    assert "secret code" not in report and "a/A.java:10-54" in report
    assert failed == {"b/Broken.java": "Lexical error at b/Broken.java"}
    with pytest.raises(ValueError, match="DTD"):
        parse_report(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>', root, set())
