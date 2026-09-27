# BoundedRSI — Surface / Task / Scoring Pilot

把“定义 surface、任务与评分；做少量 workspace”落实成可运行的研究起点。
第一阶段验证：信息在哪一层丢失、人工修复能否解决、是否伤害其他任务。

**当前是离线、确定性的流水线诊断器，不是完整自主 RSI，也不是 LLM Agent benchmark 成绩。**
没有调用外部 API、没有模型训练、没有自动生成代码补丁、没有付费运行。

## 直接运行

Python 3.9+；核心实验只使用标准库，无需下载模型或配置 API Key。

```bash
cd /Users/shichenxi/vscode/BoundedRSI
python3 -m venv .venv
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m boundedrsi pilot --out artifacts/my-first-run
```

已运行的结果在 `artifacts/pilot/report.md`。再次运行请选择新的输出目录，程序不会覆盖已有实验。
首次 pilot 会生成 `data/pilot/`；目录存在时直接使用现有数据。
所有以下命令均在项目根目录执行。`python3` 可替换为 `.venv/bin/python`。

VS Code：`终端 → 运行任务 → BoundedRSI: Run pilot`；测试任务为 `BoundedRSI: Tests`。
F5 配置为 `BoundedRSI: Debug pilot`，需要已安装 Python Debugger 扩展。
本地 `.venv` 已配置，项目迁移到其他机器时重新创建即可。

## 项目结构

```text
boundedrsi/
  generate.py       # 事实 → 原始文件、公开任务、评分答案
  surface.py        # MD/TXT/CSV/JSON 解析、元数据、证据映射、关系
  retrieval.py      # BM25、有效时间过滤、关系扩展
  executor.py       # 固定 typed-query 诊断运行器，不读取 gold
  scoring.py        # 答案、来源/版本、证据、拒答与检索评分
  evaluation.py     # 分割、快照、执行、评分、配对回归检查
  cli.py            # 一键实验和独立组件命令
policies/           # 3 种人工修复及组合、完整参考、负对照
data/pilot/
  manifest.json     # workspace/family/split
  workspaces/       # Agent 可读取的原始文档与公开目录
  tasks/            # 公开问题及结构化 query（没有答案）
  gold/             # evaluator-only 答案与必要证据；不供 Agent 读取
artifacts/pilot/
  report.md         # 全策略、全切片对比
  comparisons.json  # 修复、回归任务 ID 和经验性 gate
  <policy>/         # surface、预测、评分、运行快照
tests/              # 评分、时间边界、数据隔离、完整流程测试
docs/               # 设计、实现计划、研究协议
```

## 数据与任务

10 个可重复生成的合成 workspace，每个 12 题，总计 120 题。
每个 workspace 包含手册、目录 JSON、预算 CSV、两个有效期不同的政策版本、
低权威草案、订单和发票。字段名、实体、数值随 workspace 变化。
六类任务各两题，均要求基于 workspace 资料回答问题并提供可追溯证据；信息不足时应拒答。

| 类型 | 具体做什么 | 项目中的例子 |
|---|---|---|
| 单文件查询 `single_file` | 从单个文件读取字段 | 手册里的发货日期是什么？目录中的联系人是谁？ |
| 表格查询 `table_query` | 根据条件定位表格行、读取值 | Orchid Research 的预算是多少？ |
| 版本查询 `version_query` | 查指定日期生效的版本 | 2026-02-01 生效的住宿限额是多少？ |
| 冲突证据 `conflicting_evidence` | 根据来源权威性和有效期选择证据 | 按正式政策而非草案，2026-03-31 的餐费限额是多少？ |
| 跨文件关联 `cross_file_join` | 关联订单和发票，再计算差额 | ORC-01 的发票金额减去订单金额是多少？ |
| 信息不足 `insufficient_info` | 资料缺失时拒答 | 手册未提供的紧急电话是什么？Unknown Team 的预算是多少？ |

