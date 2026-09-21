# 检索查询级缓存（结果缓存 + 查询嵌入缓存）

> issue #21【P1】检索结果无查询级缓存，重复问题每次全链路重算
> 落地日期：2026-09-21 ｜ 状态：代码已落地，**开关默认开启**（TTL 5 分钟）
> 关联：#22（reranker 熔断——同属延迟治理，独立 issue）

## 1. 问题

`/retriever/db`、`/retriever/excel` 都是 POST（`app/api/v1/retriever.py:64/120`），而 HTTP
缓存中间件只缓存 GET（`app/core/middleware/middleware_cache.py:46`），所以每次检索都要重付：

| 固定成本 | 位置 |
|---|---|
| 1 次 BGE-M3 查询嵌入 HTTP | `app/adapters/doc_processing/embedding_store.py`（`_aget_query_embedding`） |
| 1 次 PG 向量查询 | `app/adapters/ragsystem/retriever_for_nbhx.py`（`get_chunks_async`） |
| 混合检索开启时再 1 次 PG 字面查询 | `app/adapters/knowledge/lexical_search.py` |
| （旧路径）1 次 rerank HTTP | `app/adapters/ragsystem/RAGretriever.py` 的 `HTTPReranker` |

企业场景里同一句制度问题被不同人反复问很常见，这些成本全部重复支付。

## 2. 方案总览

```
/api/v1/retriever.py（组合根）
  └─ build_retriever_port(rag, collection)
       └─ CachingRetrieverAdapter          ← 第 1 层：检索结果缓存（issue #21）
            └─ HybridRetrieverAdapter / RAGRetrieverAdapter
                 └─ ragsystem.retriever_for_nbhx
                      └─ BGEM3EmbeddingWrapper._aget_query_embedding  ← 第 2 层：查询嵌入缓存

写入端（知识库变更）
  pipeline.process（上传入库，worker 线程）→ invalidate_collection_sync
  metadata.delete_chunks_by_file_name（替换/删除文件）→ invalidate_collection
  persistence.delete_knowledge_record（删单条 chunk）→ invalidate_collection
```

设计约束：

- **不进 ragsystem 内部**：缓存挂在 adapter 端口层，`retriever_for_nbhx.py` / `RAGretriever.py`
  一行未改；UseCase 签名不变。
- **缓存实现不设 Port**：当前只有 adapter 消费它（与 `app/adapters/vector_store_manager.py`
  同类，属 adapter 内部工具）；将来若 usecase 需要缓存能力，再抽 Port。
- 键与归一化是纯函数，落在 domain：`app/domain/retrieval/cache_key.py`。

## 3. 缓存键设计

```
retrieval:cache:{scope}:{collection}:v{version}:{sha256_32}
retrieval:emb:{model}:{sha256_32}
retrieval:ver:{collection}          # 集合版本号，无 TTL
```

摘要输入（canonical JSON，`ensure_ascii=False`，`sort_keys=True`）：

| 字段 | 取值 | 为什么进键 |
|---|---|---|
| `q` | 归一化问题（见下） | 核心维度 |
| `path` | `chunks`（显式 top_k）/ `legacy`（旧 get_response / get_charts） | 两条路径代价与返回形状都不同，混用会串结果 |
| `top_k` | 仅 chunks 路径记录 | 决定召回条数与结果集 |
| `kw` | 归一化关键词集合（排序后） | 混合检索开启时字面路的真实输入 |

`scope` = `db` / `excel`（端点域）；`collection` 直接进键名（跨集合隔离）。

**归一化 = str 化 → Unicode 空白折叠为单个半角空格（含 `\u3000`、`\xa0`）→ strip**。
刻意**不做大小写折叠**：中文为主，且 `SAP` / `sap`、`V254` / `v254` 这类实体大小写有语义，
折叠后还会让两段不同文本共用同一条查询嵌入。折叠是「多空格 → 1 个」而非删除，
所以 `年假标准` 与 `年假 标准` 仍是两个不同的问题（有用例固定该语义）。

