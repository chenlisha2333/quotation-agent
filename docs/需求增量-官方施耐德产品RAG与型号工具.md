# 需求增量：官方施耐德产品资料 RAG 与型号工具

版本：v0.5.0；日期：2026-10-22。承接 [PRD V3.1](PRD.md) 的 F09/F10 和旧版[三款示例检索增量](需求增量-RAG与只读工具调用.md)。

## 业务目标

用户可问“LC1D09M7 的线圈电压和尺寸？”、“LC1D09M7 与 LC1D09BD 差在哪里？”；系统先查经人工核对的施耐德官方产品资料，再让模型解释并展示来源。完整型号未收录、技术参数缺失或来源未证实时明确说未知。工具只读，不查实时库存/供应商人民币价格，也不批准技术选型或修改报价。

## 当前交付与验收

| 编号 | 条件 | 代码与测试 |
|---|---|
| KR-01 | `data_sources/schneider_products.json` 收录 6 个真实型号：A9F77216、A9F77232、LC1D09M7、LC1D09BD、XB4BA31、XB4BA42；每条有官方链接和核对日期 | `app/knowledge.py::_products()` 校验型号唯一、HTTPS 官方域名、链接包含型号；`tests/test_chat.py` |
| KR-02 | 问题含完整未收录型号时不推荐相似型号；无完整型号时，2P/3P、16A/32A、24V/220V 等数值硬约束不混淆 | `search_knowledge()` 检索与约束测试 |
| KR-03 | 可按完整型号返回事实；可确定性比较两个型号的相同字段与差异，缺失属性显示为未知，不推断可替代 | `get_product_knowledge()`、`compare_product_models()` 及测试 |
| TC-01 | 模型可选 4 个只读工具：任务摘要、产品搜索、精确型号详情、两个型号比较；名称、参数和调用次数均由后端控制 | `app/assistant.py::READ_ONLY_TOOLS/_run_tool()`、模拟 Responses API 测试 |
| TC-02 | 模型不能传任意任务 ID、URL 或文件路径，不能补价/审批/导出；技术角色的成本不进入工具输出 | 越权工具、角色过滤与任务版本不变测试 |
| UI-01 | 页面显示来源 ID、产品标题、官方链接和资料核对日期 | `static/index.html::appendSources()` |

## 数据来源和边界

知识清单只保留简短事实摘录和链接，不缓存官方网页全文。以下六条链接在 2026-10-22 按型号/标题/所录参数核对；页面或地区可能变化，运行时不会联网更新：

| 型号 | 收录重点 | 官方资料 |
|---|---|---|
| A9F77216 | Acti9 iC60N、2P、C 曲线、16 A | [施耐德产品页](https://www.se.com/eg/en/product/A9F77216/miniature-circuitbreaker-acti9-ic60n-2p-16-a-c-curve-6000-a-iec-608981-10-ka-iec-609472/) |
| A9F77232 | Acti9 iC60N、2P、C 曲线、32 A | [施耐德产品页](https://www.se.com/fr/fr/product/A9F77232/acti9-ic60n-disjoncteur-2p-32a-courbe-c-400vca-6000a-10ka/) |
| LC1D09M7 | TeSys Deca、AC-3 9 A、220 V AC 线圈；外形尺寸 | [施耐德数据表](https://iportal.se.com/Contents/docs/SQD-LC1D09M7.PDF) |
| LC1D09BD | TeSys Deca、9 A、24 V DC 线圈 | [施耐德产品页](https://www.se.com/us/en/product/LC1D09BD/iec-contactor-tesys-deca-nonreversing-9a-5hp-at-480vac-up-to-100ka-sccr-3-phase-3-no-24vdc-coil-open-style/) |
| XB4BA31 | Harmony XB4、绿色、1 NO；外形尺寸 | [施耐德产品页](https://eshop.se.com/sg/push-button-metal-harmony-xb4-flush-green-22mm-spring-return-unmarked-1no-xb4ba31.html) |
| XB4BA42 | Harmony XB4、红色、1 NC | [施耐德产品页](https://www.se.com/us/en/product/XB4BA42/harmony-22mm-push-button-red-flush-spring-return-1-nc-unmarked/) |

这些是不同地区的施耐德资料；不证明中国市场在售、认证适配、交期、柜内安装可行或供应商采购成本。资料页可能展示外币零售价，系统**不采集、不换算、不用于 `app/pricing.py`**。例如 LC1D09M7 的 220 V AC 与 LC1D09BD 的 24 V DC 线圈差异可帮助技术核对，但不能由模型宣布可替代。

## 数据流与失败处理

`本地事实清单 → Pydantic/官方来源校验 → 词项和电气参数约束检索 → 最多两条初始片段 → Responses API → 白名单只读工具（最多两次） → 带来源的回答`。知识文件错误会中止问答，不静默回退到未经核对的型号。未知型号的详情工具返回未收录；比较工具要求两个不同的已收录型号。模型或网络故障只影响问答，报价链独立运行。

## 未完成与后续验收

当前仍是 6 条人工整理的公开产品事实，未取得企业许可的完整目录、技术确认记录、供应商人民币价或真实用户问题集。自动测试验证检索边界和模拟工具协议，尚未验证真实模型回答的准确率、来源引用和提示注入抵抗。取得授权数据后冻结业务问题集，报告 Top-1/Recall@k、错误引用、无答案拒答、越权工具拒绝和人工修正量；根据数据规模决定是否需要向量索引。用户的 Excel 清单**不会自动成为知识库**。
