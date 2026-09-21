# 混合检索（路线 1：PG 字面检索 + RRF 融合）

> issue #16【P0】检索为纯向量单路召回，缺稀疏/混合检索
> 落地日期：2026-09-21 ｜ 状态：代码已落地，**开关默认关闭**，待 golden set 度量后启用

## 1. 问题

检索链只有 dense 向量一路（`RAGretriever.py` 的 `similarity_top_k`），对「项目号 V254 /
人名 杨贵宁 / 科目名 / 文件编号 NBHX-GZZD-HR-006」这类精确 token 天然偏弱；而改写步骤
（`ragchain/app/steps/query_rewriter.py` 的 `RewriteResult`）本来已产出 `keywords`，却只被
空格拼进查询串，没有参与任何字面召回。

## 2. 路线决策：pg_trgm + ILIKE，不用 tsvector / zhparser

| 候选 | 结论 | 依据（本机实测，2026-09-21） |
|---|---|---|
| `zhparser`（中文分词 + tsvector） | **不可用** | `pg_available_extensions` 里没有 zhparser——它不在 `pgvector/pgvector:pg16` 镜像内，要装必须重建共享 infra 容器并维护 SCWS 词典 |
| `to_tsvector('simple', text)` | **无效** | PG 默认 parser 把整串 CJK 当一个 token，中文不分词时查询词永远匹配不上 |
| llama-index `PGVectorStore(hybrid_search=True)` | **不可用** | 需要建表时的 `text_search_tsv` 列（现表没有，需 ALTER + 触发器回填）**且**查询侧要稀疏向量；本项目的 BGE-M3 走 OpenAI 兼容 `/v1/embeddings`，只返回稠密向量，且 `embedding_store._vectors` 对非标准响应形状显式报错 |
| **`pg_trgm` + `ILIKE` + GIN**（选中） | **可用且改动最小** | `pg_trgm` 1.6 在 `pg_available_extensions` 里（contrib，`CREATE EXTENSION` 即可，DB 用户 `root` 是 superuser 已验证）；trigram 按字符切分，天然覆盖 CJK 子串与 `V254` 这类短 token；不需要应用侧分词器，不需要改入库链路 |

代价与已知限制见 §6。

## 3. 架构与数据流

```
ragchain: rewrite_query → RewriteResult{rewritten_query, keywords}
  └─ retrieve_local(rewritten_query, keywords)
       ├─ dense  query = build_query(rewritten_query, keywords)   ← 与改造前逐字一致
       └─ sparse keywords = rewrite.keywords                      ← 新增
  └─ RetrieverClient.query_db(body={"question":…, "keywords":[…]})
主应用 POST /retriever/db?top_k=10&rerank=false
  └─ HybridRetrieverAdapter（组合根 build_retriever_port 按开关装配）
       ├─ RAGRetrieverAdapter  → 纯向量 top_k
       ├─ PostgresLexicalSearcher → data_<collection> 上 ILIKE top_k
       └─ RRF(k=60) 融合 → 截断 top_k → chunks（带 node_id / retrieval 溯源）
ragchain 侧 reranker(top_n=5) → prompt
```

分层落位：

| 关注点 | 位置 |
|---|---|
| 关键词清洗 / 去重键 / RRF（纯逻辑） | `app/domain/retrieval/ranking.py` |
| 字面检索 + 索引保障 | `app/adapters/knowledge/lexical_search.py` |
| 集合名 → 物理表映射（白名单防注入） | `app/adapters/knowledge/collection_tables.py` |
| 双路召回 + 融合装饰器、组合根工厂 | `app/adapters/retriever.py` |
| 端口与 DTO | `app/ports/outbound/retriever.py`（`RetrievalQuery.keywords`、`LexicalHit`、`LexicalSearchPort`） |

## 4. 契约变更（向后兼容）

**请求体（`/retriever/db`、`/retriever/excel`）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `keywords` | `string[]`，可选 | 改写步骤产出的结构化关键词。缺省/空 = 纯向量，行为与改造前一致；ragchain 仅在非空时才把它放进 body（老 payload 形状逐字节不变） |

**响应 `chunks[]`**

| 字段 | 说明 |
|---|---|
| `node_id` | chunk 身份（PGVector 的 `node_id` 列）。**所有** chunks 路径都带，用于双路结果去重 |
| `retrieval` | 仅融合路径返回：`{"paths": ["dense","lexical"], "rrf": 0.0328, "lexical_hits": 2}`。`paths` 标明该 chunk 由哪几路召回，`rrf` 是融合分，`lexical_hits` 是命中的关键词个数 |
| `score` | 语义不变：dense 的向量相似度；**只有字面路命中**的 chunk 为 `null`（无向量分，顺序由 RRF 决定） |

未显式传 `top_k` 的旧路径（内部 query engine + 重排）**完全不融合**，响应形状不变。

