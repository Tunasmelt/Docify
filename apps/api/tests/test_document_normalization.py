import pytest

from services.document_model import BBox, ElementType, ParsedElement, normalize_document_text


@pytest.mark.parametrize("source,expected", [
    ("\ufeffRevenue\u00a01,350,000\r\nProﬁt\u202f5%\u00ad\u200b\x00", "Revenue 1,350,000\nProfit 5%"),
    ("Cafe\u0301\n\n\nNext", "Café\n\nNext"),
    ("| Q1 | −1.25 |\n| Q2 | ½ |\n\tFormula: x² ≠ x", "| Q1 | −1.25 |\n| Q2 | ½ |\n\tFormula: x² ≠ x"),
    ("العربية\u200d हिन्दी", "العربية\u200d हिन्दी"),
])
def test_normalization_preserves_facts_layout_and_scripts(source, expected):
    assert normalize_document_text(source) == expected
    assert normalize_document_text(expected) == expected


def test_every_text_element_is_normalized_without_changing_provenance():
    box = BBox(1, 2, 3, 4)
    element = ParsedElement(ElementType.TABLE, 7, box, "ﬁgure\u00a02", "source-7")
    assert element.content == "figure 2"
    assert element.bbox is box and element.page_number == 7 and element.element_id == "source-7"



def test_figure_pixels_are_not_normalized():
    from PIL import Image
    with Image.new("RGB", (2, 2), "red") as image:
        element = ParsedElement(ElementType.FIGURE, 1, BBox(0, 0, 2, 2), image, "figure-1")
        assert element.content is image
        assert element.content.getpixel((0, 0)) == (255, 0, 0)
