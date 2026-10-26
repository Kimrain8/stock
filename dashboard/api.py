"""REST + WebSocket 示例端点"""
import asyncio
import json
import logging
import pandas as pd
from datetime import datetime,timezone

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException, Header
from starlette.concurrency import run_in_threadpool

from .config import settings
from utils.dbManager import MySQLHelper

logger = logging.getLogger("stock2.api")
router = APIRouter(prefix=settings.REST_API_PREFIX)


# ---------- WebSocket 连接管理 ----------
class ConnectionManager:
    def __init__(self):
        self._conns: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._conns.append(ws)
        logger.info("ws connected | total=%d", len(self._conns))

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self._conns:
                self._conns.remove(ws)
        logger.info("ws disconnected | total=%d", len(self._conns))

    async def broadcast(self, message: dict):
        dead = []
        for ws in list(self._conns):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)
    async def send_json(self, ws: WebSocket, data) -> bool:
        """单点发送；返回 False 表示连接已死并已被清理"""
        try:
            await ws.send_json(data)
            return True
        except Exception:
            await self.disconnect(ws)
            return False

manager = ConnectionManager()


# ---------- REST ----------
@router.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/me")
async def me(request: Request):
    """返回当前登录用户信息。
    request.state 是 Starlette 的 State 对象，用 getattr 读取。
    """
    user = getattr(request.state, "user", None)
    role = getattr(request.state, "role", None)
    dept = getattr(request.state, "dept", None)
    return {
        "user": user,
        "role": role,
        "dept": dept,
    }

@router.post("/qry2df")
async def qry2df(data: dict):
    sql = data.get("sql")
    if not sql:
        raise HTTPException(status_code=400, detail="SQL is required")
    try:
        db = MySQLHelper(db='jhd')
        df = await run_in_threadpool(db.qry2df, sql=sql)   # 阻塞调用丢线程池
        # 转换为 JSON（日期处理为字符串）
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------- WebSocket ----------
@router.websocket(settings.WS_API_SUBPATH)  # 相对前缀，完整路径 = /api/ws
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
            except Exception:
                data = {"raw": msg}

            # 回显 + 时间戳。真实场景可在此推送最新行情。
            await ws.send_json({
                "type": "echo",
                "recv": data,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)

@router.websocket("/ws/qry2df")            # 完整路径 = /api/ws/qry2df
async def ws_qry2df(ws: WebSocket):
    await manager.connect(ws)              # accept 在 manager 内完成，别重复 accept
    try:
        while True:
            msg = await ws.receive_text()

            # 1) 兼容纯 SQL 文本 / {"sql": ...} / {"raw": ...}
            try:
                payload = json.loads(msg)
            except Exception:
                payload = {"raw": msg}
            sql = payload.get("sql") or payload.get("raw")
            if not sql or not str(sql).strip():
                await ws.send_json({"type": "error", "error": "SQL is required"})
                continue                   # 不关连接，允许重发

            # 2) 同步 DB 丢线程池
            try:
                db = MySQLHelper(db="jhd")
                df = await run_in_threadpool(db.qry2df, sql=sql)

                # 3) 安全序列化：Timestamp/Decimal/NaN
                records = json.loads(
                    df.to_json(orient="records",
                               date_format="iso",
                               force_ascii=False)
                )
                await ws.send_json(records)        # 客户端期望直接是 list
            except Exception as e:
                await ws.send_json({"type": "error", "error": str(e)})

    except WebSocketDisconnect:
        pass
    except Exception as e:                     # 兜底：send_json 失败等
        logger.warning("[WS-ERROR] %s", e)
    finally:
        await manager.disconnect(ws)