"""Grounding: cited, budget-bounded context; image vision branch; multi-doc."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _env():
    import core_boot
    core_boot.CONFIG["docintel_persist"] = False
    core_boot.CONFIG["docintel_use_embeddings"] = False
    core_boot.CONFIG["docintel_ocr_enabled"] = False
    import syrix_docintel.registry as reg
    reg._reset_for_tests()
    yield


def test_grounding_has_citations_and_material():
    import syrix_docintel as di
    d1 = di.analyze_upload("a.txt", b"The revenue was 4.2 million dollars in Q1.")
    d2 = di.analyze_upload("b.txt", b"The main risk is supply chain exposure in the EU.")
    ctx, cites = di.build_grounding([d1.id, d2.id], "revenue and risks")
    assert "ATTACHED MATERIAL" in ctx
    assert "4.2 million" in ctx and "supply chain" in ctx
    assert any("a.txt" in c for c in cites) and any("b.txt" in c for c in cites)


def test_grounding_respects_char_budget():
    import core_boot
    core_boot.CONFIG["docintel_context_char_budget"] = 400
    import syrix_docintel as di
    big = ("Section about widgets. " * 200)
    d = di.analyze_upload("big.txt", big.encode())
    ctx, _ = di.build_grounding([d.id], "widgets")
    assert len(ctx) < 400 + 600   # header/footer overhead only
    core_boot.CONFIG["docintel_context_char_budget"] = 9000


def test_grounding_empty_for_unknown_ids():
    import syrix_docintel as di
    ctx, cites = di.build_grounding(["doc_does_not_exist"], "hi")
    assert ctx == "" and cites == []


def test_image_vision_branch():
    import syrix_docintel as di
    from syrix_docintel import registry
    from syrix_docintel.types import DocumentObject
    # Register a fake image doc + blob (no real image needed for the branch).
    doc = DocumentObject(id="doc_img1", name="chart.png", kind="image", subtype="png")
    registry.put(doc)
    registry.put_blob("doc_img1", b"\x89PNGfakebytes")

    seen = {}

    def fake_vision(image_bytes, question):
        seen["q"] = question
        return "A bar chart showing revenue rising to 4.2M."

    ctx, cites = di.build_grounding(["doc_img1"], "what does this chart show?",
                                    vision_fn=fake_vision)
    assert "bar chart" in ctx and "chart.png" in " ".join(cites)
    assert seen.get("q") == "what does this chart show?"


def test_image_without_vision_is_honest():
    import syrix_docintel as di
    from syrix_docintel import registry
    from syrix_docintel.types import DocumentObject
    doc = DocumentObject(id="doc_img2", name="pic.png", kind="image", subtype="png")
    registry.put(doc)
    registry.put_blob("doc_img2", b"\x89PNGfake")
    ctx, _ = di.build_grounding(["doc_img2"], "describe", vision_fn=None)
    assert "no vision model" in ctx.lower() or "no text detected" in ctx.lower()
