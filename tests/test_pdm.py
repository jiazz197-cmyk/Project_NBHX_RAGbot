"""Debug script: 查询 quotation_tasks 表中的 keywords_payload"""
import asyncio
import sys
import os

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.orm.quotation_task import QuotationTask


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(QuotationTask).where(QuotationTask.id == 214))
        task = result.scalars().first()

        if not task:
            print("❌ Task id=214 not found")

            print("\n最近的 5 个任务:")
            recent_result = await db.execute(
                select(QuotationTask).order_by(QuotationTask.id.desc()).limit(5)
            )
            recent = recent_result.scalars().all()
            for t in recent:
                print(f"  id={t.id}, task_id={t.task_id}, status={t.status}")
            return

        print("=" * 70)
        print(f"id:          {task.id}")
        print(f"task_id:     {task.task_id}")
        print(f"status:      {task.status}")
        print(f"progress:    {task.progress}")
        print(f"message:     {task.message}")
        print("=" * 70)

        if not task.result_payload:
            print("❌ result_payload is NULL")
            return

        print("\n📋 result_payload keys:")
        print(list(task.result_payload.keys()))

        keywords_payload = task.result_payload.get("keywords_payload")

        if not keywords_payload:
            print("\n❌ keywords_payload not found in result_payload")
            return

        print("\n✅ keywords_payload:")
        print(json.dumps(keywords_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
