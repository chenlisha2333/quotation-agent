"""从已计算报价生成 Word；客户版仅使用公开字段。"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from docx import Document
from docx.document import Document as DocumentType
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .models import QuotationItem

_BLUE = "183B56"
_BORDER = "D9D9D9"
_PALE = "F3F7FA"
_FONT = "Noto Sans CJK SC"


def _set_font(run) -> None:
    run.font.name = _FONT
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.rFonts
    if r_fonts is not None:
        r_fonts.set(qn("w:eastAsia"), _FONT)


def _money(value: Decimal) -> str:
    return f"¥{value:,.2f}"


def _set_cell_shading(cell, color: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(shading)


def _set_cell_padding(cell) -> None:
    margins = OxmlElement("w:tcMar")
    for side in ("top", "left", "bottom", "right"):
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:w"), "100")
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    cell._tc.get_or_add_tcPr().append(margins)


def _style_table(table, widths: Sequence[float]) -> None:
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for column, width in zip(table.columns, widths):
        column.width = Inches(width)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{edge}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "4")
        border.set(qn("w:color"), _BORDER)
        borders.append(border)
    table._tbl.tblPr.append(borders)
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for row_index, row in enumerate(table.rows):
        for column_index, cell in enumerate(row.cells):
            cell.width = Inches(widths[column_index])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_padding(cell)
            if row_index == 0:
                _set_cell_shading(cell, _BLUE)
            elif row_index % 2 == 0:
                _set_cell_shading(cell, _PALE)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.space_before = Pt(0)
                if column_index == 0 or (column_index > 1 and column_index < len(widths) - 2):
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif column_index >= len(widths) - 2:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                for run in paragraph.runs:
                    _set_font(run)
                    run.font.size = Pt(9)
                    if row_index == 0:
                        run.font.bold = True
                        run.font.color.rgb = RGBColor(255, 255, 255)


def _new_document(title: str, customer_name: str, task_id: str) -> DocumentType:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)
    normal = doc.styles["Normal"]
    normal.font.name = _FONT
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT)
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.space_after = Pt(7)
    title_style = doc.styles["Title"]
    title_style.font.name = _FONT
    title_style._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT)
    title_style.font.size = Pt(18)
    title_style.font.bold = True
    title_style.font.color.rgb = RGBColor(0, 0, 0)
    for style_name in ("Heading 1", "Heading 2"):
        doc.styles[style_name].font.color.rgb = RGBColor(0, 0, 0)
        doc.styles[style_name].font.name = _FONT
        doc.styles[style_name]._element.rPr.rFonts.set(qn("w:eastAsia"), _FONT)
    doc.add_paragraph(title, style="Title")
    doc.add_paragraph(f"客户：{customer_name or '未填写'}    报价编号：{task_id}")
    doc.add_paragraph("币种：人民币（CNY）")
    doc.core_properties.title = title
    return doc


def export_internal_word(
    items: Sequence[QuotationItem], customer_name: str, task_id: str, output_path: Path,
) -> Path:
    """内部草稿包含成本与规则依据；仅由现有 API 的内部导出权限调用。"""
    doc = _new_document("电气产品报价内部草稿", customer_name, task_id)
    doc.add_paragraph(f"生成日期：{date.today().isoformat()}。本文件供内部核对，税费及商务条款待确认。")
    headers = ("序号", "产品及型号", "数量", "单位", "成本价", "报价单价", "金额")
    table = doc.add_table(rows=1, cols=len(headers))
    for cell, label in zip(table.rows[0].cells, headers):
        cell.text = label
    for index, item in enumerate(items, 1):
        cells = table.add_row().cells
        values = (
            str(index), f"{item.product.name}\n{item.product.model or ''}".strip(),
            str(item.product.quantity), item.product.unit or "", _money(item.cost_price or Decimal("0")),
            _money(item.unit_price), _money(item.total_price),
        )
        for cell, value in zip(cells, values):
            cell.text = value
    _style_table(table, (0.4, 2.35, 0.55, 0.55, 1.05, 1.1, 1.1))
    total = sum((item.total_price for item in items), Decimal("0.00"))
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph.add_run(f"合计：{_money(total)}").bold = True
    doc.add_heading("计算依据", level=1)
    for item in items:
        doc.add_paragraph(
            f"{item.item_id}  {item.product.name} {item.product.model or ''}："
            f"成本 {_money(item.cost_price or Decimal('0'))}，加价率 {item.profit_rate}，"
            f"折扣 {item.discount}，规则 {item.pricing_rule_id}，"
            f"舍入 {item.rounding_mode}，价格来源 {item.price_source or '未记录'}。"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path


def export_customer_word(
    items: Sequence[QuotationItem], customer_name: str, task_id: str,
    revision_no: int, output_path: Path, published_at: str | None = None,
) -> Path:
    """客户 Word 只写冻结报价的公开字段，不写成本和内部备注。"""
    doc = _new_document("施耐德电气产品报价单", customer_name, task_id)
    doc.add_paragraph(f"报价版本：v{revision_no}" + (f"    发布日期：{published_at[:10]}" if published_at else ""))
    headers = ("序号", "产品及型号", "数量", "单位", "单价", "金额")
    table = doc.add_table(rows=1, cols=len(headers))
    for cell, label in zip(table.rows[0].cells, headers):
        cell.text = label
    for index, item in enumerate(items, 1):
        cells = table.add_row().cells
        values = (
            str(index), f"{item.product.name}\n{item.product.model or ''}".strip(),
            str(item.product.quantity), item.product.unit or "",
            _money(item.unit_price), _money(item.total_price),
        )
        for cell, value in zip(cells, values):
            cell.text = value
    _style_table(table, (0.4, 2.9, 0.6, 0.55, 1.25, 1.4))
    total = sum((item.total_price for item in items), Decimal("0.00"))
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph.add_run(f"合计：{_money(total)}").bold = True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path
