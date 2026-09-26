"""REST + WebSocket 示例端点"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from .config import settings

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


manager = ConnectionManager()


# ---------- REST ----------
@router.get("/health")
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@router.get("/me")
async def me(request: Request):
    user = request.state.get("user") if hasattr(request, "state") else None
    return {"user": user}


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
                "ts": datetime.utcnow().isoformat(),
            })
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)