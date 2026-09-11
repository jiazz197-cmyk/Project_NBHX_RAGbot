import gc
import os
import threading
from typing import Optional, Dict, List

from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core import Settings

from app.core.logging import get_logger
from app.core.storage import save_file_from_minio
from app.adapters.ragsystem.data_analyze import excel_to_json
from app.adapters.ragsystem.RAGretriever import create_rag_retriever_system, HTTPReranker

logger = get_logger("ragsystem.retriever_for_yamato")


def format_docs(docs):
    return "\n\n".join(f"{doc.page_content}" for doc in docs)


class ModelManager:
    """Singleton for RAG retrievers, query engines, and HTTP reranker."""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._rag_system = None
        self._reranker = None
        self._retrievers_cache = {}
        self._query_engines_cache = {}
        self._cache_lock = threading.Lock()
        self._reranker_api_url = os.environ.get("RERANKER_API_URL", "http://localhost:8001/v1/rerank")
        self._initialized = True
        logger.info("模型管理器初始化完成，使用 HTTP API 模式")
    
    def set_rag_system(self, rag_system):
        """Attach shared RAGRetrieverSystem (first call wins)."""
        if self._rag_system is None:
            self._rag_system = rag_system
            logger.info("RAG系统已设置到模型管理器")
        else:
            logger.info("RAG系统已存在，跳过重复设置")
    
    def get_reranker(self):
        """Lazy-init HTTPReranker（线程安全）。"""
        if self._reranker is not None:
            return self._reranker
        with self._cache_lock:
            if self._reranker is not None:
                return self._reranker
            try:
                self._reranker = HTTPReranker(
                    api_url=self._reranker_api_url,
                    top_n=3,
                    timeout=30
                )
                logger.info("重排序器创建完成，API: %s", self._reranker_api_url)
            except Exception as e:
                logger.error("创建重排序器失败: %s", e)
                self._reranker = None
            return self._reranker
    
    def get_retriever(self, collection_name: str, top_k: int = 5):
        """Cached retriever per (collection, top_k)（线程安全）。"""
        cache_key = f"{collection_name}_{top_k}"
        
        existing = self._retrievers_cache.get(cache_key)
        if existing is not None:
            return existing
        
        if self._rag_system is None:
            raise ValueError("RAG系统未设置")
        
        retriever = self._rag_system.get_retriever_for_collection(collection_name, top_k=top_k)
        
        with self._cache_lock:
            if cache_key in self._retrievers_cache:
                return self._retrievers_cache[cache_key]
            self._retrievers_cache[cache_key] = retriever
            logger.debug("检索器缓存: %s", collection_name)
            return retriever
    
    def get_query_engine(self, collection_name: str, top_k: int = 5):
        """RetrieverQueryEngine with optional reranker; cached（线程安全）。"""
        cache_key = f"{collection_name}_{top_k}"
        
        existing = self._query_engines_cache.get(cache_key)
        if existing is not None:
            return existing
        
        retriever = self.get_retriever(collection_name, top_k)
        if retriever is None:
            return None
        
        reranker = self.get_reranker()
        query_engine = RetrieverQueryEngine.from_args(
            retriever=retriever,
            node_postprocessors=[reranker] if reranker else [],
            streaming=False,
        )
        
        with self._cache_lock:
            if cache_key in self._query_engines_cache:
                return self._query_engines_cache[cache_key]
            self._query_engines_cache[cache_key] = query_engine
            logger.debug("查询引擎缓存: %s", collection_name)
            return query_engine
    
    def get_available_collections(self) -> List[str]:
        """DB table names without data_ prefix."""
        if self._rag_system is None:
            return []
        
        try:
            collections = (
                self._rag_system.vector_store_manager.list_available_collections_sync()
            )
            return [col.replace("data_", "") for col in collections]
        except Exception as e:
            logger.error("获取collection列表失败: %s", e)
            return []
    
    def clear_cache(self):
        """Drop retriever/query-engine caches and run gc（线程安全）。"""
        logger.info("开始清理模型管理器缓存...")
        with self._cache_lock:
            self._retrievers_cache.clear()
            self._query_engines_cache.clear()
        gc.collect()
        logger.info("缓存清理完成")
    
    def get_memory_info(self) -> Dict:
        """Lightweight stats (HTTP mode, cache sizes)."""
        info = {
            "mode": "HTTP API",
            "retrievers_cached": len(self._retrievers_cache),
            "query_engines_cached": len(self._query_engines_cache),
            "reranker_api_url": self._reranker_api_url,
        }
        
        return info


