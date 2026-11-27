import uuid
import hashlib
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Dict
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .approval import approve_quote
from .assistant import (
    AIConfigurationError, AIRequestError, ChatRequest, ChatResponse, answer_question,
)
from .auth import Approver, authorize
from .config import BASE_DIR, DATA_DIR, UPLOAD_DIR, MAX_UPLOAD_BYTES, DEFAULT_PROFIT_RATE, logger
from .parser import parse_excel
from .agent import run_quotation_agent, get_task_state, apply_manual_prices
from .exporter import export_quotation, export_customer_quotation
from .word_exporter import export_internal_word, export_customer_word
from .product_policy import product_issue
from .agent import GraphState, TaskSnapshot
from pydantic import BaseModel

@asynccontextmanager
async def lifespan(app: FastAPI):
    from .config import ensure_dirs
    ensure_dirs()
    db.init_db()
    logger.info("数据库初始化完成")
    yield


app = FastAPI(title="电气报价 Agent", version="0.5.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def read_root():
    return {"message": "电气报价 Agent", "currency": "CNY", "required_brand": "施耐德", "docs": "/docs"}


@app.get("/templates/inquiry")
def download_inquiry_template():
    path = BASE_DIR / "templates" / "施耐德询价表示例.xlsx"
    return FileResponse(path, filename=path.name)


def _task_response(state: GraphState, actor: Approver | None = None) -> dict:
    pending_products = [
        {
            "item_id": product.item_id,
            "name": product.name,
            "model": product.model,
            "brand": product.brand,
            "quantity": product.quantity,
            "unit": product.unit,
            "reason": state["review_reasons"][product.item_id],
            "can_enter_price": product_issue(product) is None,
        }
        for product in state["pending_products"]
    ]
    items = [item.model_dump(mode="json") for item in state["items"]]
    total_price = str(sum((item.total_price for item in state["items"]), Decimal("0.00")))
    if actor and actor.role in {"technical", "procurement"}:
        hidden = {"profit_rate", "discount", "unit_price", "total_price"}
        if actor.role == "technical":
            hidden |= {
                "cost_price", "price_source", "price_precision", "price_record_id",
                "price_source_reference", "price_valid_until", "price_evidence_status",
            }
        for item in items:
            for key in hidden:
                item[key] = None
            if actor.role == "technical":
                item["remark"] = None
                item["validation_warning"] = None
        total_price = None
    approvals = state.get("approvals", {})
    if actor and actor.role != "commercial":
        approvals = {
            stage: {"actor": record["actor"], "approved_at": record["approved_at"]}
            for stage, record in approvals.items()
        }
    return {
        "task_id": state["task_id"],
        "actor_role": actor.role if actor else None,
        "revision": state["revision"],
        "approvals": approvals,
        "published_revision": state.get("published_revision"),
        "status": state["status"],
        "currency": state["currency"],
        "price_source_selection": state.get("price_source_selection", "database"),
        "items": items,
        "total_price": total_price,
        "messages": state["messages"] if actor is None or actor.role == "commercial" else [],
        "pending_count": len(pending_products),
        "pending_products": pending_products,
    }


@app.post("/upload")
async def upload_excel(customer_name: str = Form(""), profit_rate: Decimal = Form(DEFAULT_PROFIT_RATE, ge=0), price_source: str = Form("database"), file: UploadFile = File(...), authorization: str | None = Header(None)):
    actor = authorize(authorization)
    if price_source not in {"database", "manual", "web", "software"}:
        raise HTTPException(status_code=422, detail="不支持的报价来源选项")
    if price_source in {"web", "software"}:
        source_name = "网页" if price_source == "web" else "外部软件/ERP"
        raise HTTPException(status_code=501, detail=f"{source_name}报价来源尚未配置适配器；当前可用来源为本地价格库和人工录价")
    if not profit_rate.is_finite() or profit_rate != DEFAULT_PROFIT_RATE:
        raise HTTPException(status_code=422, detail="当前询价规则固定为成本价 × 1.2（加价率 0.20）")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise HTTPException(status_code=400, detail="只支持 Excel 文件")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    input_file_id = file_path.stem
    digest = hashlib.sha256()
    size = 0
    try:
        with open(file_path, "wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Excel 文件不能超过 10 MiB")
                target.write(chunk)
                digest.update(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Excel 文件为空")
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    try:
        products = parse_excel(file_path)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"解析 Excel 失败: {str(e)}")

    if not products:
        raise HTTPException(status_code=400, detail="未解析到有效产品")

    try:
        result = run_quotation_agent(
            products, customer_name, profit_rate,
            input_file_id=input_file_id, input_file_sha256=digest.hexdigest(),
            input_file_extension=suffix,
            price_source_selection=price_source,
        )
    except db.PriceLookupError as exc:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=exc.public_message) from exc

    return _task_response(result, actor)


@app.get("/tasks/{task_id}")
def get_task(task_id: str, authorization: str | None = Header(None)):
    """获取任务状态和结果"""
    actor = authorize(authorization)
    state = get_task_state(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="任务不存在")

    return _task_response(state, actor)


