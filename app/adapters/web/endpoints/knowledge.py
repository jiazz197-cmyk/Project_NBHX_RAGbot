"""
知识库管理 Schema（data_knowledge_chunks）
"""
from typing import Optional

from pydantic import BaseModel, Field


class KnowledgeRecord(BaseModel):
    """data_knowledge_chunks 单条记录"""

    id: str
    text: str
    file_name: Optional[str] = None
    upload_time: Optional[str] = None
    uploader: str = ""
    status: str = "approved"


class KnowledgeRecordListResponse(BaseModel):
    """data_knowledge_chunks 列表响应"""

    success: bool = True
    total: int = 0
    records: list[KnowledgeRecord] = Field(default_factory=list)


class KnowledgeRecordDeleteResponse(BaseModel):
    """删除知识库记录响应"""

    success: bool = True
    message: str = "删除成功"
    deleted_id: str


class KnowledgeUploadResponse(BaseModel):
    """知识上传任务提交响应"""

    task_id: str
    status: str = "pending"
    message: str = ""
    files_count: int
    collection: str
