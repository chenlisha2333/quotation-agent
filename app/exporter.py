from pathlib import Path
from typing import List
from datetime import datetime, timedelta
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from .models import QuotationItem


def _safe_excel_text(value: str | None) -> str:
    """客户输入若以公式符号开头，作为普通文本写入 Excel。"""
    text = value or ""
    return f"'{text}" if text.lstrip().startswith(("=", "+", "-", "@")) else text


def export_quotation(
    items: List[QuotationItem],
    customer_name: str = "",
    output_path: Path = None,
    valid_days: int = 30  # 报价有效期（天）
) -> Path:
    """
    导出报价 Excel

    Args:
        items: 报价项列表
        customer_name: 客户名称
        output_path: 输出路径（可选）
        valid_days: 报价有效期天数，默认 30 天
    """
    now = datetime.now()
    valid_until = now + timedelta(days=valid_days)

    if output_path is None:
        output_path = Path(f"报价单_{customer_name or '客户'}_{now.strftime('%Y%m%d_%H%M%S')}.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "报价单"

    # 报价单头部信息
    ws.append(["报价单（人民币 CNY，内部草稿）"])
    ws.merge_cells("A1:N1")
    ws["A1"].font = Font(size=16, bold=True)
    ws["A1"].alignment = Alignment(horizontal="center")

    ws.append([_safe_excel_text(f"客户：{customer_name}"), "", "", "", "", "", "", "", "", "", "", f"报价日期：{now.strftime('%Y-%m-%d')}"])
    ws.append(["", "", "", "", "", "", "", "", "", "", "", "", f"有效期至：{valid_until.strftime('%Y-%m-%d')}"])
    ws.append([])  # 空行

    # 表头
    headers = ["序号", "产品名称", "型号", "规格", "品牌", "类别", "数量", "单位", "成本价", "加价率", "折扣", "单价", "总价", "备注"]
    ws.append(headers)
    header_row = ws.max_row

    for idx, item in enumerate(items, 1):
        ws.append([
            idx,
            _safe_excel_text(item.product.name),
            _safe_excel_text(item.product.model),
            _safe_excel_text(item.product.spec),
            _safe_excel_text(item.product.brand),
            item.product.category.value,
            item.product.quantity,
            _safe_excel_text(item.product.unit),
            float(item.cost_price) if item.cost_price is not None else "",
            float(item.profit_rate),
            float(item.discount),
            float(item.unit_price),
            float(item.total_price),
            _safe_excel_text(item.remark),
        ])
        for column in (9, 12, 13):
            ws.cell(row=header_row + idx, column=column).number_format = '¥#,##0.00'
        ws.cell(row=header_row + idx, column=10).number_format = '0.00%'

    # 样式
    header_fill = PatternFill(start_color="B4C7E7", end_color="B4C7E7", fill_type="solid")
    for cell in ws[header_row]:
        cell.fill = header_fill
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    # 合计行
    ws.append([])
    total_row = ws.max_row
    ws.cell(row=total_row, column=1, value="合计")
    exact_total = sum((i.total_price for i in items), Decimal("0.00"))
    ws.cell(row=total_row, column=13, value=float(exact_total))
    ws.cell(row=total_row, column=1).font = Font(bold=True)
    ws.cell(row=total_row, column=13).font = Font(bold=True)
    ws.cell(row=total_row, column=13).number_format = '¥#,##0.00'

    # Excel 数值单元格是浮点表示；保留十进制原文供内部核对。
    audit = wb.create_sheet("计算依据-内部")
    audit.append(["明细ID", "产品名称", "型号", "原始成本价", "加价率", "折扣", "舍入后单价", "行价", "价格来源", "价格精度", "规则版本", "舍入方式", "币种"])
    for idx, item in enumerate(items, 1):
        audit.append([
            item.item_id or str(idx), _safe_excel_text(item.product.name), _safe_excel_text(item.product.model),
            str(item.cost_price) if item.cost_price is not None else "",
            str(item.profit_rate), str(item.discount), str(item.unit_price),
            str(item.total_price), _safe_excel_text(item.price_source), item.price_precision or "",
            item.pricing_rule_id, item.rounding_mode, item.currency,
        ])
    audit.append(["合计", "", "", "", "", "", "", str(exact_total)])

    # 自动调整列宽（跳过合并单元格）
    for col_idx, col in enumerate(ws.columns, 1):
        max_length = 0
        col_letter = openpyxl.utils.get_column_letter(col_idx)
        for cell in col:
            try:
                # 跳过合并单元格
                if hasattr(cell, 'value') and cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max_length + 2, 40)

    wb.save(output_path)
    return output_path


def export_customer_quotation(
    items: List[QuotationItem], customer_name: str, task_id: str,
    revision_no: int, output_path: Path,
) -> Path:
    """客户版只写批准版本中的公开字段，不写成本、加价率或内部备注。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "客户报价单"
    ws.append(["施耐德电气产品报价单（人民币 CNY）"])
    ws.append([_safe_excel_text(f"客户：{customer_name}")])
    ws.append([f"报价编号：{task_id} / 版本：{revision_no}"])
    ws.append([])
    ws.append(["序号", "产品名称", "型号", "规格", "品牌", "数量", "单位", "单价（元）", "金额（元）"])
    for index, item in enumerate(items, 1):
        ws.append([
            index, _safe_excel_text(item.product.name), _safe_excel_text(item.product.model),
            _safe_excel_text(item.product.spec), _safe_excel_text(item.product.brand),
            item.product.quantity, _safe_excel_text(item.product.unit),
            float(item.unit_price), float(item.total_price),
        ])
        for column in (8, 9):
            ws.cell(row=5 + index, column=column).number_format = '¥#,##0.00'
    total = sum((item.total_price for item in items), Decimal("0.00"))
    ws.append(["合计", "", "", "", "", "", "", "", float(total)])
    ws.cell(row=ws.max_row, column=9).number_format = '¥#,##0.00'
    ws["A1"].font = Font(size=16, bold=True)
    for cell in ws[5]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
    for col in ws.columns:
        cells = list(col)
        ws.column_dimensions[cells[0].column_letter].width = min(
            max(len(str(cell.value or "")) for cell in cells) + 2, 50
        )
    wb.save(output_path)
    return output_path