**关键词取「集合」而非「列表」**：用与检索侧同一个
`app.domain.retrieval.ranking.normalize_keywords`（同一 `RETRIEVAL_MAX_KEYWORDS` /
`RETRIEVAL_KEYWORD_MAX_LEN` 上限）清洗后排序。截断子集不同 ⇒ 排序结果必不同，
因此不会把两组不同关键词映射成同一个键（只提升命中率，不引入错误命中）。
`PostgresLexicalSearcher.search` 的命中与关键词顺序无关
（`ORDER BY hits DESC, length(text) ASC, node_id ASC`），排序编码是安全的。

**`metadata["rerank"]` 刻意不进键**：chunks 路径根本不消费它（见 `_should_return_chunks`
的注释）。若将来 chunks 路径开始消费 rerank，必须把它加进键并补用例——代码里留了同款提醒。

**键只含 collection，不含用户身份**：ACL 是「集合白名单」，在路由层
（`_ensure_collection_access`）先于端口执行，且普通用户的可见集合是常量。若将来出现
「按用户过滤的检索结果」，必须重审本设计（issue #21 明确要求记录该边界）。

## 4. 一致性：集合版本号 + TTL 兜底

- 每个 collection 一个 Redis 计数器 `retrieval:ver:<collection>`（**不设 TTL**：版本号过期
  归零会让失效前的旧条目重新命中；键数按集合数量有界）。
- 写入端（上传入库 / 删文件 / 删记录）`INCR` 一次；键里带 `v{version}`，旧条目**不再可达**，
  按 TTL 自然过期——写路径因此**不需要 SCAN 删键**（避免在写入热路径上遍历 keyspace）。
- TTL（默认 300s）是**兜底**：某条写入路径漏 bump，或版本键被 maxmemory 淘汰时，陈旧窗口
  上限就是 TTL。这也是 issue 里「接受 TTL 内的陈旧」的落地口径。
- 同步写入路径（文档处理 worker 线程自建事件循环）用**独立 sync 客户端**（连接 0.5s /
  读写 1s 超时）bump，避免跨事件循环复用 async 客户端；异步删除路径用全局 `redis_manager`。
- 多实例部署天然一致：版本号在 Redis 里共享。

## 5. 失败语义（承重）

全链路 **fail-open**：Redis 任何异常都只记日志 + 指标，绝不抛进检索 / 入库链路。

| 场景 | 行为 |
|---|---|
| Redis 不可用（版本号取不到） | **整体旁路**，行为与改造前逐字一致 |
| 读失败 / 写入失败 / 序列化失败 | 当未命中处理，照常检索；写入跳过（DEBUG 日志） |
| 内层检索抛异常 | 照常抛出，**不缓存错误** |
| 结果为空（空 chunks / 空 answer） | **不缓存**——否则一次网关抖动会被固化成数分钟的「空答案」 |
| 载荷超过 `RETRIEVAL_CACHE_MAX_PAYLOAD_BYTES` | 跳过写入（挡住 `/excel` 整表 JSON 撑爆 Redis） |
| 缓存条目形状不符 / schema 版本过期 | 删除该键并按未命中处理 |
| 嵌入返回全零向量（空文本 / NaN 回退） | 不缓存（避免固化降级结果） |

同类错误只 `WARNING` 一次（之后降 DEBUG），避免 Redis 长时间不可用打爆日志。

## 6. 可观测

日志（`app.adapters.retrieval_cache` → 控制台 + `logs/app.log`）：

- 命中：`检索缓存命中: scope=db collection=knowledge_chunks path=chunks`（INFO）
- 失效：`检索缓存已失效: collection=... version=...`（INFO，含同步路径）
- 旁路 / 失败：`WARNING`（限噪）+ `DEBUG`

指标（`/api/v1/metrics`，`app/adapters/monitoring/prometheus.py`）：

| 指标 | 标签 | 用途 |
|---|---|---|
| `aida_retrieval_cache_lookups_total` | `layer=result\|embedding`、`outcome=hit\|miss\|skip\|error` | 命中率 / 跳过率 / 错误率 |
| `aida_retrieval_cache_invalidations_total` | — | 写入端失效次数 |

刻意不打 `collection` 标签（集合数随知识库增长，标签基数不可控）。

