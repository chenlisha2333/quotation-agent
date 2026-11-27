"""将 cad-agent DrawingSpec 矩形实体安全映射为本项目的布局提案。

本模块不导入或执行第三方代码，也不信任第三方 DXF。只读候选 JSON 中
约定命名的 rectangle 实体，随后必须通过本项目独立的几何/BOM 校验。
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.cad2d import LayoutError, LayoutProposal, PanelDesignSpec, Placement, validate_layout


_MAX_DRAWING_ENTITIES = 500
_PLACEMENT_PREFIX = "placement:"


def drawing_spec_to_layout(
    spec: PanelDesignSpec,
    drawing_spec: dict[str, Any],
) -> LayoutProposal:
    """把 cad-agent DrawingSpec JSON 的 rectangle 实体转换为合成布局提案。

    组件实体 ID 必须严格采用 `placement:{bom_item_id}:{instance_index}`。
    坐标单位必须是毫米；方向旋转暂不支持，宽高需与受控元件外形完全一致。
    板框、标注、标题栏等实体不会进入结果，也不用于绕过本项目校验器。
    """
    if spec.data_class != "synthetic":
        raise LayoutError("CAD 候选适配器当前只接受合成数据")
    if not isinstance(drawing_spec, dict):
        raise LayoutError("DrawingSpec 必须是 JSON 对象")
    if drawing_spec.get("units", "mm") != "mm":
        raise LayoutError("DrawingSpec 单位必须明确为 mm")
    entities = drawing_spec.get("entities")
    if not isinstance(entities, list) or len(entities) > _MAX_DRAWING_ENTITIES:
        raise LayoutError("DrawingSpec entities 缺失或超过实体数量上限")

    expected: dict[str, tuple[Any, int]] = {}
    for component in spec.components:
        for instance_index in range(1, component.quantity + 1):
            expected[f"{_PLACEMENT_PREFIX}{component.bom_item_id}:{instance_index}"] = (
                component,
                instance_index,
            )

    placements: list[Placement] = []
    seen_ids: set[str] = set()
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("kind") != "rectangle":
            continue
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id.startswith(_PLACEMENT_PREFIX):
            continue
        if entity_id in seen_ids:
            raise LayoutError(f"DrawingSpec placement ID 重复：{entity_id}")
        seen_ids.add(entity_id)
        binding = expected.get(entity_id)
        if binding is None:
            raise LayoutError(f"DrawingSpec 包含未登记的 BOM placement ID：{entity_id}")

        component, instance_index = binding
        try:
            corner = entity["corner"]
            if not isinstance(corner, (list, tuple)) or len(corner) != 2:
                raise ValueError("corner 必须有两个坐标")
            x, y = (Decimal(str(value)) for value in corner)
            width = Decimal(str(entity["width"]))
            height = Decimal(str(entity["height"]))
            if not all(value.is_finite() for value in (x, y, width, height)):
                raise ValueError("坐标和外形必须是有限数值")
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise LayoutError(f"DrawingSpec placement 几何无效：{entity_id}") from exc

        try:
            placements.append(Placement(
                bom_item_id=component.bom_item_id,
                instance_index=instance_index,
                model=component.model,
                x=x,
                y=y,
                width=width,
                height=height,
            ))
        except ValidationError as exc:
            raise LayoutError(f"DrawingSpec placement 数值超出允许范围：{entity_id}") from exc

    proposal = LayoutProposal(design_spec_id=spec.design_spec_id, placements=placements)
    issues = validate_layout(spec, proposal)
    if issues:
        raise LayoutError("DrawingSpec 未通过独立布局校验：" + "；".join(issues))
    return proposal
