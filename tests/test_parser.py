from pathlib import Path
import pandas as pd
import pytest
from app.parser import parse_excel, parse_excel_with_rows, infer_category, normalize_column_name
from app.models import Product, ProductCategory


def test_normalize_column_name():
    assert normalize_column_name("产品名称") == "name"
    assert normalize_column_name("型号") == "model"
    assert normalize_column_name("数量") == "quantity"


def test_infer_category():
    assert infer_category("低压配电柜") == ProductCategory.CABINET
    assert infer_category("断路器") == ProductCategory.COMPONENT
    assert infer_category("铜排") == ProductCategory.AUXILIARY


def test_parse_excel(tmp_path):
    df = pd.DataFrame({
        "产品名称": ["断路器", "配电箱"],
        "型号": ["DZ47-63", "PZ30-12"],
        "数量": [10, 2],
        "单位": ["个", "台"],
    })
    file_path = tmp_path / "test.xlsx"
    df.to_excel(file_path, index=False)

    products = parse_excel(file_path)
    assert len(products) == 2
    assert products[0].name == "断路器"
    assert products[0].quantity == 10


@pytest.mark.parametrize("quantity", [None, 2.5, "10", True, 0, -1, float("inf"), 1_000_000_000_000_000])
def test_parse_excel_rejects_invalid_quantities_with_row_number(tmp_path, quantity):
    file_path = tmp_path / "invalid_quantity.xlsx"
    pd.DataFrame({"产品名称": ["断路器"], "数量": [quantity]}).to_excel(file_path, index=False)

    with pytest.raises(ValueError, match=r"第 2 行.*数量"):
        parse_excel(file_path)


def test_parse_excel_accepts_integral_numeric_cells(tmp_path):
    file_path = tmp_path / "integral_quantities.xlsx"
    pd.DataFrame({"产品名称": ["断路器", "接触器"], "数量": [10, 2.0]}).to_excel(file_path, index=False)

    products = parse_excel(file_path)

    assert [product.quantity for product in products] == [10, 2]


def test_parse_excel_accepts_maximum_exact_excel_integer(tmp_path):
    file_path = tmp_path / "max_quantity.xlsx"
    pd.DataFrame({"产品名称": ["断路器"], "数量": [999_999_999_999_999]}).to_excel(file_path, index=False)

    products = parse_excel(file_path)

    assert products[0].quantity == 999_999_999_999_999


def test_parse_excel_keeps_blank_name_rows_skipped_and_reports_physical_row(tmp_path):
    file_path = tmp_path / "physical_row.xlsx"
    pd.DataFrame({"产品名称": [None, "断路器"], "数量": [None, 2.5]}).to_excel(file_path, index=False)

    with pytest.raises(ValueError, match=r"第 3 行.*数量"):
        parse_excel(file_path)


def test_parse_excel_with_rows_preserves_physical_worksheet_row_numbers(tmp_path):
    file_path = tmp_path / "source_rows.xlsx"
    pd.DataFrame({"产品名称": [None, "断路器"], "数量": [None, 2]}).to_excel(file_path, index=False)

    products = parse_excel_with_rows(file_path)

    assert [(row_number, product.name, product.quantity) for row_number, product in products] == [(3, "断路器", 2)]


def test_parse_excel_normalizes_missing_optional_text_to_none(tmp_path):
    file_path = tmp_path / "optional_fields.xlsx"
    pd.DataFrame({
        "产品名称": ["断路器", "接触器"],
        "数量": [1, 2],
        "型号": [None, "  "],
        "规格": ["", None],
        "品牌": [None, "  "],
        "单位": ["", "  "],
    }).to_excel(file_path, index=False)

    products = parse_excel(file_path)

    assert [(product.model, product.spec, product.brand, product.unit) for product in products] == [
        (None, None, None, "个"),
        (None, None, None, "个"),
    ]


def test_parse_excel_quantity_error_has_stable_category_and_row(tmp_path):
    file_path = tmp_path / "invalid_quantity.xlsx"
    pd.DataFrame({"产品名称": ["断路器"], "数量": [2.5]}).to_excel(file_path, index=False)

    with pytest.raises(ValueError) as error:
        parse_excel_with_rows(file_path)

    assert error.value.category == "invalid_quantity"
    assert error.value.row_number == 2