## 7. 配置（`.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `RETRIEVAL_CACHE_ENABLED` | `True` | 总开关；`false` = 整体旁路（回滚开关） |
| `RETRIEVAL_CACHE_TTL_SEC` | `300` | 结果缓存 TTL，同时是漏失效时的最大陈旧窗口 |
| `RETRIEVAL_CACHE_MAX_PAYLOAD_BYTES` | `262144` | 单条载荷上限；`0` = 不限 |
| `RETRIEVAL_EMBEDDING_CACHE_ENABLED` | `True` | 第 2 层（查询嵌入）开关 |

## 8. 启用与回滚

```bash
# 默认即开启，无需额外步骤（重启后端生效）
# 回滚：.env 里 RETRIEVAL_CACHE_ENABLED=False 后重启
# 只想退回第 1 层：RETRIEVAL_EMBEDDING_CACHE_ENABLED=False
# 清缓存（不影响知识库）：redis-cli -a "$REDIS_PASSWORD" --scan --pattern 'retrieval:*' | xargs -r redis-cli -a "$REDIS_PASSWORD" DEL
```

## 9. 已知限制 / 非目标

- **不做并发合并（single-flight）**：同一时刻 N 个相同问句的首次并发仍会各算一次。
  企业内单问句并发量低，暂不引入分布式锁；如需再加。
- **不缓存空结果 / 错误**（见 §5），因此「本来就没有答案」的问题每次都重算。
- **配置变更不立即生效**：TTL 内改 `RETRIEVAL_HYBRID_ENABLED`、RRF 参数等不会即时反映到
  已缓存条目；等 TTL 过期或 `DEL retrieval:*`。
- **ragchain 侧不缓存**（容器内 LLM 改写 / 意图分类等另有成本，属独立议题）。
- **同步嵌入钩子不接缓存**：`_get_query_embedding` 只服务轻量替身 / 线程池回退，
  那里没有 loop-safe Redis 客户端；HTTP 链路全走异步钩子。
- 版本键若被 maxmemory 淘汰 → 陈旧窗口 ≤ TTL（见 §4）。

## 10. 验证

单元测试（无 Redis / 无 DB / 无 llama-index，CI 可跑）：

```bash
bash scripts/dev.sh docker test -q        # 或 pytest -q
```

| 文件 | 覆盖 |
|---|---|
| `tests/test_retrieval_cache.py` | 归一化 / 键维度 / 命中省调用 / 跨集合隔离 / 版本号失效 / 空结果与异常不缓存 / fail-open / 超限跳过 / 指标 |
| `tests/test_retrieval_cache_invalidation.py` | 删除路径 bump（含 rowcount=0 与懒建表不 bump、Redis 挂时删除照常成功） |
| `tests/test_pipeline_chunk_dedup.py` | 上传入库后同步 bump；全重复无写入时不白 bump |
| `tests/test_embedding_wrapper.py` | 异步查询嵌入第二次零 HTTP；失败 / 全零不缓存；缓存挂 fail-open；同步钩子不接缓存 |

端到端（真实 Redis / PG / BGE-M3 链路）：同问句连发两次，第二次出现命中日志且响应体一致；
`INCR retrieval:ver:<collection>` 后同一问句必回源；`/api/v1/metrics` 能看到 hit/miss 计数。
步骤与实测证据见 §11。
## 11. 实施记录（2026-09-21）

### 改动清单

- `app/domain/retrieval/cache_key.py`（新增）：归一化 + 三条键构造（纯函数）
- `app/adapters/retrieval_cache.py`（新增）：`RetrievalCache`（结果 / 嵌入 / 版本号 + 两套客户端 + 指标 + 单例）
- `app/adapters/retriever.py`：`CachingRetrieverAdapter` + `build_retriever_port(cache=...)`
- `app/adapters/doc_processing/embedding_store.py`：`_aget_query_embedding` 接缓存
- `app/adapters/doc_processing/pipeline.py` / `app/adapters/knowledge/metadata.py` /
  `app/adapters/knowledge/persistence.py`：写入端失效钩子
