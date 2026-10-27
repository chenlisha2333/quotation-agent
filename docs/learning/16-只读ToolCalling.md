# 16 只读 Tool Calling：查任务、查型号与比较

## 业务目标：为什么模型需要工具

报价任务状态会随补价和审批变化，模型靠聊天记录可能说错；产品型号相近时，模型凭记忆可能把 220 V AC 线圈说成 24 V DC。让它**请求后端查询**，可以得到当前且有来源的数据。简单说，Tool Calling 是“模型提出调用哪个工具，后端决定是否执行”；专业定义是模型输出结构化函数调用请求，应用验证权限和参数、执行函数，再将结果交回模型。模型从未直接执行 Python 代码。

## 核心技术及为何选择只读白名单

**必须掌握：** `app/assistant.py::READ_ONLY_TOOLS` 向 Responses API 声明四个函数：`get_current_task_summary`、`search_product_knowledge`、`get_product_details`、`compare_product_models`。`_run_tool()` 再做真正的服务端白名单和参数检查，最多两次工具调用；`parallel_tool_calls=False`。模型没有补价、审批、发布、任意 URL 或文件路径工具。**可以暂缓：** 多工具并行、写入型操作、自治循环和长期记忆；报价的关键动作仍由用户和确定性代码完成。

## 与其他模块的关系和数据流

页面 `sendChat()` → `POST /assistant/chat` → `app/main.py::chat_with_assistant()` 调用 `authorize()`；若关联任务，先用 `_task_response()` 生成当前角色可见摘要（最多 30 行）。`answer_question()` 初次发送问题与官方产品检索片段。模型返回 `function_call` 后，`_run_tool()` 校验函数名和 JSON 参数：任务工具读取已过滤摘要；另外三个工具读取 `app/knowledge.py` 的官方事实清单。结果按原 `call_id` 作为 `function_call_output` 续接，模型再回答。整个分支不调用 `save_task_state()`、`approve_quote()` 或导出函数。

## 相关文件与核心代码逐段解释

1. `READ_ONLY_TOOLS` 的每个 JSON Schema 有 `strict=True` 和 `additionalProperties=False`。任务工具没有 `task_id` 参数，因为要绑定当前已认证请求；搜索仅收 `query`，详情仅收 `model`，比较仅收 `left_model/right_model`。
2. `_run_tool()` 先检查 `call_id`、参数 JSON 大小和形状。`get_current_task_summary` 只返回 API 传入的角色过滤摘要，没有关联任务就返回错误对象，不搜索其他任务。
3. 产品搜索把查询限制为 1–160 字；精确详情只按完整型号返回来源；比较工具调用 `compare_product_models()`，返回逐字段差异和两个真实来源。未知型号返回“未收录”，不会调用相近型号替代。所有工具都不返回供应商价格。
4. `_post_response()` 处理模型超时、HTTP/JSON 错误，不把 API Key 写进响应。`answer_question()` 最多三轮模型请求，其中最多两轮是工具调用；未知 `approve_quote` 等工具名会得到 502，不会动态执行同名 Python 函数。
5. `tests/test_chat.py` 用 `httpx.MockTransport` 模拟模型请求，检查技术角色的成本字段为空、任务版本不变、详情/比较有来源、未知工具与超限调用被拒绝。模拟协议正确不代表真实模型总能选对工具。

## 必须掌握的 Python 基础

`dict` 是 JSON 对象在 Python 中的常见形式；`json.loads()` 只解析数据，**不执行代码**。`if/elif/else` 是白名单分派；`set(arguments) == {"model"}` 检查键名恰好符合约定；`raise` 立即拒绝不合法调用。`async def`/`await` 等待模型网络请求，`asyncio.to_thread()` 把本地文件检索放到工作线程。先能解释一次 `function_call → function_call_output`，再考虑异步细节。

## 实际运行与测试

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_chat.py
```

先读 `test_chat_official_product_tools_return_sourced_read_only_results()`：模拟模型先要求 `get_product_details` 或 `compare_product_models`，后端执行后再回传结果。配置可用的 `OPENAI_API_KEY` 和 `OPENAI_MODEL` 后，在页面问“LC1D09M7 与 LC1D09BD 线圈电压不同吗？”；再问当前任务状态。真实模型选择、引用和地区产品资料适用性须人工核查。

## 常见错误与排查

- 503：模型环境变量没有进入服务进程；本机启动不会自动加载 `.env`。
- 502“未授权的工具”：模型提出白名单外函数。检查请求和权限设计，不能为了消除错误随意加入写工具。
- 502“参数无效”：检查模型给的 JSON、键名、长度。工具声明限制和 `_run_tool()` 后端校验必须同时存在。
- 详情返回 `null`：该完整型号未收录；先核对官方资料，不猜型号。
- 回答与任务明细或来源冲突：以服务端任务/官方资料为核查起点，最终选型与金额交相应业务人员确认。

## 面试可能问

**谁真正执行工具？** 后端；模型只是提出请求。**为什么还要在代码中校验？** JSON Schema 和提示词不能替代服务器权限边界。**为什么任务工具没有任务 ID 参数？** 防止模型越权读其他任务。**比较工具是否代表自动推荐替代产品？** 不代表，它只返回已收录事实差异，并未验证电气兼容或采购条件。

## 小练习

1. 在测试中找到 `compare_product_models` 的模拟 `function_call` 和返回的 `function_call_output`，画出两轮请求中 `call_id` 如何对应。
2. 在临时分支让模拟模型请求 `set_price`，预测 HTTP 状态和任务版本是否变化，再运行相关测试验证。
