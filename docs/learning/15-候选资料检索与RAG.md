# 15 官方施耐德产品资料检索与 RAG

## 业务目标：为什么先查资料

用户问“LC1D09M7 线圈电压是多少”时，模型记忆可能把相近型号混淆。先按完整型号查已核对资料，再把片段交给模型，并把原始链接给用户。简单说，**RAG 是先查资料、再回答**；专业定义是检索增强生成，即用检索结果作为生成模型的外部上下文。当前资料是 6 个真实施耐德型号的官方事实摘录，不是完整产品目录或供应商报价。

## 核心技术及为何这样设计

**必须掌握：** `data_sources/schneider_products.json` 是人工核对的短事实清单；Pydantic `ProductFact` 校验每条结构；`search_knowledge()` 用可解释的词项检索并加型号、电流、电压、极数约束；`KnowledgeHit` 同时携带事实和来源。资料只有六条，先让“型号正确、证据可查、没有时承认未知”可测试，再决定是否需要向量数据库。**可以暂缓：** embeddings、向量索引、重排模型和实时爬虫；增加它们也不能把官网产品参数变成核准采购价。

## 与其他模块的关系和数据流

`data_sources/schneider_products.json` → `app/knowledge.py::_products()` 读取和校验 → `search_knowledge()` 找相关产品 → `app/assistant.py::answer_question()` 把最多两条命中片段放入模型初始请求 → `ChatResponse.sources` → `static/index.html::appendSources()` 显示产品链接及核对日期。模型还可调用教程 16 的只读工具进一步按型号查找或比较。报价流程 `app/agent.py`、金额 `app/pricing.py` **不读取知识清单**；供应商价来自另一条受控数据链。

## 相关文件与核心代码逐段解释

1. `data_sources/schneider_products.json` 对 A9F77216/A9F77232、LC1D09M7/LC1D09BD、XB4BA31/XB4BA42 分别记录完整型号、系列、简短参数、官方 URL 和 `checked_on`。不同地区页面只证明所录产品事实，不能证明在中国在售或能够替换。
2. `_products()` 用 `json.loads()` 和 `ProductFact.model_validate()` 建立 Python 对象，再检查型号不重复、事实非空、链接是允许的施耐德 HTTPS 域名且路径含同一型号。如果来源填成陌生网站，会抛错误，阻止污染模型上下文。
3. `_terms()` 把“32 A”与“32A”统一，再抽取英文数字词和中文双字词。`search_knowledge()` 若发现用户写了未收录的完整型号，直接返回空列表；无论是否写出型号，都会用 `_electrical_tokens()` 限制 2P/3P、16A/32A、24V/220V 等参数。最后排序并最多返回三条。这个过滤避免把 16A 型号当成 32A 答案，也会拒绝“A9F77216 32A”这类自相矛盾的查询。
4. `get_product_knowledge()` 只接受完整型号，未知型号返回 `None`；`compare_product_models()` 对两个已收录型号逐字段列出相同与不同的事实。缺失字段是未知，不补造，不判断可替代。
5. `answer_question()` 初次只放两条命中；后续工具结果和 `sources` 仍来自代码返回的 `KnowledgeHit`，不是由模型自报一个链接。**模型最终文字仍可能出错**，用户要查看来源并经技术人员确认。

## 必须掌握的 Python 基础

`Path` 表示文件位置；`json.loads()` 把 JSON 文字转成字典/列表；`dict[str, str]` 保存“字段名→事实”；`set` 去重型号和检索词；列表推导式和 `sorted(..., key=...)` 形成排序结果。`None` 代表未知，和“0”“不适用”不同。先能沿着一个型号从 JSON 找到 `KnowledgeHit`，再研究检索算法。

## 实际运行与测试

在项目根目录运行：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chat.py
.venv/bin/python -c 'from app.knowledge import search_knowledge; print(search_knowledge("2P 32A C曲线断路器"))'
```

第二条应只返回 `product:A9F77232`；`LC1D09M8 接触器` 应返回空。`tests/test_chat.py` 还验证六个型号、相近线圈电压、陌生来源拒绝和型号比较。配置模型后可在页面问“LC1D09M7 和 LC1D09BD 线圈电压有何不同？”，链接会显示核对日期；真实模型回答仍需人工检查。

## 常见错误与排查

- 明确型号却无命中：先检查拼写，再在 JSON 中查完整型号。**不要**把 LC1D09M8 自动替换为 LC1D09M7。
- 问“32A”却出现 16A：检查 `_electrical_tokens()` 与事实字段的单位写法；回归测试应拦截。
- 官方 PDF 或地区产品页打不开：可用性随地区变化，保留型号/URL 并人工换成同型号的官方可核对来源，不使用第三方无出处摘要顶替。
- 问采购价没有答案：知识库没有人民币供应商价格；需采购从有效报价凭证进入结构化价格库。

## 面试可能问

**为什么这也算 RAG，没用向量库？** RAG 的关键是“检索结果进入生成上下文”，索引实现可先用小规模词项检索；这里还加了安全的型号和电气参数过滤。**如何评估？** 用冻结问题集测 Top-1、Recall@k、错误型号命中和无答案拒答；目前只有合成回归案例，真实问题集未到位。**官方产品页能直接给 CAD/报价吗？** 不能，柜体安装需工程核对，成本需采购凭证。

## 小练习

1. 手动比较 JSON 中 LC1D09M7 与 LC1D09BD 的“线圈电压”，预测 `compare_product_models()` 的 `differences`，再运行测试核对。
2. 输入 `A9F77299 断路器` 和 `2P 16A 断路器`，说明为何前者无结果、后者可命中；尝试在不改变事实数据的情况下解释代码路径。
