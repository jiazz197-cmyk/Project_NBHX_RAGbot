from typing import Any, Optional, Dict, List

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse


class FormatJSONResponse(JSONResponse):
    def __init__(self,
                 data: Any = None,
                 code: int = 200,
                 message: str = 'success',
                 status_code: int = 200,
                 headers: Optional[Dict[str, str]] = None,
                 background: Optional[BackgroundTask] = None
                 ) -> None:
        processed_data = self._add_statistics(data)
        content = jsonable_encoder({
            'code': code,
            'message': message,
            'data': processed_data
        })
        super().__init__(content=content, status_code=status_code,
                         headers=headers, media_type='application/json; charset=utf-8', background=background)

    def _add_statistics(self, data: Any) -> Any:
        if isinstance(data, list):
            return {
                'items': data,
                'total': len(data),
                'count': len(data)
            }
        elif isinstance(data, dict):
            if 'items' in data and 'total' in data:
                return data
            elif isinstance(data.get('data'), list):
                data['data'] = {
                    'items': data['data'],
                    'total': len(data['data']),
                    'count': len(data['data'])
                }
                return data
            else:
                return data
        else:
            return data

class Token(BaseModel):
    access_token: str
    token_type: str

class ChatRequest(BaseModel):

    question: str
    collection_name: Optional[str] = None
    # issue #16：改写步骤产出的结构化关键词。缺省空列表 = 纯向量检索，
    # 与既有调用方（及未升级的 RAG 容器）完全一致。
    keywords: List[str] = []

class ChartRequest(BaseModel):
    data_source: dict
    requirements: dict

class ChatSummaryRequest(BaseModel):
    """触发会话摘要的请求体。"""
    user_id: str
    conversation_id: str
    limit: Optional[int] = 20

class ChatSummaryResponse(BaseModel):
    """摘要任务结果。"""
    user_id: str
    conversation_id: str
    query_count: int
    previous_summary: Optional[str] = None
    new_summary: Optional[str] = None
    is_first_time: bool
    db_updated: bool

class UserSummaryResponse(BaseModel):
    """用户最新摘要查询结果。"""
    user_id: str
    latest_summary: Optional[str] = None
    exists: bool