## 5. 索引与运维

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_data_knowledge_chunks_text_trgm
  ON data_knowledge_chunks USING gin (text gin_trgm_ops);
```

- 运维入口：`python scripts/ensure_fulltext_index.py [--dry-run] [--no-concurrently]`
  默认用 `CREATE INDEX CONCURRENTLY`（不锁写），大表请先离线跑再开开关。
- 自动保障：开关打开时，`main.py` 的 lifespan 会把同样的检查放**后台任务**执行
  （不阻塞启动；失败只告警）。只对 `data_%` 且同时含 `text`/`node_id` 列的表建索引
  （`data_shares` 等平台表自动跳过）。
- 无 superuser / 无 contrib：`CREATE EXTENSION` 失败只告警并跳过建索引——
  `ILIKE` 仍能工作，只是退化为顺序扫描，**功能不降级**。

## 6. 已知限制

1. **短关键词（< 3 字符）**：pg_trgm 无法从搜索模式里提取 trigram，索引不能做选择性剪枝。
   实测（2026-09-21，`EXPLAIN`）：默认设置下**所有**模式（含 23 字符的 `NBHX-GZZD-HR-006`）
   在这两张小表上都走顺序扫描；`SET enable_seqscan = off` 时 1–2 字符中文也走
   `Bitmap Index Scan on idx_data_knowledge_chunks_text_trgm`（即全索引扫描而非精确命中的剪枝）。
   当前数据量（153 / 439 行）下延迟无差异；规模化后需评估 `pg_bigm`（2-gram）或重建镜像加 zhparser。
2. **`ILIKE` 只做 ASCII 大小写不敏感**（`V254`/`v254` 等价；非拉丁字母按原样比较）。
3. **无相似度阈值**：字面路命中即入池（由 RRF 名次与 reranker 决定最终顺序），
   阈值/MMR 不在本次范围（会把「总是返回 N 条」变成「有时 0 条」，需先有评测基线）。
4. **`metadata_` 是 `json` 列**：适配器取 `metadata_::text` 后在 Python 侧 `json.loads`，
   解析失败退回 `{}`（不抛异常）。

## 7. 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `RETRIEVAL_HYBRID_ENABLED` | `False` | 混合检索总开关（关闭 = 纯向量，可作秒级回滚开关） |
| `RETRIEVAL_LEXICAL_TOP_K` | `10` | 字面路召回条数（与 dense 的 `top_k` 各自独立） |
| `RETRIEVAL_RRF_K` | `60` | RRF 平滑常数：`score = Σ 1/(k + rank)` |
| `RETRIEVAL_MAX_KEYWORDS` | `12` | 单请求最多使用多少关键词（防 SQL 膨胀） |
| `RETRIEVAL_KEYWORD_MAX_LEN` | `64` | 单个关键词最大长度（超长丢弃） |

## 8. 启用与回滚

```bash
# 1) 建索引（幂等，可重复执行）
source scripts/env.sh
python scripts/ensure_fulltext_index.py --dry-run   # 先看要执行什么
python scripts/ensure_fulltext_index.py             # CONCURRENTLY 建索引

# 2) 打开开关（.env）
RETRIEVAL_HYBRID_ENABLED=True

# 3) 重启后端；启动日志应出现「字面检索索引就绪: idx_data_*_text_trgm」
# 回滚：RETRIEVAL_HYBRID_ENABLED=False 后重启即可（索引留着无害）
```

## 9. 评测待办（下一轮，对应 #16 验收第 4 条）

⚠️ issue #18 的四条验收已在 issue 里勾选完成，但**仓库、三个分支、工作区与 issue 正文/评论中
都不存在 golden set、评测脚本或基线数字**。下一轮开局第一件事是定位或重建该资产。

评测方法（开关即前后对比的旋钮）：

1. 同一批问题、同一 `collection` / `top_k` / `rerank top_n`，分别用
   `RETRIEVAL_HYBRID_ENABLED=false` 与 `=true` 各跑一遍；
2. 指标：`recall@k`、`MRR`、未命中率，按问题类型分组（制度类 / 台账字段类 / 聚合计算类 /
   精确词类 / 未命中类）；
3. 重点看**精确词类**（项目号、人名、科目名、文件编号）的召回增益；
4. 数字与参数（collection、top_k、rerank top_n、检索日期）写回 issue #16。

## 10. 验证

```bash
bash scripts/check_layered_architecture.sh          # 8 条规则
pytest -q                                           # 主应用套件（CI 同构：无 llama-index）
bash scripts/dev.sh docker test -q                  # 完整环境（含 llama-index）
cd ragchain && ../.cache/ragchain-venv/bin/pytest -q # RAG 容器套件
```

新增测试：`tests/test_hybrid_retrieval.py`（关键词清洗 / RRF / 装饰器 / API 契约）、
`tests/test_lexical_search_adapter.py`（SQL 形态与转义 / 集合名白名单 / 降级语义 / 索引 DDL）。