class OptimizedRetriever:
    """Single- or multi-collection retrieval via ModelManager."""
    
    def __init__(self, rag_system=None, collection_name: Optional[str] = None):
        self.collection_name = collection_name
        
        if rag_system is None:
            raise ValueError("rag_system is required")
        
        self.model_manager = ModelManager()
        self.model_manager.set_rag_system(rag_system)
        
        self.default_top_n = getattr(rag_system, 'default_top_n', 3)
        logger.info("OptimizedRetriever 使用 top_n=%s", self.default_top_n)
        
        self._initialize_query_engines()
    
    def _initialize_query_engines(self):
        """Multi: defer engines; single: build one engine."""
        if self.collection_name is None:
            collections = self.model_manager.get_available_collections()
            logger.info("初始化全库检索，发现 %d 个collection", len(collections))
            
            self.query_engines = {}
            self.available_collections = collections
        else:
            query_engine = self.model_manager.get_query_engine(self.collection_name, top_k=5)
            if query_engine is None:
                raise ValueError(f"无法创建查询引擎: {self.collection_name}")
            self.query_engines = query_engine
            logger.info("单库检索模式初始化完成: %s", self.collection_name)
    
    def get_response(self, question: str, max_collections: int = 3) -> dict:
        """Return content/source lists (top 5 each)."""
        try:
            if self.collection_name is None:
                return self._get_multi_collection_response(question, max_collections)
            else:
                return self._get_single_collection_response(question)
        except Exception as e:
            logger.exception("检索响应失败: %s", e)
            return {
                "content": [f"检索失败: {str(e)}"],
                "source": ["error"]
            }
    
    def _get_single_collection_response(self, question: str) -> dict:
        raw_docs = self.query_engines.query(question)
        source_nodes = raw_docs.source_nodes
        
        logger.debug("检索到的文档块数量: %d", len(source_nodes))
        
        sources = []
        contents = []
        
        for i, node in enumerate(source_nodes):
            logger.debug(
                "  节点 %d - Score: %.4f - Source: %s",
                i + 1,
                node.score or 0.0,
                node.metadata.get('source', 'Unknown'),
            )
            source = node.metadata.get('source', 'Unknown')
            content = node.text.strip()
            sources.append(source)
            contents.append(content)
        
        return {
            "content": contents[:5],
            "source": sources[:5]
        }
    
    def _get_multi_collection_response(self, question: str, max_collections: int) -> dict:
        all_contents = []
        all_sources = []
        
        collections_to_query = self.available_collections[:max_collections]
        
        for collection_name in collections_to_query:
            try:
                if collection_name not in self.query_engines:
                    query_engine = self.model_manager.get_query_engine(collection_name, top_k=3)
                    if query_engine is not None:
                        self.query_engines[collection_name] = query_engine
                    else:
                        continue
                
                query_engine = self.query_engines[collection_name]
                raw_docs = query_engine.query(question)
                source_nodes = raw_docs.source_nodes
                
                max_results_per_collection = self.default_top_n if len(collections_to_query) == 1 else 2
                for node in source_nodes[:max_results_per_collection]:
                    source = node.metadata.get('source', f'Unknown_{collection_name}')
                    content = node.text.strip()
                    all_sources.append(source)
                    all_contents.append(content)
                    
            except Exception as e:
                logger.error("查询collection %s 失败: %s", collection_name, e)
                continue
        
        return {
            "content": all_contents[:5],
            "source": all_sources[:5]
        }
    
    def get_charts(self, question: str):
        """Resolve source path via retrieval, load from MinIO, excel_to_json."""
        try:
            response_filename = self.get_response(question)
            filename = response_filename["source"]
            
            if not filename or filename == ["error"]:
                return {"error": "未找到相关文件"}
            
            object_name = filename[0] if isinstance(filename, list) else filename
            file_path = save_file_from_minio(object_name)
            data_source = excel_to_json(file_path)
            return data_source
        except Exception as e:
            logger.exception("获取图表数据失败: %s", e)
            return {"error": str(e)}
    
    def cleanup(self):
        logger.info("开始清理retriever资源...")
        if hasattr(self, 'query_engines'):
            if isinstance(self.query_engines, dict):
                self.query_engines.clear()
            else:
                del self.query_engines
        
        self.model_manager.clear_cache()
        
        logger.info("Retriever资源清理完成")
    
    def get_memory_info(self) -> Dict:
        return self.model_manager.get_memory_info()
    
    def __del__(self):
        try:
            import sys
            if sys is None or sys.meta.path is None:
                return
            
            self.cleanup()
        except:
            pass


class retriever(OptimizedRetriever):
    """Backward-compatible alias."""
    pass


def cleanup_all_resources():
    try:
        manager = ModelManager()
        manager.clear_cache()
        logger.info("全局资源清理完成")
    except Exception as e:
        logger.error("全局资源清理失败: %s", e)


if __name__ == '__main__':
    try:
        Settings.llm = None
        
        rag_system = create_rag_retriever_system(
            host=os.getenv("RAG_DB_HOST", "localhost"),
            user=os.getenv("RAG_DB_USER", "postgres"),
            password=os.getenv("RAG_DB_PASSWORD", ""),
            database=os.getenv("RAG_DB_NAME", "postgres"),
            port=int(os.getenv("RAG_DB_PORT", "5432")),
            table_prefix=os.getenv("RAG_DB_TABLE_PREFIX", "doc_collection"),
            instance_id=1,
            default_top_k=20,
            default_top_n=3
        )
        retriever_instance = OptimizedRetriever(rag_system=rag_system)
        responses = retriever_instance.get_response("北部湾")
        logger.info("检索结果: %s", responses)
        
        logger.info("内存使用情况: %s", retriever_instance.get_memory_info())
        
        logger.info("请确保传入有效的rag_system实例")
        
    except Exception as e:
        logger.exception("执行失败: %s", e)
    finally:
        cleanup_all_resources()
