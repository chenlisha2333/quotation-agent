"""合成尺寸下的确定性二维安装板布局与最小 ASCII DXF 输出。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from decimal import Decimal
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SYNTHETIC_SPEC = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "synthetic_panel_spec.json"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "cad" / "synthetic_layout.dxf"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_PANEL_MM = Decimal("5000")
_MAX_COMPONENT_COUNT = 50


class Rectangle(BaseModel):
    x: Decimal = Field(ge=0)
    y: Decimal = Field(ge=0)
    width: Decimal = Field(gt=0, le=_MAX_PANEL_MM)
    height: Decimal = Field(gt=0, le=_MAX_PANEL_MM)

    @field_validator("x", "y", "width", "height")
    @classmethod
    def require_finite_geometry(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("二维尺寸必须是有限数值")
        return value

    @property
    def right(self) -> Decimal:
        return self.x + self.width

    @property
    def top(self) -> Decimal:
        return self.y + self.height


class Keepout(Rectangle):
    keepout_id: str

    @field_validator("keepout_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("禁布区 ID 只能含字母、数字、点、下划线和连字符")
        return value


class ComponentFootprint(BaseModel):
    bom_item_id: str
    model: str
    quantity: int = Field(ge=1, le=_MAX_COMPONENT_COUNT)
    width: Decimal = Field(gt=0, le=_MAX_PANEL_MM)
    height: Decimal = Field(gt=0, le=_MAX_PANEL_MM)
    footprint_version: str
    source_reference: str

    @field_validator("width", "height")
    @classmethod
    def require_finite_footprint(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("元器件二维外形必须是有限数值")
        return value

    @field_validator("bom_item_id", "model", "footprint_version", "source_reference")
    @classmethod
    def validate_ascii_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("合成组件标识只能含字母、数字、点、下划线和连字符")
        return value


class PanelDesignSpec(BaseModel):
    data_class: Literal["synthetic"]
    design_spec_id: str
    cabinet_type: str
    board_width: Decimal = Field(gt=0, le=_MAX_PANEL_MM)
    board_height: Decimal = Field(gt=0, le=_MAX_PANEL_MM)
    edge_clearance: Decimal = Field(ge=0, le=_MAX_PANEL_MM)
    minimum_gap: Decimal = Field(ge=0, le=_MAX_PANEL_MM)
    keepouts: list[Keepout] = Field(default_factory=list, max_length=30)
    components: list[ComponentFootprint] = Field(min_length=1, max_length=10)

    @field_validator("board_width", "board_height", "edge_clearance", "minimum_gap")
    @classmethod
    def require_finite_constraints(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("安装板约束必须是有限数值")
        return value

    @field_validator("design_spec_id", "cabinet_type")
    @classmethod
    def validate_spec_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("DesignSpec 标识只能含字母、数字、点、下划线和连字符")
        return value

    @model_validator(mode="after")
    def validate_design_bounds(self):
        usable_width = self.board_width - 2 * self.edge_clearance
        usable_height = self.board_height - 2 * self.edge_clearance
        if usable_width <= 0 or usable_height <= 0:
            raise ValueError("安装板边缘净空使有效区域为空")
        item_ids = [item.bom_item_id for item in self.components]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("BOM item_id 必须唯一")
        if sum(item.quantity for item in self.components) > _MAX_COMPONENT_COUNT:
            raise ValueError("合成布局最多接受 50 个元器件实例")
        if any(item.width > usable_width or item.height > usable_height for item in self.components):
            raise ValueError("元器件外形大于安装板有效区域")
        for zone in self.keepouts:
            if zone.right > self.board_width or zone.top > self.board_height:
                raise ValueError(f"禁布区 {zone.keepout_id} 超出安装板")
        zone_ids = [zone.keepout_id for zone in self.keepouts]
        if len(zone_ids) != len(set(zone_ids)):
            raise ValueError("禁布区 ID 必须唯一")
        return self


class Placement(BaseModel):
    bom_item_id: str
    instance_index: int = Field(ge=1)
    model: str
    x: Decimal = Field(ge=0)
    y: Decimal = Field(ge=0)
    width: Decimal = Field(gt=0)
    height: Decimal = Field(gt=0)

    @property
    def rect(self) -> Rectangle:
        return Rectangle(x=self.x, y=self.y, width=self.width, height=self.height)


class LayoutProposal(BaseModel):
    design_spec_id: str
    placements: list[Placement]
    status: Literal["draft_for_review"] = "draft_for_review"


class CadArtifactManifest(BaseModel):
    schema_version: Literal["1"] = "1"
    artifact_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    data_class: Literal["synthetic"]
    design_spec_id: str
    design_spec_filename: str
    design_spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dxf_filename: str
    dxf_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generated_at: datetime
    generator_id: Literal["deterministic-shelf-v1"]
    layout_validation: Literal["passed"]
    dxf_format_validation: Literal["passed"]
    dxf_geometry_validation: Literal["passed"]
    review_status: Literal["not_reviewed"] = "not_reviewed"
    engineering_use_approved: Literal[False] = False


class LayoutError(ValueError):
    """合成几何约束下不存在本算法可给出的布局。"""


def _clearance(a: Rectangle, b: Rectangle) -> Decimal:
    dx = max(b.x - a.right, a.x - b.right, Decimal("0"))
    dy = max(b.y - a.top, a.y - b.top, Decimal("0"))
    return (dx * dx + dy * dy).sqrt()


def _intersects(a: Rectangle, b: Rectangle) -> bool:
    return a.x < b.right and a.right > b.x and a.y < b.top and a.top > b.y


def _violates_gap(a: Rectangle, b: Rectangle, gap: Decimal) -> bool:
    return _intersects(a, b) or _clearance(a, b) < gap


def generate_layout(spec: PanelDesignSpec) -> LayoutProposal:
    """按 BOM 顺序做可重复的横向排布；仅供算法演示，不做工程选型。"""
    left = spec.edge_clearance
    bottom = spec.edge_clearance
    right = spec.board_width - spec.edge_clearance
    top = spec.board_height - spec.edge_clearance
    x, y, row_height = left, bottom, Decimal("0")
    placements: list[Placement] = []

    for component in spec.components:
        for instance_index in range(1, component.quantity + 1):
            if x + component.width > right:
                x, y, row_height = left, y + row_height + spec.minimum_gap, Decimal("0")
            while True:
                if y + component.height > top:
                    raise LayoutError(
                        f"BOM {component.bom_item_id} 第 {instance_index} 个实例无法放入合成安装板"
                    )
                candidate = Rectangle(
                    x=x, y=y, width=component.width, height=component.height,
                )
                blocking = next((
                    zone for zone in spec.keepouts
                    if _violates_gap(candidate, zone, spec.minimum_gap)
                ), None)
                if blocking is None:
                    break
                next_x = blocking.right + spec.minimum_gap
                if next_x + component.width <= right:
                    x = next_x
                else:
                    x, y = left, max(y, blocking.top + spec.minimum_gap)
                    row_height = Decimal("0")

            placements.append(Placement(
                bom_item_id=component.bom_item_id,
                instance_index=instance_index,
                model=component.model,
                x=x,
                y=y,
                width=component.width,
                height=component.height,
            ))
            x += component.width + spec.minimum_gap
            row_height = max(row_height, component.height)

    return LayoutProposal(design_spec_id=spec.design_spec_id, placements=placements)


def validate_layout(spec: PanelDesignSpec, proposal: LayoutProposal) -> list[str]:
    """独立重算 BOM、边界、禁布区、碰撞和最小间距错误。"""
    issues: list[str] = []
    if proposal.design_spec_id != spec.design_spec_id:
        issues.append("design_spec_id 与输入快照不一致")
    expected = {item.bom_item_id: item for item in spec.components}
    actual_counts: dict[str, int] = {}
    seen_instances: set[tuple[str, int]] = set()
    placements: list[Placement] = []

    for placement in proposal.placements:
        actual_counts[placement.bom_item_id] = actual_counts.get(placement.bom_item_id, 0) + 1
        instance_key = (placement.bom_item_id, placement.instance_index)
        if instance_key in seen_instances:
            issues.append(f"BOM {placement.bom_item_id} 实例序号重复")
        seen_instances.add(instance_key)
        component = expected.get(placement.bom_item_id)
        if component is None:
            issues.append(f"出现未知 BOM item_id: {placement.bom_item_id}")
            continue
        if not 1 <= placement.instance_index <= component.quantity:
            issues.append(f"BOM {placement.bom_item_id} 实例序号超出数量范围")
        if placement.model != component.model or placement.width != component.width or placement.height != component.height:
            issues.append(f"BOM {placement.bom_item_id} 型号或外形与 DesignSpec 不一致")
        rect = placement.rect
        if (
            rect.x < spec.edge_clearance
            or rect.y < spec.edge_clearance
            or rect.right > spec.board_width - spec.edge_clearance
            or rect.top > spec.board_height - spec.edge_clearance
        ):
            issues.append(f"BOM {placement.bom_item_id} 超出安装板有效边界")
        for zone in spec.keepouts:
            if _violates_gap(rect, zone, spec.minimum_gap):
                issues.append(f"BOM {placement.bom_item_id} 与禁布区 {zone.keepout_id} 冲突或间距不足")
        placements.append(placement)

    for item in spec.components:
        if actual_counts.get(item.bom_item_id, 0) != item.quantity:
            issues.append(
                f"BOM {item.bom_item_id} 数量不一致：期望 {item.quantity}，实际 {actual_counts.get(item.bom_item_id, 0)}"
            )
    for i, current in enumerate(placements):
        for other in placements[i + 1:]:
            if _violates_gap(current.rect, other.rect, spec.minimum_gap):
                issues.append(
                    f"BOM {current.bom_item_id}/{other.bom_item_id} 碰撞或间距不足"
                )
    return issues


def _number(value: Decimal) -> str:
    return format(value, "f")


def _line(layer: str, x1: Decimal, y1: Decimal, x2: Decimal, y2: Decimal) -> list[str]:
    return [
        "0", "LINE", "8", layer,
        "10", _number(x1), "20", _number(y1), "30", "0",
        "11", _number(x2), "21", _number(y2), "31", "0",
    ]


def _rectangle_entities(layer: str, rect: Rectangle) -> list[str]:
    x, y, right, top = rect.x, rect.y, rect.right, rect.top
    return (
        _line(layer, x, y, right, y)
        + _line(layer, right, y, right, top)
        + _line(layer, right, top, x, top)
        + _line(layer, x, top, x, y)
    )


def render_dxf(spec: PanelDesignSpec, proposal: LayoutProposal) -> str:
    """写出只含板框、禁布区、元件框和 ASCII 标签的毫米 DXF。"""
    issues = validate_layout(spec, proposal)
    if issues:
        raise LayoutError("拒绝导出未通过几何校验的布局：" + "；".join(issues))
    entities: list[str] = []
    entities.extend(_rectangle_entities("BOARD", Rectangle(
        x=Decimal("0"), y=Decimal("0"), width=spec.board_width, height=spec.board_height,
    )))
    for zone in spec.keepouts:
        entities.extend(_rectangle_entities(
            "KEEPOUT", Rectangle(x=zone.x, y=zone.y, width=zone.width, height=zone.height),
        ))
    for placement in proposal.placements:
        entities.extend(_rectangle_entities("COMPONENT", placement.rect))
        label = f"{placement.bom_item_id}-{placement.instance_index}"
        entities.extend([
            "0", "TEXT", "8", "LABEL",
            "10", _number(placement.x + Decimal("2")),
            "20", _number(placement.y + Decimal("2")),
            "30", "0", "40", "5", "1", label, "50", "0",
        ])
    header = [
        "0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1015",
        "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LTYPE", "70", "1",
        "0", "LTYPE", "2", "CONTINUOUS", "70", "0", "3", "Solid line", "72", "65", "73", "0", "40", "0",
        "0", "ENDTAB",
        "0", "TABLE", "2", "LAYER", "70", "5",
        "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "BOARD", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "LAYER", "2", "KEEPOUT", "70", "0", "62", "1", "6", "CONTINUOUS",
        "0", "LAYER", "2", "COMPONENT", "70", "0", "62", "3", "6", "CONTINUOUS",
        "0", "LAYER", "2", "LABEL", "70", "0", "62", "7", "6", "CONTINUOUS",
        "0", "ENDTAB", "0", "ENDSEC",
        "0", "SECTION", "2", "BLOCKS", "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ]
    return "\n".join(header + entities + ["0", "ENDSEC", "0", "EOF", ""])


def dxf_entity_types(document: str) -> list[str]:
    """检查 DXF 子集的单位、基本区段、图层引用和实体字段。"""
    lines = document.splitlines()
    if len(lines) % 2:
        raise ValueError("DXF group-code/value 行未成对")
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(lines), 2):
        try:
            int(lines[index].strip())
        except ValueError as exc:
            raise ValueError("DXF group code 必须是整数") from exc
        pairs.append((lines[index].strip(), lines[index + 1].strip()))
    if not pairs or pairs[-1] != ("0", "EOF"):
        raise ValueError("DXF 缺少 EOF")
    sections: dict[str, tuple[int, int]] = {}
    index = 0
    while index < len(pairs) - 1:
        if pairs[index] == ("0", "SECTION") and pairs[index + 1][0] == "2":
            name = pairs[index + 1][1]
            end = next((j for j in range(index + 2, len(pairs)) if pairs[j] == ("0", "ENDSEC")), None)
            if end is None:
                raise ValueError(f"DXF {name} 段缺少 ENDSEC")
            sections[name] = (index + 2, end)
            index = end + 1
        else:
            index += 1
    if not {"HEADER", "TABLES", "BLOCKS", "ENTITIES"} <= sections.keys():
        raise ValueError("DXF 缺少必要区段")
    header = pairs[slice(*sections["HEADER"])]
    if ("9", "$ACADVER") not in header or ("1", "AC1015") not in header:
        raise ValueError("DXF 版本头无效")
    if ("9", "$INSUNITS") not in header or ("70", "4") not in header:
        raise ValueError("DXF 未声明毫米单位")

    table_start, table_end = sections["TABLES"]
    layers: set[str] = set()
    active_table = False
    for code, value in pairs[table_start:table_end]:
        if code == "0" and value == "TABLE":
            active_table = False
        elif code == "2" and value == "LAYER":
            active_table = True
        elif active_table and code == "0" and value == "LAYER":
            continue
        elif active_table and code == "2":
            layers.add(value)
        elif code == "0" and value == "ENDTAB":
            active_table = False

    start, end = sections["ENTITIES"]
    types: list[str] = []
    index = start
    while index < end:
        code, entity_type = pairs[index]
        if code != "0" or entity_type not in {"LINE", "TEXT"}:
            raise ValueError(f"不支持的 DXF 实体：{entity_type}")
        next_index = next((j for j in range(index + 1, end) if pairs[j][0] == "0"), end)
        entity_pairs = pairs[index + 1:next_index]
        fields = {field_code: field_value for field_code, field_value in entity_pairs}
        if fields.get("8") not in layers:
            raise ValueError("DXF 实体引用了未定义图层")
        required = {"10", "20", "30", "40", "1"} if entity_type == "TEXT" else {"10", "20", "11", "21"}
        if not required <= fields.keys():
            raise ValueError(f"DXF {entity_type} 实体缺少必要坐标或文本字段")
        numeric_codes = {"10", "20", "11", "21", "30", "31", "40", "50"}
        try:
            for field_code, field_value in entity_pairs:
                if field_code in numeric_codes:
                    number = Decimal(field_value)
                    if not number.is_finite():
                        raise ValueError
        except (ValueError, ArithmeticError) as exc:
            raise ValueError("DXF 实体包含非数字或非有限坐标") from exc
        types.append(entity_type)
        index = next_index
    return types


def validate_dxf_matches_layout(
    spec: PanelDesignSpec, proposal: LayoutProposal, document: str,
) -> list[str]:
    """把 DXF 子集重新读成线段/标签，并与已校验布局逐项比对。"""
    try:
        dxf_entity_types(document)
    except ValueError as exc:
        return [str(exc)]
    lines = document.splitlines()
    pairs = [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines), 2)]
    start = next(
        i + 2 for i in range(len(pairs) - 1)
        if pairs[i] == ("0", "SECTION") and pairs[i + 1] == ("2", "ENTITIES")
    )
    end = next(i for i in range(start, len(pairs)) if pairs[i] == ("0", "ENDSEC"))
    actual_lines: list[tuple[str, Decimal, Decimal, Decimal, Decimal]] = []
    actual_labels: list[tuple[str, Decimal, Decimal]] = []
    index = start
    while index < end:
        entity_type = pairs[index][1]
        next_index = next((j for j in range(index + 1, end) if pairs[j][0] == "0"), end)
        fields = {field_code: field_value for field_code, field_value in pairs[index + 1:next_index]}
        if entity_type == "LINE":
            actual_lines.append((
                fields["8"], Decimal(fields["10"]), Decimal(fields["20"]),
                Decimal(fields["11"]), Decimal(fields["21"]),
            ))
        else:
            actual_labels.append((fields["1"], Decimal(fields["10"]), Decimal(fields["20"])))
        index = next_index

    expected_lines: list[tuple[str, Decimal, Decimal, Decimal, Decimal]] = []
    rectangles = [("BOARD", Rectangle(
        x=Decimal("0"), y=Decimal("0"), width=spec.board_width, height=spec.board_height,
    ))]
    rectangles.extend(("KEEPOUT", Rectangle(
        x=zone.x, y=zone.y, width=zone.width, height=zone.height,
    )) for zone in spec.keepouts)
    rectangles.extend(("COMPONENT", placement.rect) for placement in proposal.placements)
    for layer, rect in rectangles:
        expected_lines.extend([
            (layer, rect.x, rect.y, rect.right, rect.y),
            (layer, rect.right, rect.y, rect.right, rect.top),
            (layer, rect.right, rect.top, rect.x, rect.top),
            (layer, rect.x, rect.top, rect.x, rect.y),
        ])
    expected_labels = [
        (f"{placement.bom_item_id}-{placement.instance_index}",
         placement.x + Decimal("2"), placement.y + Decimal("2"))
        for placement in proposal.placements
    ]
    issues = []
    if Counter(actual_lines) != Counter(expected_lines):
        issues.append("DXF 线段坐标/图层与已校验布局不一致")
    if Counter(actual_labels) != Counter(expected_labels):
        issues.append("DXF 标签内容/坐标与已校验 BOM 布局不一致")
    return issues


def build_artifact_manifest(
    spec_path: Path,
    dxf_path: Path,
    spec: PanelDesignSpec,
    proposal: LayoutProposal,
    document: str,
) -> CadArtifactManifest:
    """从输入快照和已写入的 DXF 建立内容寻址的合成产物清单。"""
    layout_issues = validate_layout(spec, proposal)
    dxf_issues = validate_dxf_matches_layout(spec, proposal, document)
    if layout_issues or dxf_issues:
        raise LayoutError("不能为未通过校验的产物建立清单")
    spec_hash = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    dxf_bytes = dxf_path.read_bytes()
    dxf_hash = hashlib.sha256(dxf_bytes).hexdigest()
    artifact_id = hashlib.sha256(f"{spec_hash}:{dxf_hash}".encode("ascii")).hexdigest()[:24]
    return CadArtifactManifest(
        artifact_id=artifact_id,
        data_class=spec.data_class,
        design_spec_id=spec.design_spec_id,
        design_spec_filename=spec_path.name,
        design_spec_sha256=spec_hash,
        dxf_filename=dxf_path.name,
        dxf_sha256=dxf_hash,
        generated_at=datetime.now(timezone.utc),
        generator_id="deterministic-shelf-v1",
        layout_validation="passed",
        dxf_format_validation="passed",
        dxf_geometry_validation="passed",
    )


def write_artifact_manifest(manifest: CadArtifactManifest, path: Path) -> None:
    """原子写入 sidecar JSON，避免留下半截审计清单。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(manifest.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def verify_artifact_manifest(
    manifest_path: Path, spec_path: Path, dxf_path: Path,
) -> list[str]:
    """比对清单声明的输入/输出文件名和 SHA-256。"""
    try:
        manifest = CadArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        spec_hash = hashlib.sha256(spec_path.read_bytes()).hexdigest()
        dxf_hash = hashlib.sha256(dxf_path.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        return ["产物清单或关联文件无法读取/校验"]
    issues = []
    if manifest.design_spec_filename != spec_path.name or manifest.design_spec_sha256 != spec_hash:
        issues.append("DesignSpec 文件名或 SHA-256 与产物清单不一致")
    if manifest.dxf_filename != dxf_path.name or manifest.dxf_sha256 != dxf_hash:
        issues.append("DXF 文件名或 SHA-256 与产物清单不一致")
    return issues


def resolve_manifest_path(spec_path: Path, dxf_path: Path, requested: Path | None = None) -> Path:
    """阻止清单或 DXF 覆盖输入规格及彼此覆盖。"""
    manifest_path = requested or dxf_path.with_name(f"{dxf_path.stem}.manifest.json")
    resolved = [spec_path.resolve(), dxf_path.resolve(), manifest_path.resolve()]
    if len(set(resolved)) != len(resolved):
        raise ValueError("DesignSpec、DXF 和产物清单必须使用不同文件路径")
    return manifest_path


def load_spec(path: Path) -> PanelDesignSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return PanelDesignSpec.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("DesignSpec 读取或验证失败") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="生成合成示例二维安装板 DXF（仅用于演示）")
    parser.add_argument("--spec", type=Path, default=SYNTHETIC_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        manifest_path = resolve_manifest_path(args.spec, args.output, args.manifest)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    spec = load_spec(args.spec)
    proposal = generate_layout(spec)
    issues = validate_layout(spec, proposal)
    if issues:
        raise SystemExit("布局校验失败：" + "；".join(issues))
    document = render_dxf(spec, proposal)
    entity_types = dxf_entity_types(document)
    dxf_issues = validate_dxf_matches_layout(spec, proposal, document)
    if dxf_issues:
        raise SystemExit("DXF 与布局不一致：" + "；".join(dxf_issues))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding="ascii")
    manifest = build_artifact_manifest(args.spec, args.output, spec, proposal, document)
    write_artifact_manifest(manifest, manifest_path)
    manifest_issues = verify_artifact_manifest(manifest_path, args.spec, args.output)
    if manifest_issues:
        raise SystemExit("产物追溯校验失败：" + "；".join(manifest_issues))
    print(json.dumps({
        "status": "synthetic_draft_for_review",
        "design_spec_id": spec.design_spec_id,
        "placements": len(proposal.placements),
        "dxf_entities": len(entity_types),
        "output": str(args.output),
        "manifest": str(manifest_path),
        "artifact_id": manifest.artifact_id,
        "engineering_use_approved": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
