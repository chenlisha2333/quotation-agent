# cad-agent 适配边界：DrawingSpec 矩形提案转换

更新日期：2026-11-22。本文记录固定候选版本的静态源码核查，以及本项目如何把候选格式中的矩形实体转换为自己的布局提案。它不是 cad-agent 模型调用或 CAD 软件验收报告。

## 为什么要做格式适配

候选仓库 [Youssef-RAPCorp/cad-agent](https://github.com/Youssef-RAPCorp/cad-agent) 的固定版本为 `f5e3fec5629347f8e9c6e9b2fc3dd0a607e2eeb4`。其 vendored `DrawingSpec` 支持 `Rectangle` 等二维图元，字段包括 entity ID、左下角、宽和高。该 schema 没有电气柜 BOM、元器件外形来源、禁布区或间距等业务概念。候选自身的绘图 validator 主要检查退化几何和标注重叠，不能代替本项目的几何规则。

因此本项目只接受符合约定的 JSON 矩形，并立即转换为 `LayoutProposal`。任何候选结果仍需本项目校验器重新核对 BOM、型号、尺寸、边界、禁布区、碰撞与间距。它不会直接读取或采信候选 DXF。

## 数据约定

每个元件实例的 DrawingSpec 矩形 ID 必须是：

```text
placement:{bom_item_id}:{instance_index}
```

例如：`placement:SYNTH-BOM-01:1`。DrawingSpec 的 `units` 必须是 `mm`，`corner` 为矩形左下角，`width` 与 `height` 必须和受控尺寸一致。当前接口不支持旋转，也不接受非矩形元件外形。

## 转换流程

`app/cad_agent_adapter.py::drawing_spec_to_layout()` 执行以下步骤：

1. 拒绝非合成数据、缺少 entities、实体数量超限或非毫米单位。
2. 根据输入 `PanelDesignSpec` 枚举预期 BOM 实例 ID。
3. 只解析标记为 `kind="rectangle"` 且 ID 符合 `placement:` 前缀的实体；板框、标注和其他图元不进入报价/布局数据。
4. 拒绝重复或未知实例 ID、非有限坐标和非法尺寸。
5. 生成 `LayoutProposal`，交给 `app/cad2d.py::validate_layout()` 重验。
6. 只有所有 BOM 数量、型号、外形、安装板有效边界、禁布区、碰撞和最小间距都通过，才返回提案；否则抛出 `LayoutError`。

## 当前测试能证明什么

`tests/test_cad_agent_adapter.py` 使用手工构造、符合候选 schema 结构的合成 JSON，覆盖有效转换、单位错误、缺失实例、未知/重复 ID、尺寸变化、禁布区冲突、无效几何和实体上限。当前适配测试 5 项通过，与 CAD 基线测试合计 16 项通过；项目全量测试 125 项通过。

测试只证明本项目转换器与确定性 validator 的行为。没有安装候选依赖、调用 Gemini/Claude、运行候选生成器或测试真实模型输出，所以不能据此说 Text-to-CAD 已接入或候选能稳定生成正确布局。

## 安全与剩余工作

候选的主要 3D 代码生成路径会在 Python 进程内对模型生成的代码调用 `exec`，并通过受限 builtins/import 名称过滤做沙箱处理。静态源码没有展示操作系统级网络/文件隔离和资源配额；该路径不可在本项目 Web/API 进程启用。候选 package 元数据标出 MIT，但固定源码树没有独立 LICENSE 文件；依赖许可还没有审查。项目运行 Python 3.13.12，候选分类器列出到 Python 3.12，兼容性仍待实测。

下一步只有在准备好独立临时环境、审查许可及依赖后，才用固定 commit 做合成模型调用；记录每个样例原始 DrawingSpec、模型/提示版本、独立校验错误、生成和重试结果。若模型输出没遵守实例 ID/尺寸，或无法可靠通过独立校验，就拒绝候选，保留确定性基线。所有通过的 DXF 仍是待工程师审核的初稿。
