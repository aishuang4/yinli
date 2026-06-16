"""对话路由共享的 APIRouter 实例。

单独成模块,供 handlers 子包各端点 import 同一个 router,
避免在 __init__ 中产生循环导入。
"""

from fastapi import APIRouter

router = APIRouter()