@app.post("/tasks/{task_id}/confirm")
def confirm_prices(task_id: str, prices: Dict[str, Decimal], authorization: str | None = Header(None)):
    """
    人工确认缺失价格的产品
    prices: {"line-1": 成本价, ...}
    """
    actor = authorize(authorization)
    if actor and actor.role != "procurement":
        raise HTTPException(status_code=403, detail="仅采购角色可录入成本价")
    if any(not price.is_finite() or price <= 0 for price in prices.values()):
        raise HTTPException(status_code=422, detail="人工成本价必须是正的有限数字")
    try:
        result = apply_manual_prices(task_id, prices)
    except db.TaskConflictError as exc:
        raise HTTPException(status_code=409, detail="任务已被其他操作更新，请刷新后重试") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    return _task_response(result, actor)


class ExportRequest(BaseModel):
    task_id: str
    format: str = "internal"
    file_type: str = "xlsx"


class ApprovalRequest(BaseModel):
    reason: str


@app.post("/assistant/chat", response_model=ChatResponse)
async def chat_with_assistant(request: ChatRequest, authorization: str | None = Header(None)):
    """只读问答；若指定任务，只向模型提供当前角色可见的摘要。"""
    actor = authorize(authorization)
    context = None
    if request.task_id:
        state = get_task_state(request.task_id)
        if state is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        view = _task_response(state, actor)
        context = {
            "task_id": view["task_id"], "revision": view["revision"],
            "status": view["status"], "currency": view["currency"],
            "pending_count": view["pending_count"],
            "published_revision": view["published_revision"],
            "item_count": len(view["items"]),
            "items": [
                {
                    "item_id": item["item_id"], "name": item["product"]["name"],
                    "model": item["product"]["model"],
                    "quantity": item["product"]["quantity"],
                    "unit": item["product"]["unit"],
                    "cost_price": item["cost_price"],
                    "unit_price": item["unit_price"],
                    "total_price": item["total_price"],
                }
                for item in view["items"][:30]
            ],
        }
    try:
        return await answer_question(request, context)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AIRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/tasks/{task_id}/approve/{stage}")
def approve_task_endpoint(
    task_id: str, stage: str, request: ApprovalRequest,
    authorization: str | None = Header(None),
):
    if stage not in {"technical", "procurement", "commercial"}:
        raise HTTPException(status_code=404, detail="审批阶段不存在")
    approver = authorize(authorization, required_role=stage)
    try:
        state = approve_quote(task_id, stage, approver, request.reason)
    except db.TaskConflictError as exc:
        raise HTTPException(status_code=409, detail="任务已更新，请刷新后重试") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_response(state, approver)


@app.post("/export")
def export_quotation_endpoint(request: ExportRequest, authorization: str | None = Header(None)):
    if request.format not in {"internal", "customer"}:
        raise HTTPException(status_code=422, detail="导出格式只支持 internal 或 customer")
    if request.file_type not in {"xlsx", "docx"}:
        raise HTTPException(status_code=422, detail="文件类型只支持 xlsx 或 docx")
    actor = authorize(authorization, required_role="commercial" if request.format == "customer" else None)
    if request.format == "internal" and actor and actor.role == "technical":
        raise HTTPException(status_code=403, detail="技术角色无权导出含成本的内部草稿")
    state = get_task_state(request.task_id)
    if state is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if state["status"] != "完成" or not state["items"] or any(
        not item.item_id or product_issue(item.product)
        or item.cost_price is None or item.cost_price <= 0 or item.currency != "CNY"
        for item in state["items"]
    ):
        raise HTTPException(status_code=409, detail="仅能导出已补齐价格的施耐德人民币报价草稿")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if request.format == "customer":
        revision_no = state.get("published_revision")
        quote = db.load_quote_revision(request.task_id, revision_no) if revision_no else None
        if quote is None or set(quote.get("approvals", {})) != {"technical", "procurement", "commercial"}:
            raise HTTPException(status_code=409, detail="须完成技术、采购、商务审批并发布报价版本")
        frozen = TaskSnapshot.model_validate(quote)
        output_path = DATA_DIR / f"客户报价单_{request.task_id}_v{revision_no}.{request.file_type}"
        if request.file_type == "docx":
            export_path = export_customer_word(
                frozen.items, frozen.customer_name, frozen.task_id, revision_no, output_path,
                published_at=frozen.approvals["commercial"]["approved_at"],
            )
        else:
            export_path = export_customer_quotation(
                frozen.items, frozen.customer_name, frozen.task_id, revision_no, output_path,
            )
    else:
        output_path = DATA_DIR / f"内部草稿_{request.task_id}.{request.file_type}"
        if request.file_type == "docx":
            export_path = export_internal_word(
                state["items"], state["customer_name"], state["task_id"], output_path,
            )
        else:
            export_path = export_quotation(
                state["items"], customer_name=state["customer_name"], output_path=output_path,
            )
    return FileResponse(export_path, filename=export_path.name)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