- `app/core/cache.py`：`RedisKVStore.incr`
- `app/adapters/monitoring/prometheus.py`：两个缓存指标
- `app/core/config.py` / `.env.example`：4 个配置项
- `main.py`：退出时关闭缓存同步客户端
- `.gitlab-ci.yml`：pytest 依赖补 `prometheus_client` / `psutil`（缓存模块 import 链需要）
- `tests/conftest.py`（新增）：单测默认关缓存，避免触碰真实 Redis

### 与原计划的差异

| 计划 | 实际 | 原因 |
|---|---|---|
| 新增 `tests/test_embedding_query_cache.py` | 改在既有 `tests/test_embedding_wrapper.py` 里追加 | 直接复用该文件既有的 `FakeGateway` / `_AsyncHarness`，避免复制一套注入代码；嵌入缓存自身的语义仍在新文件里覆盖 |
| 新增 `tests/conftest.py`（原计划未提） | 新增 | 缓存默认开启后，任何走到检索 / 入库的既有用例都会对真实 Redis 发命令（本地污染共享实例、CI 刷连接失败告警）；用 autouse 夹具统一替换为关闭态单例 |
| 计划里写「归一化 = strip + 空白折叠」 | 明确为「折叠成单个半角空格」，不做大小写折叠 | 折叠是折叠不是删除：`年假标准` ≠ `年假 标准`；有用例固定 |
| 计划未提 `_store_payload` 的开关判断 | 补上总开关判断 | 首轮测试发现 `enabled=False` 时仍会写入；总开关应短路一切写入 |

### E2E 实测（2026-09-21，本地真实链路）

环境：dev 容器 `nbhx-jiazhenyu-dev-1`（宿主 8000）+ pgvector 5433（`data_knowledge_chunks` 153 行）
+ Redis 6379 + BGE-M3/Reranker 网关 172.28.16.50:8096（启动探活均成功）。
请求：`POST /api/v1/retriever/db?collection=knowledge_chunks&top_k=10&rerank=false`，
body `{"question": "...", "keywords": ["报销标准", "费用"]}`，逐次对比指标增量：

| 步骤 | 耗时 | 返回 | 指标增量 |
|---|---|---|---|
| Q1 全新问句 | 345 / 73 ms（两次实验） | 10 chunks | `result=miss` + `embedding=miss`（付 1 次 BGE-M3 HTTP） |
| Q2 重复同一问句 | **4 / 13 ms** | 10 chunks，与 Q1 **逐字段一致** | `result=hit`（嵌入钩子根本没被调到） |
| `INCR retrieval:ver:knowledge_chunks` | — | — | 版本号 0 → 1（模拟写入端失效） |
| Q3 bump 后同一问句 | 8 / 17 ms | 10 chunks | `result=miss`（旧键不可达）+ `embedding=hit`（第 2 层兜住嵌入） |
| Q4 bump 后再重复 | 3 / 13 ms | 10 chunks | `result=hit` |

其它证据：

- Redis：`--scan --pattern retrieval:cache:*` 1 条（键形如
  `retrieval:cache:db:knowledge_chunks:v0:d163d1b43a4a715653671d1f86eca8e9`）、
  `retrieval:emb:*` 1 条；
- 后端日志出现 2 行 `检索缓存命中: scope=db collection=knowledge_chunks path=chunks`，
  且全量日志无任何缓存读写失败 / Traceback；
- `/api/v1/metrics`：`aida_retrieval_cache_lookups_total{layer="result",outcome="hit"} 2.0`、
  `{layer="result",outcome="miss"} 2.0`、`{layer="embedding",outcome="hit"} 1.0`、
  `{layer="embedding",outcome="miss"} 1.0`。

第一轮 345 ms → 4 ms 的绝对差主要来自首次请求的网关建连；**同进程内重复问句稳定在
3–17 ms**（对照组：冷问句 73 ms），且第二次请求未产生任何嵌入 HTTP。

> 备注：写入端 bump 的完整链路（上传 / 替换 / 删除）由单测覆盖，E2E 只用手工 `INCR`
> 验证「版本号参与键 ⇒ 失效生效」，以避免在本地知识库里造脏数据。

