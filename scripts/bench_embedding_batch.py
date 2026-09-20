#!/usr/bin/env python3
"""入库嵌入吞吐对比：改造前 vs 改造后（issue #35 / 并入的 #28）。

背景：真实 BGE-M3 网关（``BGE_M3_API_URL``）在开发机不可达，本脚本自带一个
OpenAI 兼容的**假网关**（``POST /v1/embeddings``，每请求固定 ``--latency-ms`` 延迟），
把「HTTP 往返次数」的差异放大成可测的墙钟时间；向量维度 1024，与 pgvector 列一致。

两种实现跑同一份文档（``--chunks`` 个 chunk）：

- ``legacy``：``git show <rev>:app/adapters/doc_processing/embedding_store.py`` 取旧实现，
  装进临时包 import。它没覆写 ``_get_text_embeddings``，llama-index 的默认实现逐条
  调用 ``_get_text_embedding`` → **每 chunk 一次 HTTP**。
- ``new``：当前工作区实现，``_get_text_embeddings`` 一次 HTTP 带
  ``BGE_M3_BATCH_SIZE`` 条。

两种度量：
1. ``embed``：``get_text_embedding_batch(texts)``——入库链路的嵌入步骤本身；
2. ``ingest``：``VectorStoreManager.upsert_chunks(nodes, ...)``——真实写入 pgvector。

用法（容器内，DB/网关配置来自 .env）：

    bash scripts/dev.sh docker py scripts/bench_embedding_batch.py            # both, 300 chunks
    bash scripts/dev.sh docker py scripts/bench_embedding_batch.py --impl legacy --chunks 300
    bash scripts/dev.sh docker py scripts/bench_embedding_batch.py --impl new --mode ingest

外部网关可达时也可以用 ``--api-url http://<host>:<port>/v1/embeddings --latency-ms 0``
直接对真网关跑（此时不再启动假网关）。
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EMBED_DIM = 1024


class _MockGateway(BaseHTTPRequestHandler):
    """OpenAI 兼容假网关：固定延迟 + 记录每次请求携带的文本条数。"""

    protocol_version = "HTTP/1.1"
    latency_sec = 0.0
    requests: List[int] = []
    lock = threading.Lock()

    def do_POST(self):  # noqa: N802  (BaseHTTPRequestHandler 命名约定)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        inputs = body.get("input") or []
        if isinstance(inputs, str):
            inputs = [inputs]
        time.sleep(type(self).latency_sec)
        data = [
            {"object": "embedding", "index": i, "embedding": [0.01] * EMBED_DIM}
            for i in range(len(inputs))
        ]
        payload = json.dumps(
            {
                "object": "list",
                "data": data,
                "model": body.get("model", "bge-m3"),
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        ).encode()
        with type(self).lock:
            type(self).requests.append(len(inputs))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静默访问日志
        pass


def _start_gateway(latency_ms: int) -> tuple[ThreadingHTTPServer, str]:
    _MockGateway.latency_sec = latency_ms / 1000.0
    _MockGateway.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockGateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1/embeddings"


def _load_legacy(rev: str):
    """把 ``git rev`` 里的旧模块装进临时包 import（相对 import 需要同包 exceptions）。"""
    def _show(path: str) -> str:
        return subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{rev}:{path}"],
            capture_output=True, text=True, check=True,
        ).stdout

    tmp = Path(tempfile.mkdtemp(prefix="legacy-embedding-"))
    pkg = tmp / "legacy_embedding_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "exceptions.py").write_text(_show("app/adapters/doc_processing/exceptions.py"))
    (pkg / "embedding_store.py").write_text(_show("app/adapters/doc_processing/embedding_store.py"))
    sys.path.insert(0, str(tmp))
    return importlib.import_module("legacy_embedding_pkg.embedding_store"), tmp


def _chunk_texts(n: int) -> List[str]:
    """确定性的伪文档：每段约 300 字，贴近真实 chunk 体量。"""
    para = "宁波华翔知识库基准段落：这一段用于嵌入吞吐对比，不含真实业务数据。"
    return [f"[chunk {i:04d}] {para * 6}" for i in range(n)]


def _reset_requests() -> None:
    with _MockGateway.lock:
        _MockGateway.requests = []


def _stats() -> Dict[str, float]:
    with _MockGateway.lock:
        per_request = list(_MockGateway.requests)
    total = sum(per_request)
    return {
        "http_requests": len(per_request),
        "texts": total,
        "avg_inputs_per_request": (total / len(per_request)) if per_request else 0.0,
    }


def _run_embed_bench(model, texts: List[str]) -> float:
    start = time.perf_counter()
    vectors = model.get_text_embedding_batch(texts, show_progress=False)
    elapsed = time.perf_counter() - start
    assert len(vectors) == len(texts), f"嵌入条数不符: {len(vectors)} != {len(texts)}"
    assert all(len(v) == EMBED_DIM for v in vectors), "向量维度不符"
    return elapsed


def _run_ingest_bench(module, model, texts: List[str], collection: str) -> float:
    from llama_index.core.schema import TextNode

    from app.core.config import settings

    db_config = {
        "host": settings.POSTGRES_SERVER,
        "user": settings.POSTGRES_USER,
        "password": settings.POSTGRES_PASSWORD,
        "database": settings.POSTGRES_DB,
        "port": settings.POSTGRES_PORT,
    }
    nodes = [TextNode(text=text, metadata={"collection": collection}) for text in texts]
    manager = module.VectorStoreManager(db_config)
    start = time.perf_counter()
    manager.upsert_chunks(nodes, collection, model)
    return time.perf_counter() - start


def _drop_table(collection: str) -> None:
    import psycopg2

    from app.core.config import settings

    conn = psycopg2.connect(
        host=settings.POSTGRES_SERVER,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS data_{collection} CASCADE')
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--impl", choices=["new", "legacy", "both"], default="both")
    parser.add_argument("--mode", choices=["embed", "ingest", "both"], default="both")
    parser.add_argument("--chunks", type=int, default=300)
    parser.add_argument("--latency-ms", type=int, default=50, help="假网关每请求固定延迟（模拟真实网关）")
    parser.add_argument("--rev", default="HEAD", help="legacy 实现所在的 git revision")
    parser.add_argument("--api-url", default=None, help="外部网关完整端点；给了就不启动假网关")
    parser.add_argument("--collection", default="issue35_bench")
    args = parser.parse_args()

    from app.adapters.doc_processing import embedding_store as new_impl

    gateway = None
    api_url = args.api_url
    if api_url is None:
        gateway, api_url = _start_gateway(args.latency_ms)
        print(f"[bench] 假网关: {api_url}  每请求延迟 {args.latency_ms}ms")
    else:
        print(f"[bench] 外部网关: {api_url}")

    impls = []
    legacy_tmp = None
    if args.impl in ("new", "both"):
        impls.append(("new", new_impl))
    if args.impl in ("legacy", "both"):
        legacy_impl, legacy_tmp = _load_legacy(args.rev)
        impls.append(("legacy", legacy_impl))

    texts = _chunk_texts(args.chunks)
    collections = {name: f"{args.collection}_{name}" for name, _ in impls}
    rows = []
    try:
        for name, module in impls:
            model = module.BGEM3EmbeddingWrapper(api_url=api_url)
            if args.mode in ("embed", "both"):
                _reset_requests()
                elapsed = _run_embed_bench(model, texts)
                rows.append({"impl": name, "mode": "embed", "sec": elapsed, **_stats()})
            if args.mode in ("ingest", "both"):
                collection = collections[name]
                _reset_requests()
                elapsed = _run_ingest_bench(module, model, texts, collection)
                rows.append({"impl": name, "mode": "ingest", "sec": elapsed, **_stats()})
    finally:
        for collection in collections.values():
            try:
                _drop_table(collection)
            except Exception as exc:  # noqa: BLE001  清理失败不影响结论
                print(f"[bench] 清理 {collection} 失败: {exc}", file=sys.stderr)
        if gateway is not None:
            gateway.shutdown()
        if legacy_tmp is not None:
            shutil.rmtree(legacy_tmp, ignore_errors=True)

    header = f"{'impl':8} {'mode':8} {'chunks':>7} {'http_req':>9} {'inputs/req':>11} {'elapsed_s':>10} {'chunks/s':>9}"
    print("\n" + header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['impl']:8} {row['mode']:8} {row['texts']:>7} {row['http_requests']:>9} "
            f"{row['avg_inputs_per_request']:>11.1f} {row['sec']:>10.3f} {row['texts'] / row['sec']:>9.1f}"
        )

    for mode in ("embed", "ingest"):
        pair = {row["impl"]: row for row in rows if row["mode"] == mode}
        if set(pair) == {"new", "legacy"}:
            speedup = pair["legacy"]["sec"] / pair["new"]["sec"]
            cut = 1 - pair["new"]["http_requests"] / pair["legacy"]["http_requests"]
            print(
                f"\n[{mode}] HTTP 请求 {pair['legacy']['http_requests']} → {pair['new']['http_requests']}"
                f"（-{cut:.1%}）；耗时 {pair['legacy']['sec']:.3f}s → {pair['new']['sec']:.3f}s"
                f"（快 {speedup:.1f}×）"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
