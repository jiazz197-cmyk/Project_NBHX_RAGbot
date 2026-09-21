# Excel 读写口径统一（issue #23）

> 关联 issue：[#23 Excel 读写两端口径不一致，表头假设相互矛盾](http://10.80.153.12/Carl_Jia/ragchatbot/-/work_items/23)
> 落地分支：`develop`（提交 `50ff4d8` 代码 + 本文件）
> 存量重灌：2026-09-21 已在 dev 环境执行完毕（见 §5），重灌后对账 `EXIT=0`

## 1. 问题：同一份 Excel，写入端与读取端各按一套表头假设解析

| 路径 | 旧假设 | 旧实现位置 |
|---|---|---|
| 写入端（chunk 化） | 第 0 行即表头，`row[1:]` 即数据 | `ExcelParser.__call__`（`app/adapters/doc_processing/doc_reader.py`） |
| 读取端（整表 JSON） | 第 0 行是标题行（跳过），第 1–2 行是**两级表头** | `excel_to_json`（`app/adapters/ragsystem/data_analyze.py`，写死 `skiprows=[0]` + `header=[0,1]`） |

两套假设互相矛盾，且**都不适配真实文件**：文件布局只要不是「标题行 + 两级表头」，
写入端会把标题行/组行当表头、把真表头行当数据；读取端会把真表头行整行丢掉、
拿前两行数据当列名。错误不抛异常，只产出**静默错位**的财务数字。

### 修复前实测（2026-09-21，8 个源文件 / 439 块，全部来自 MinIO 源文件重新解析）

| 文件 | 库内块 | 旧口径受影响块 | 空标签块（`：值`） | 典型错位 |
|---|---:|---:|---:|---|
| Startup项目信息更新202605.xlsx | 153 | 153 | 0 | 38 列里 14 列重名（`试制材料`×3 等）→ 读取端 `to_dict` 静默丢列 |
| NICE BG_Project list财务指标.xlsx | 104 | 88 | 5 | `工作表1` 首行全空 → 整 sheet 的块都是「`：值`」 |
| NICE BG_内部订单号命名.xlsx | 88 | 84 | 42 | 尾部空列 → `：open`（标签丢失） |
| PM项目分配表_0618.xlsx | 33 | 5 | 5 | 透视表 `Sheet1` 二级表头被当数据 |
| FONE模板_主数据收集模板.xlsx | 26 | 26 | 15 | `基础表` 19/29 列表头为空 → 值无标签 |
| 华翔定价表报价模型基础参数.xlsx | 21 | 14 | 14 | 首行是标题（`IM注塑模具基础系数`）→ 4 个 sheet 的块全部带错标签 |
| 项目利润表总览模板.xlsx | 9 | 9 | 9 | 首行是两级表头的组行 → `项目信息：项目编号, ：客户, …` |
| NICE内部单位清单-new.xlsx | 5 | 0 | 0 | 无反例（单级表头，两侧恰好一致） |
| **合计** | **439** | **379** | **90** | 另有 129 列在读取端会因空/重复列名被静默吞掉 |

读取端示例（`NICE BG_Project list财务指标.xlsx`，修复前）：`skiprows=[0] + header=[0,1]`
把 `1/赵琨` 与 `2/洪鑫浩` 两行数据当成了列名——表头变成 `序_1 / 负责人_赵琨 / …`，
前两行真实数据同时消失；写入端则把 `负责人=洪鑫浩` 正确落库，于是「检索命中的 chunk」
与「整表 JSON」的行列语义完全对不上。

## 2. 唯一口径：`app/domain/knowledge/excel_layout.py`

新增纯逻辑模块（stdlib，不 import pandas；两个 adapter 共用，CI 无 pandas 也能跑）。
行号是**绝对行号**，`headers` 是唯一、非空的列名元组。

| # | 规则 | 说明 |
|---|---|---|
| 1 | 归一化 | `None/NaN/NaT/空白 → ""`，其余 `str(v).strip()`（数字/日期两端一致） |
| 2 | 空 sheet | 返回 `None`；调用方按空表处理（不产 chunk、不算失败） |
| 3 | 前导空行 | 跳过整行全空的行（真实文件 `工作表1` 首行全空），行号仍取绝对值 |
| 4 | 标题行 | 首行仅 1 个非空格、该格非数值/日期、且第 1 行像表头 → `has_title_row=True` |
| 5 | 像表头 | 非空格 ≥ 2 且数值/日期样格占比 < 50%；**单列表格**（每行最多 1 个非空格）首行即表头 |
| 6 | 级数 | 表头行存在 ≥2 连续空格（跨列合并痕迹）且下一行像表头：填充率 ≥0.8 → 两级；≤0.2 → 一级；中间 → **拒绝** |
| 7 | 拒绝 | 首行不像表头（`no_header_row`）或级数判不准（`ambiguous_header_levels`）→ 抛 `ExcelLayoutError` |
| 8 | 列名 | 两级时组名向右填充 + `组_子` 拼接；空名 → `列N`；重名 → `_2`/`_3` |
| 9 | 数据行 | 去全空行、列宽对齐表头；写入端与读取端都调 `data_rows()` |

**拒绝语义是承重的**：判不准时宁可让用户看到失败原因，也不退回旧口径继续产错位数据。

真实语料验证（8 文件 / 32 个可解析 sheet）：只有 1 个散文 sheet（`项目利润表总览模板.xlsx`
的 `操作指南`，两列「步骤/说明」文本）被判为不可解析，其余全部正确判定，无误拒。

## 3. 两端接入后的行为

### 写入端（`ExcelParser` / `DocumentProcessor`）

- 每个 sheet 用同一探测结果产出 `{"headers", "rows", "layout", "sheet_name"}`；被拒的
  sheet 记入 `ExcelParser.skipped_sheets`（含原因）并打 WARNING，其他 sheet 照常解析。
- **整文件无可用表格时抛 `DocumentParseError`**（沿用 issue #15 失败反馈链）：
  单 sheet 模式（知识库上传）sheet 0 被拒、或多 sheet 模式全部 sheet 被拒/为空/
  只有表头没有数据行，任务标记失败并带 sheet 名与原因，不再 `continue` 静默跳过。
- 部分 sheet 被拒 → 文件成功，明细经 `pipeline` 结果与任务完成消息回报：
  `文档处理完成，跳过 N 个无法识别表头的 sheet（详见任务结果）`，`result.skipped_sheets`
  给出「文件名 / sheet / 原因」。
- chunk metadata 新增 `excel_layout`（如 `header_row=1,levels=1,title=1,data_start=2`），
  存量对账脚本凭它区分新旧数据。

### 读取端（`excel_to_json`）

- 走同一探测；输出形状不变：`{"sheet_name", "headers": [...], "rows": [{...}]}`，
  ragchain / polars / 图表链路无需改动。重名列去重后不再静默丢列。
- `sheet_name`：**只有探测到首行是标题行**时才取首行那一格，否则用真实 sheet 名
  （旧实现拿首行第一个非空格，会得到 `序`、`项目号` 这种怪标题）。
- 探测判不准 → 抛 `ExcelLayoutError`，由 `_charts_from_response` 转成
  `{"error": ...}` 返回（`answer` 为错误信息、`sources=[]`），不再返回错位数据。
- 兼容：显式传 `skiprows` / `header_rows` 时保留旧手工口径并打 WARNING（仓内无调用方）。

## 4. 存量比对报告（修复前）

只读对账脚本 [`scripts/check_excel_layout.py`](../scripts/check_excel_layout.py)：
拉取 `data_excel_db_chunks` 的文件/sheet/块文本，从 MinIO 下载源文件重新解析，
逐行比对「库内块文本」与「当前口径渲染」，并给出旧口径证据。

修复前运行（退出码 1 = 存在需重灌文件）：

```
- 源文件 8 个｜库内块 439 条｜需重灌文件 7 个 / 候选块 379 条
- 旧口径证据：空标签（：值）块 90 条｜读取端会因空/重复列名静默丢列 129 列（最坏情况）
- 需重灌：NICE BG_内部订单号命名.xlsx、NICE BG_Project list财务指标.xlsx、
  Startup项目信息更新202605.xlsx、FONE模板_主数据收集模板.xlsx、PM项目分配表_0618.xlsx、
  项目利润表总览模板.xlsx、华翔定价表报价模型基础参数.xlsx
```

逐 sheet 明细（截取，完整表见脚本输出）：

| 文件 / sheet | 库内块 | 空标签块 | 旧口径丢列 | 判定 |
|---|---:|---:|---:|---|
| 项目利润表总览模板.xlsx / 项目利润表总览 | 7 | 7 | 17 | 组行被当表头（`项目信息：项目编号, ：客户…`） |
| 项目利润表总览模板.xlsx / 操作指南 | 2 | 2 | 1 | 散文 sheet，新口径按「拒绝」处理 |
| 华翔定价表报价模型基础参数.xlsx / 模具系数表 | 4 | 4 | 5 | 标题行被当表头（`IM注塑模具基础系数：#`） |
| NICE BG_Project list财务指标.xlsx / 工作表1 | 2 | 2 | 5 | 首行全空 → 块内全是「`：值`」 |
| NICE BG_Project list财务指标.xlsx / 全新项目 | 83 | 0 | 3 | `IBP/内控/SOP` 各重名一次 → 读取端丢 3 列 |
| Startup项目信息更新202605.xlsx / 项目信息 | 153 | 0 | 14 | 3 组预算列重名 → 读取端丢 14 列 |
| FONE模板_主数据收集模板.xlsx / 基础表 | 11 | 8 | 19 | 19/29 列表头为空 → 值无标签 |
| PM项目分配表_0618.xlsx / Sheet1 | 5 | 5 | 3 | 透视表二级表头被当数据 |

## 5. 存量重灌执行记录（2026-09-21）

方式：dev 容器后端（`bash scripts/dev.sh docker backend`）逐个文件走**现有上传接口**
`POST /api/v1/knowledge/excel-db?on_conflict=replace`，每个文件一个任务、轮询
`GET /api/v1/document-tasks/status/{task_id}` 至完成。**逐文件而非一次 8 个**：
issue #24（replace 在后台处理前先删旧块）未修，分批把爆炸半径压到单文件。
源文件未改动（MinIO 里仍在），失败可重跑同一条上传（幂等）。

| 文件 | 任务 ID | 状态 | 块数 前→后 |
|---|---|---:|---:|
| 项目利润表总览模板.xlsx | `doc_process_20260921_085905_864_d3574bdd` | completed | 9 → 11 |
| NICE BG_内部订单号命名.xlsx | `doc_process_20260921_085919_467_45214b81` | completed | 88 → 91 |
| NICE BG_Project list财务指标.xlsx | `doc_process_20260921_085928_648_bf27cf86` | completed | 104 → 110 |
| Startup项目信息更新202605.xlsx | `doc_process_20260921_085937_860_c97e978b` | completed | 153 → 153 |
| FONE模板_主数据收集模板.xlsx | `doc_process_20260921_085950_060_3d529c5c` | completed | 26 → 28 |
| PM项目分配表_0618.xlsx | `doc_process_20260921_085956_364_52d368b9` | completed | 33 → 35 |
| 华翔定价表报价模型基础参数.xlsx | `doc_process_20260921_090002_552_195d3093` | completed | 21 → 24 |
| NICE内部单位清单-new.xlsx（口径未变，未重灌） | — | — | 5 → 5 |
| **合计** | | | **439 → 457** |

副作用（已知且可接受）：

- `uploader` 由 `NBHX` 变为执行重灌的账号（`superuser`）；`file_resource` 新增 7 行，
  旧行留作历史（知识库记录列表读 chunk 表，不受影响）。
- 重灌后 MinIO 对象路径换了一批 `documents/20260921_0859*_*.xlsx`（旧对象不再被引用）。
- `项目利润表总览模板.xlsx` 的 `操作指南` sheet 按新口径**被拒绝**，其旧的 2 个块随
  replace 删除（散文内容不再进知识库；任务结果里以「跳过 1 个无法识别表头的 sheet」可见）。

## 6. 重灌后复验

1. **对账脚本**（重灌后重跑）：

   ```
   - 源文件 8 个｜库内块 457 条｜需重灌文件 0 个 / 陈旧块 0 条
   - 库内 vs 当前口径：缺失行 0｜多余行 0｜空标签（：值）块 0
   - 全部文件的库内块与当前口径一致，无需重灌
   退出码 EXIT=0
   ```

   另有 457 块中 452 块带 `excel_layout` metadata（未重灌的 NICE内部单位清单-new 5 块除外）。

2. **检索端到端冒烟**（`POST /api/v1/retriever/excel`，不传 `top_k` 即整表 JSON 路径）：

   | 问题 | sources | sheet_name | 命中 |
   |---|---|---|---|
   | `项目 V254 (GLC) 的负责人是谁？查查表` | NICE BG_Project list财务指标.xlsx | 全新项目 | `{"序":"2","负责人":"洪鑫浩","OEM":"北京奔驰","产品":"注塑件","量产地":"沈阳"}` |
   | `查一下 V254 (GLC) 项目的负责人是谁，顺便给出OEM和量产地` | PM项目分配表_0618.xlsx | PM项目清单 | `{"项目经理":"杨贵宁","OEM":"北京奔驰","项目名称":"V254 (GLC)","量产地":"沈阳"}` |

   修复前这条链路的列名是 `序_1 / 负责人_赵琨` 这类错位结果，且前两行数据被吞；现在列名
   与行内容都与源文件一致（`sheet_name` 也回到真实 sheet 名）。

3. **失败/跳过反馈实证**：重灌 `项目利润表总览模板.xlsx` 的任务消息为
   `文档处理完成，跳过 1 个无法识别表头的 sheet（详见任务结果）`，
   `result.skipped_sheets[0] = {file_name, sheet_name: "操作指南", reason: "无法可靠探测表头：…"}`。

## 7. 测试与验收映射

| issue #23 验收项 | 证据 |
|---|---|
| 同一文件两侧表头/数据行语义一致（三类布局用例） | `tests/test_excel_retrieval_hardening.py::test_write_and_read_paths_agree_on_layout`（标题行+一级 / 两级 / 含空列一级，参数化 3 例，写入端 `ExcelParser` ↔ 读取端 `excel_to_json` 逐项比对） |
| 无法可靠探测 → 解析失败反馈、不产 chunk | `tests/test_excel_parser_engine.py::test_process_document_refuses_single_sheet_without_header` / `test_process_document_empty_excel_fails_with_reason` / `test_write_path_records_refused_sheet_and_keeps_others` / `test_pipeline_reports_skipped_sheets_and_message_mentions_them`；读取端 `test_excel_to_json_refuses_undecidable_layout` |
| 存量比对报告 + 关联重灌 | 本文件 §4/§5/§6；issue #23 评论附摘要 |
| `test_excel_parser_engine.py` / `test_excel_retrieval_hardening.py` 扩展后全绿 | 容器内 `python -m pytest -q` = 549 passed, 4 skipped；分层守卫 PASSED；CI（GitLab pipeline，arch-guard + pytest） |

新增纯逻辑测试 `tests/test_excel_layout.py`（16 例）**不依赖 pandas**，CI 的 pytest job
（只装最小依赖集）也能跑；xlsx 端到端用例沿用 `pytest.importorskip("pandas")`。

顺带修掉的 CI 阻塞：`.gitlab-ci.yml` 的 pytest job 用 YAML fold 块（`>`）写 pip 清单，
issue #21 在清单中间插了三行 `#` 注释 → 折行后 `#` 之后（含 `prometheus_client`/`psutil`）
全被当注释丢弃，pipeline #111 起 pytest 实际跑不起来（conftest 导入 `psutil` 失败）。
注释已移到 fold 块之外。

## 8. 已知边界与非目标

- **单 sheet 内多区块表头**（如 FONE `基础表`：A–I 列是块 A 的表头，J–AC 列的表头在第 18 行）
  不识别，按「第 0 行表头 + 空列 `列N`」处理；该布局列入后续 issue（与 #19 的表格结构化一起做）。
- **散文 sheet**（`操作指南` 这类）与「标题 + 单格组行」一律**拒绝**：宁可失败并给原因，
  不猜表头。单列表格（如 `元数据` 编码/X/Y）例外，首行即表头。
- 重名列命名规则为 `名字 / 名字_2 / 名字_3`（固定后缀，不还原原始分组语义）；
  两级表头扁平化为 `组_子`（组名取右侧合并空位的上一个非空组名）。
- 不改 `ragchain/`（只消费 `/retriever/excel` 的 JSON，形状未变）；不做 issue #19 的
  PDF/DOCX 表格结构化与 Markdown 输出；不做自动重灌脚本（用现有上传接口）。

## 9. 运维入口

```bash
bash scripts/dev.sh docker shell
python scripts/check_excel_layout.py                       # 全部文件（只读），退出码 0/1
python scripts/check_excel_layout.py --out /tmp/report.md  # 报告落盘
python scripts/check_excel_layout.py --file documents/xxx.xlsx
python scripts/check_excel_layout.py --json
```

判定口径：**库内块文本缺行 = 陈旧**（需重灌）；**多余行 = 残留旧口径**（重灌后消失）；
被拒绝但库内仍有块的 sheet 一律计为陈旧。重灌后应得到「需重灌文件 0 个 / 缺失行 0 /
多余行 0 / 空标签块 0」与退出码 0。