以上例子译自 [ws_01 任务文件](data/pilot/tasks/ws_01.jsonl)。

分割按 workspace 家族：6 development、2 validation、2 locked_holdout。
默认只评价 96 道开发/验证题。全部数据仍由同一个生成器产生，不能据此宣称真实分布泛化。
没有复制参考对话里的示例百分比；所有报告由程序实际计算。

问题同时有自然语言 `question` 和公开的结构化 `query`。离线运行器解释 query，
不从自然语言推理；查询字段/操作并非 gold 标签，但给出的任务结构显著简化了解题。
导出给外部 LLM 的 contexts 只有自然语言问题和检索内容，不包含 query 或答案。

## Surface 定义

`S = B_policy(W)`，包含 chunks/records、metadata、BM25 index、links、registry。
registry 将 evidence ID 映射回原文；ID 由文件相对路径、文件内容 SHA256、定位符生成。
CSV `row:1` 表示第一条数据行（不含表头），MD `line:2` 是原始第 2 行，
JSON 使用 `/0` 等指针，PDF 使用 `page:1`。

`workspace.json` 是公开来源目录，声明 collection、authority、version、valid_from、valid_to。
这些来自显式输入；当前不声称能自动从任意文档推断来源权威或有效日期。
valid_from 包含边界，valid_to 不包含边界。

| 策略 | 改动 | 解释限制 |
|---|---|---|
| baseline | CSV 只保留展平值，丢弃时间元数据与关系 | 故意注入的弱诊断基线 |
| table_rows | 保留表头、行记录和行证据 | 人工修复 1 |
| temporal | 保留有效期供固定检索器过滤 | 人工修复 2 |
| relations | 按同名非空 `*_id` 建跨文件边并扩展检索 | 人工修复 3，依赖 table_rows |
| combined | 合并三种修复 | 非自动学习结果 |
| structured_reference | 起点即保留完整结构 | 与 combined 等价的参考，非理论上限 |
| latest_only_control | 从完整 surface 丢弃历史记录 | 人为负对照，检验历史任务回归 |

检索器先按 collection 和有效期筛选，以公开 authority、BM25、版本排序。
关系扩展计入总 k 预算，不额外偷偷提供 gold 证据。运行器只根据已检索记录查询/计算。
当前关系推断基于同名 ID，无法解决语义别名、歧义 ID 或复杂关系推断。

## 评分约定

主指标 `success = answer_correct × version_correct × evidence_correct`。

- 答案：字符串忽略首尾空格与大小写；数值容许 1e-9 绝对误差；布尔与数值区分。
- 版本/来源：实际引用来源集合必须符合 gold 所需来源。无版本任务也检查正确来源。
- 证据：引用必须存在于原文 registry、曾出现在实际检索结果中，并完整匹配一个 gold evidence set。
  多余但无支持的引用也失败；join 缺少任一参与记录也失败。
- Recall@k：必要证据 ID 的召回比例；多证据任务与 single-hit 不混为一谈。
  同时输出 any_hit_at_k 与 complete_evidence_at_k。
- 无答案题：必须 `abstained=true`、`answer=null`、无引用。版本、证据、召回为 N/A，
  不作为成功值加入分母。这是受控闭世界缺失任务，不证明任意开放语料的无答案性。

`summary.json` 为各指标保留 `*_n` 分母；同时报告 workspace 宏平均。
失败分类只区分检索/表示未覆盖与回答/证据选择阶段，不构成严格的因果归因。
近义回答、单位换算、自由文本蕴含及部分正确答案尚无通用评分器。

## 独立组件命令

