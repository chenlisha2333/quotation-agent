from pathlib import Path
import pandas as pd
from app.parser import parse_excel, infer_category, normalize_column_name
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
