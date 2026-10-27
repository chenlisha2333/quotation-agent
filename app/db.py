import sqlite3
import json
from decimal import Decimal
from datetime import datetime
from typing import List
from .config import DATA_DIR, logger
from .models import PriceRecord, Product
from .product_policy import eligible_price, normalized_brand, product_issue


def get_conn():
    # 确保目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "quotation.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name TEXT NOT NULL,
        model TEXT,
        category TEXT,
        source TEXT NOT NULL,
        price REAL NOT NULL,
        price_decimal TEXT,
        brand TEXT,
        spec TEXT,
        unit TEXT,
        currency TEXT,
        source_reference TEXT,
        valid_until TEXT,
        verified INTEGER NOT NULL DEFAULT 0,
        confidence REAL DEFAULT 0.5,
        fetched_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS quotation_tasks (
        task_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        input_file TEXT,
        result TEXT,
        version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        details TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES quotation_tasks(task_id),
        UNIQUE(task_id, version)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS quote_revisions (
        task_id TEXT NOT NULL,
        revision_no INTEGER NOT NULL,
        snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (task_id, revision_no),
        FOREIGN KEY (task_id) REFERENCES quotation_tasks(task_id)
    )
    """)
    # 增列兼容旧库。旧记录缺少品牌、币种和核准证据，不参与自动报价。
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(price_records)")}
    for name, definition in {
        "price_decimal": "TEXT", "brand": "TEXT", "spec": "TEXT", "unit": "TEXT",
        "currency": "TEXT", "source_reference": "TEXT", "valid_until": "TEXT",
        "verified": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in columns:
            cursor.execute(f"ALTER TABLE price_records ADD COLUMN {name} {definition}")
    task_columns = {row[1] for row in cursor.execute("PRAGMA table_info(quotation_tasks)")}
    if "version" not in task_columns:
        cursor.execute("ALTER TABLE quotation_tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


class TaskConflictError(Exception):
    """任务版本已变化；调用方必须重新读取后再决定下一步。"""


def load_task_snapshot(task_id: str) -> tuple[int, dict] | None:
    init_db()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT version, result FROM quotation_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["result"]:
        return None
    return row["version"], json.loads(row["result"])


def save_task_snapshot(
    task_id: str, status: str, snapshot: dict, expected_version: int,
    event_type: str, details: dict | None = None,
    published_quote: tuple[int, dict] | None = None,
) -> int:
    """状态与事件在同一事务提交；版本比较防止旧状态覆盖新状态。"""
    init_db()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT version FROM quotation_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        actual = row["version"] if row else 0
        if actual != expected_version:
            raise TaskConflictError(f"任务版本已变化：期望 {expected_version}，实际 {actual}")
        next_version = actual + 1
        payload = json.dumps(snapshot, ensure_ascii=False)
        if row:
            conn.execute(
                "UPDATE quotation_tasks SET status = ?, result = ?, version = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (status, payload, next_version, task_id),
            )
        else:
            conn.execute(
                "INSERT INTO quotation_tasks (task_id, status, result, version) VALUES (?, ?, ?, ?)",
                (task_id, status, payload, next_version),
            )
        conn.execute(
            "INSERT INTO task_events (task_id, version, event_type, details) VALUES (?, ?, ?, ?)",
            (task_id, next_version, event_type, json.dumps(details or {}, ensure_ascii=False)),
        )
        if published_quote is not None:
            revision_no, quote = published_quote
            conn.execute(
                "INSERT INTO quote_revisions (task_id, revision_no, snapshot) VALUES (?, ?, ?)",
                (task_id, revision_no, json.dumps(quote, ensure_ascii=False)),
            )
        conn.commit()
        return next_version
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_task_events(task_id: str) -> list[dict]:
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT version, event_type, details, created_at FROM task_events "
            "WHERE task_id = ? ORDER BY version", (task_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"version": row["version"], "event_type": row["event_type"],
         "details": json.loads(row["details"]), "created_at": row["created_at"]}
        for row in rows
    ]


def load_quote_revision(task_id: str, revision_no: int) -> dict | None:
    init_db()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT snapshot FROM quote_revisions WHERE task_id = ? AND revision_no = ?",
            (task_id, revision_no),
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["snapshot"]) if row else None


def insert_price(record: PriceRecord):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO price_records (
            product_name, model, spec, brand, unit, currency, source, source_reference,
            valid_until, verified, price, price_decimal, confidence, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record.product_name,
        record.model,
        record.spec,
        normalized_brand(record.brand),
        record.unit,
        record.currency,
        record.source,
        record.source_reference,
        record.valid_until,
        int(record.verified),
        float(record.price),  # 只填兼容列，精确值读取 price_decimal
        str(record.price),
        record.confidence,
        record.fetched_at or datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def _price_record_from_row(row: sqlite3.Row) -> PriceRecord:
    values = dict(row)
    exact = values.get("price_decimal")
    values["price"] = Decimal(exact if exact is not None else str(values["price"]))
    if values.get("confidence") is None:
        values["confidence"] = 0.5
    values["price_precision"] = "decimal_text" if exact is not None else "legacy_real"
    return PriceRecord(**values)


def search_local_price(product: Product) -> List[PriceRecord]:
    """只返回当前有效、已核准、同品牌同型号规格单位的人民币价格。"""
    if product_issue(product):
        return []
    try:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM price_records WHERE product_name = ? AND currency = 'CNY' "
                "AND verified = 1 AND price_decimal IS NOT NULL ORDER BY fetched_at DESC",
                (product.name,),
            ).fetchall()
        finally:
            conn.close()
        records = [_price_record_from_row(row) for row in rows]
        return [record for record in records if eligible_price(product, record)]
    except Exception as e:
        logger.warning(f"本地价格查询失败: {e}")
        return []


def get_historical_prices(product: Product, limit: int = 5) -> List[PriceRecord]:
    """价格核查只比较同一施耐德型号、规格、单位的有效人民币记录。"""
    return search_local_price(product)[:limit]