```bash
# 生成新的独立数据目录；不会覆盖现有目录
python3 -m boundedrsi generate --out data/pilot-seed42 --seed 42

# 构建一个 surface（不需要 tasks 或 gold）
python3 -m boundedrsi build --workspace data/pilot/workspaces/ws_01 \
  --policy policies/combined.json --out artifacts/orchid-surface.json

# 检查日期过滤和证据定位；会校验原始文件是否已变化
python3 -m boundedrsi search --surface artifacts/orchid-surface.json \
  --workspace data/pilot/workspaces/ws_01 --collection policy \
  --as-of 2026-02-01 --question 'What hotel_limit is effective?' --k 5

# 单独评价一种策略
python3 -m boundedrsi evaluate --policy policies/temporal.json \
  --splits validation --out artifacts/temporal-validation

# 查看人为负对照相对完整策略的回归；不会执行部署
python3 -m boundedrsi compare --baseline artifacts/pilot/combined \
  --candidate artifacts/pilot/latest_only_control --out artifacts/regression-check.json
```

compare 要求输入数据指纹、task ID、split、k 和 engine 一致。
经验性 gate 检查整体正收益、pass→fail 比例、每个 split/任务切片退化。
默认阈值 5% 回归、2 个百分点切片退化，仅为可配置诊断值，不是统计保证。
当前没有生产发布器、自动回滚、成本约束搜索或多轮候选生成器；这些属于下一阶段。

## 接入真实 LLM 或其他 Agent

```bash
python3 -m boundedrsi contexts --policy policies/combined.json \
  --splits validation --out artifacts/validation-contexts.jsonl
```

固定模型版本、系统提示、采样参数与 token 预算，将每行 question/passages 提供给 Agent。
只给 Agent contexts 文件；不要给原始项目根目录或 `gold/` 的访问权限。
要求输出 JSONL：

```json
{"task_id":"ws_07_q01","answer":"2026-11-16","abstained":false,"citations":["替换为该题提供的真实 evidence_id"]}
```

上面只是格式示例，证据 ID 不能照抄。无答案用 null / true / []。
外部预测必须覆盖所选任务且无重复 ID。导入时程序根据固定策略重新计算检索上下文，
不会信任外部输出自称的 retrieved 字段，因此不要在导出和评分之间修改数据、策略或 k。

```bash
python3 -m boundedrsi evaluate --policy policies/combined.json --splits validation \
  --predictions artifacts/model-predictions.jsonl --out artifacts/model-validation
```

外部运行的 token/API 成本与模型延迟本工具无法获知，标为未知，不记为零。
脚本可验证 JSON、数值与引用来源，不能替代人工审计自由文本回答。

## Locked holdout 与可复现性

最终设计及模型配置冻结后，才显式执行：

```bash
python3 -m boundedrsi evaluate --policy policies/combined.json \
  --splits locked_holdout --include-holdout --out artifacts/final-holdout
```

这一步会接触最终测试，不应成为日常调试任务。每次运行保存策略、数据、gold、任务指纹、
Python 版本及运行器标识。含 holdout 的比较只能用于报告，gate 不允许给出接纳结论。
“locked”是本地流程约定而非权限隔离；正式研究应让独立评测进程控制访问。

## 可选 PDF 支持

```bash
.venv/bin/python -m pip install '.[pdf]'
```

仅支持可提取文本的 PDF，按页建立文本证据；扫描页会明确报错。
本 pilot 的表格修复使用 CSV，不把“按页提取 PDF 文本”冒充跨页表格表头恢复。
真实 PDF 的表格结构与跨页处理应作为下一阶段独立任务扩展和验证。

## 下一阶段研究

1. 人工核查 3 类失败和负对照中的 16 条回归；确认诊断符合原始文档。
2. 加入真正不同文档模板、真实公开文件及新实体，避免生成器模板过拟合。
3. 用固定真实 Agent 运行基线、三个修复和结构完整参考；统计逐题变化与完整成本。
4. 若出现可迁移的人工修复和自然回归，再加入受限候选生成、独立发布验证、快照回滚。

参考需求：[定义Surface与评分](chatgpt-conversation://6ab926d7-ea40-83ec-b714-8dc159adcdd3)。
