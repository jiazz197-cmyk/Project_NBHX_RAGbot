import logging
import os
import uuid
from io import BytesIO
from typing import Dict, List, Optional, Union

from llama_index.core.schema import TextNode

from .doc_reader import DocumentProcessor
from .embedding_store import BGEM3EmbeddingWrapper, VectorStoreManager
from .exceptions import DocumentProcessingError
from .text_splitter import TagGenerator, TokenAwareTextSplitter, ExcelHeaderPreservingSplitter

from app.adapters.knowledge.constants import EXCEL_DB_COLLECTION_NAME
from app.core.time_utils import utcnow

logger = logging.getLogger(__name__)


FileInput = Union[str, os.PathLike, BytesIO]


def clean_text_for_postgres(text: str) -> str:
    """
    清理文本中的 NUL (0x00) 字符，PostgreSQL 不支持在文本字段中存储这些字符
    
    Args:
        text: 原始文本
        
    Returns:
        清理后的文本
    """
    if not text:
        return text
    # 移除 NUL 字符 (0x00)
    return text.replace('\x00', '')


class DocumentProcessingPipeline:
    """从文档到 PGVector 的最小可复用处理管线"""

    def __init__(
        self,
        db_config: Dict,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        device: str = "auto",
        num_tags: int = 5,
    ):
        self.db_config = db_config
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.num_tags = num_tags

        self.text_splitter = TokenAwareTextSplitter(chunk_size, chunk_overlap)
        # [note] 添加Excel专用分割器
        self.excel_splitter = ExcelHeaderPreservingSplitter(chunk_size, chunk_overlap)
        self.tag_generator = TagGenerator(device=device)
        self.document_processor = DocumentProcessor()
        # 嵌入模型改为调用远程 Docker 服务，通过环境变量配置接口地址和模型名
        self.embedding_model = BGEM3EmbeddingWrapper()
        self.vector_store_manager = VectorStoreManager(db_config)

    def _prepare_files(self, input_data: Union[FileInput, List[FileInput]]) -> List[FileInput]:
        if isinstance(input_data, (str, os.PathLike)):
            if os.path.isfile(input_data):
                return [str(input_data)]
            if os.path.isdir(input_data):
                allowed_ext = set(self.document_processor._parser_classes.keys())
                collected = []
                for root, _, files in os.walk(input_data):
                    for filename in files:
                        if filename.lower().split(".")[-1] in allowed_ext:
                            collected.append(os.path.join(root, filename))
                return collected
            logger.warning("指定路径不存在: %s", input_data)
            return []
        if isinstance(input_data, BytesIO):
            return [input_data]
        if isinstance(input_data, list):
            return input_data
        raise ValueError("input_data 只支持路径、数据流或它们的列表")

    def _documents_to_nodes(
        self,
        documents: List,
        collection: str,
        uploader: Optional[str] = None,
        upload_time: Optional[str] = None,
        minio_object_path: Optional[str] = None,
        file_id: Optional[int] = None,
    ) -> List[TextNode]:
        nodes = []
        for chunk in documents:
            # 清理文本中的 NUL 字符（PostgreSQL 不支持）
            cleaned_text = clean_text_for_postgres(chunk.page_content)
            
            # 清理 metadata 中的字符串值
            metadata = {}
            for key, value in chunk.metadata.items():
                if isinstance(value, str):
                    metadata[key] = clean_text_for_postgres(value)
                else:
                    metadata[key] = value
            
            metadata.setdefault("chunk_id", str(uuid.uuid4()))
            metadata["collection"] = collection
            # 同名预检 / 列表 / 删除都按 metadata 键读写（issue #3 P0 修复）：
            # 写入端必须落 uploader / upload_time（file_name 由 extract_metadata 落）。
            if uploader:
                metadata.setdefault("uploader", clean_text_for_postgres(uploader))
            if upload_time:
                metadata.setdefault("upload_time", upload_time)
            # issue #14：源文件的 MinIO 对象路径随 chunk 落库；检索端 get_charts
            # 凭它精确定位源文件，不再把 metadata['source']（裸文件名）当 object key。
            if minio_object_path:
                metadata.setdefault(
                    "minio_object_path", clean_text_for_postgres(minio_object_path)
                )
            if file_id is not None:
                metadata.setdefault("file_id", file_id)
            
            node = TextNode(
                text=cleaned_text,
                metadata=metadata,
            )
            nodes.append(node)
        return nodes

    def process(
        self,
        input_data: Union[FileInput, List[FileInput]],
        collection: str,
        uploader: Optional[str] = None,
        minio_object_path: Optional[str] = None,
        file_id: Optional[int] = None,
    ):
        """主入口：读取、切分、向量化并写入 PGVector（data_<collection>）。

        uploader 为本次上传者标识，写入每个 chunk 的 metadata（同名预检依赖它）。
        minio_object_path / file_id 为源文件在 MinIO / file_resource 表的定位信息
        （issue #14）：随 chunk metadata 落库，供检索端（get_charts）还原真实对象
        路径。调用方（document_task_runner）逐文件调用（单元素列表），故按标量
        传入并作用于本批全部文件，与 uploader 的传法一致。
        """
        files = self._prepare_files(input_data)
        if not files:
            logger.warning("未找到可处理的文件")
            return {"status": "empty"}

        # 同一批次（一个上传任务）的所有 chunk 使用同一个 upload_time
        upload_time = utcnow().isoformat()

        processed = 0
        # issue15 兜底：单文件解析失败不再静默吞掉（原来只写日志，任务仍报
        # “完成”，用户看不到任何原因）。逐文件登记失败原因，随返回值交给
        # document_task_runner 汇入任务状态反馈给用户。
        failed_files: List[Dict[str, str]] = []
        for file_path in files:
            try:
                # [note] 传递Excel专用分割器；excel-db 集合开启多 sheet 解析
                chunks = self.document_processor.process_document(
                    file_path,
                    self.text_splitter,
                    tag_generator=self.tag_generator,
                    num_tags=self.num_tags,
                    excel_splitter=self.excel_splitter,
                    excel_all_sheets=(collection == EXCEL_DB_COLLECTION_NAME),
                )
                if not chunks:
                    continue
                nodes = self._documents_to_nodes(
                    chunks,
                    collection,
                    uploader,
                    upload_time,
                    minio_object_path,
                    file_id,
                )
                self.vector_store_manager.upsert_chunks(nodes, collection, self.embedding_model)
                processed += 1
            except DocumentProcessingError as exc:
                logger.error("处理文件失败 %s: %s", file_path, exc)
                if isinstance(file_path, (str, os.PathLike)):
                    failed_name = os.path.basename(str(file_path))
                else:
                    failed_name = str(getattr(file_path, "name", "") or "未命名文件")
                failed_files.append({"file_name": failed_name, "error": str(exc)})
        return {
            "status": "success",
            "processed_files": processed,
            "total_files": len(files),
            "failed_files": failed_files,
        }

