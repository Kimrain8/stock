"""FastAPI 主入口：装配认证、日志、API、Panel"""
from string import Template
import logging

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import panel as pn
from panel.io.fastapi import add_applications

from .config import settings
from .logger import setup_logging
from .auth import authenticate, sessions
from .middleware import AuthMiddleware
from .api import router as api_router
from .explorer import build_explorer
from .trend import build_trend
from .scan import build_scan
from .analysis_page import build_analysis_page
from .data_explorer import build_data_explorer
from .data_workbench import build_data_workbench
from .data_studio import build_data_studio

setup_logging()
logger = logging.getLogger("stock2.server")


app = FastAPI(
    title="Stock2 Dashboard",
    docs_url=f"{settings.REST_API_PREFIX}/docs",
    redoc_url=None,
    openapi_url=f"{settings.REST_API_PREFIX}/openapi.json",
)

# 认证中间件：必须在 include_router 之前添加
app.add_middleware(AuthMiddleware)

# REST + WebSocket
app.include_router(api_router)


# ============================================================
# 登录页
# ============================================================
LOGIN_HTML = Template("""<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>Stock2 登录</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #f3f4f6;
           display: flex; align-items: center; justify-content: center;
           min-height: 100vh; margin: 0; }
    .card { background: #fff; padding: 32px 40px; border-radius: 10px;
            box-shadow: 0 4px 20px rgba(0,0,0,.08); width: 320px; }
    h1 { margin: 0 0 20px; font-size: 20px; color: #111; }
    input { width: 100%; padding: 10px 12px; margin-bottom: 12px;
            border: 1px solid #d1d5db; border-radius: 6px;
            font-size: 14px; box-sizing: border-box; }
    button { width: 100%; padding: 10px; background: #2563eb; color: #fff;
             border: none; border-radius: 6px; font-size: 15px;
             cursor: pointer; }
    button:hover { background: #1d4ed8; }
    .err { color: #dc2626; font-size: 13px; margin-bottom: 12px; }
  </style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>Stock2 数据平台</h1>
    $error
    <input name="username" placeholder="用户名" autocomplete="username" autofocus>
    <input name="password" type="password" placeholder="密码" autocomplete="current-password">
    <button type="submit">登 录</button>
  </form>
</body>
</html>""")

@app.get(settings.LOGIN_PATH, response_class=HTMLResponse)
async def login_page(request: Request):
    err = '<div class="err">用户名或密码错误</div>' if request.query_params.get("error") else ""
    return LOGIN_HTML.substitute(error=err)


@app.post(settings.LOGIN_PATH)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = authenticate(username, password)
    if not user:
        return RedirectResponse(f"{settings.LOGIN_PATH}?error=1", status_code=303)

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    token = sessions.create(
        username=user["username"],
        role=user.get("role", "user"),
        dept=user.get("dept") or "",
        ip=ip,
        user_agent=ua,
    )
    next_url = request.query_params.get("next", settings.HOME_PATH)

    resp = RedirectResponse(next_url, status_code=303)
    resp.set_cookie(
        settings.SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.USE_HTTPS,
        max_age=settings.SESSION_TTL_HOURS * 3600,
    )
    return resp


@app.get(settings.LOGOUT_PATH)
async def logout(request: Request):
    token = request.cookies.get(settings.SESSION_COOKIE)
    sessions.destroy(token)
    resp = RedirectResponse(settings.LOGIN_PATH, status_code=303)
    resp.delete_cookie(settings.SESSION_COOKIE)
    return resp


# ============================================================
# 首页
# ============================================================
HOME_HTML = Template("""<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>Stock2</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #f9fafb; padding: 40px;
           max-width: 1280px; margin: 0 auto; color: #111; }
    h1 { font-size: 26px; }
    /* ★ 一行 4 个卡片 */
    .grid { display: grid; grid-template-columns: repeat(4, 1fr);
            gap: 16px; margin-top: 24px; }
    .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
            padding: 20px; text-decoration: none; color: inherit;
            transition: .15s; }
    .card:hover { border-color: #2563eb; box-shadow: 0 4px 12px rgba(37,99,235,.1); }
    .card h2 { margin: 0 0 8px; font-size: 16px; }
    .card p { margin: 0; color: #6b7280; font-size: 13px; }
    .topbar { display: flex; justify-content: space-between; align-items: center; }
    .logout { color: #6b7280; font-size: 13px; }
    /* 小屏适配 */
    @media (max-width: 1024px) {
      .grid { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
  <div class="topbar">
    <h1>Stock2 数据平台</h1>
    <a class="logout" href="/logout">登出 ($user)</a>
  </div>
  <p>欢迎，$user。请选择要进入的模块：</p>
  <div class="grid">
    <a class="card" href="$explorer">
      <h2>📈 K线展示</h2>
      <p>K 线 + 成交量 + 换手率交互分析</p>
    </a>
    <a class="card" href="$trend">
      <h2>📉 趋势探索</h2>
      <p>均价区间 · 连续涨跌天数</p>
    </a>
    <a class="card" href="$scan">
      <h2>🔍 选股扫描</h2>
      <p>N 日内涨幅筛选 · 多规则组合</p>
    </a>
    <a class="card" href="$analysis">
      <h2>🧠 智能分析</h2>
      <p>4 Agent 协同 · 规则闭环学习</p>
    </a>
    <a class="card" href="$data_explorer">
      <h2>📊 数据探索器</h2>
      <p>SQL 查询 · 表格/图形展示 · 导出</p>
    </a>
    <a class="card" href="$workbench">
      <h2>🧪 数据工作台</h2>
      <p>IPython 风格 · 5 个数据源 · 代码操作 DataFrame</p>
    </a>
    <a class="card" href="$data_studio">
      <h2>🎨 数据可视化</h2>
      <p>拖拽式图表生成 · 自由布局</p>
    </a>
    <a class="card" href="$api_docs">
      <h2>🔌 API 文档</h2>
      <p>REST 与 WebSocket 接口调试</p>
    </a>
  </div>
  <script>
    const ws = new WebSocket(`$${location.protocol === 'https:' ? 'wss' : 'ws'}://$${location.host}$ws_path`);
    ws.onmessage = e => console.log('[WS]', e.data);
    function testWs() {
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({hello: 'from browser', t: Date.now()}));
      else alert('WebSocket 尚未连接');
    }
  </script>
</body>
</html>""")


@app.get(settings.HOME_PATH, response_class=HTMLResponse)
async def home(request: Request):
    user = getattr(request.state, "user", "unknown")
    return HOME_HTML.substitute(
        user=user,
        explorer=settings.EXPLORER_PATH,
        trend=getattr(settings, "TREND_PATH", "/trend"),
        scan=settings.SCAN_PATH,
        analysis=settings.ANALYSIS_PATH,
        data_explorer=settings.DATA_EXPLORER_PATH,
        workbench=settings.WORKBENCH_PATH,
        data_studio=settings.DATA_STUDIO_PATH,       # ← 新增
        api_docs=f"{settings.REST_API_PREFIX}/docs",
        ws_path=settings.WS_API_PATH,
    )


# ============================================================
# 权限管理端点（仅 admin）
# ============================================================
from .permissions import engine as perm_engine

def _require_admin(request: Request):
    if getattr(request.state, "role", None) != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None

@app.get(f"{settings.REST_API_PREFIX}/admin/permissions")
async def list_permissions(request: Request):
    err = _require_admin(request)
    if err:
        return err
    from utils.dbManager import MySQLHelper
    db = MySQLHelper(db=settings.DB_NAME)
    df = db.qry2df(
        "SELECT id, subject_type, subject, resource, resource_type, "
        "action, priority, is_active, description, update_time "
        "FROM permissions ORDER BY subject_type, subject, priority"
    )
    return df.to_dict(orient="records")

@app.post(f"{settings.REST_API_PREFIX}/admin/permissions/invalidate")
async def invalidate_permissions(
    request: Request,
    subject_type: str = Form(None),
    subject: str = Form(None),
):
    err = _require_admin(request)
    if err:
        return err
    perm_engine.invalidate(subject_type, subject)
    return {"ok": True, "scope": f"{subject_type}:{subject}" if subject else "all"}

@app.post(f"{settings.REST_API_PREFIX}/admin/sessions/purge")
async def purge_sessions(request: Request):
    err = _require_admin(request)
    if err:
        return err
    sessions.purge_expired()
    return {"ok": True}


# ============================================================
# 挂载 Panel 探索器
# ============================================================
explorer_app = build_explorer()
trend_app = build_trend()
scan_app = build_scan()
analysis_app = build_analysis_page()
data_explorer_app = build_data_explorer()
workbench_app = build_data_workbench()
data_studio_app = build_data_studio()

add_applications(
    {
        settings.EXPLORER_PATH: explorer_app,
        settings.TREND_PATH: trend_app,
        settings.SCAN_PATH: scan_app,
        settings.ANALYSIS_PATH: analysis_app,
        settings.DATA_EXPLORER_PATH: data_explorer_app,
        settings.WORKBENCH_PATH: workbench_app,
        settings.DATA_STUDIO_PATH: data_studio_app,   # ← 新增
    },
    app=app
)
logger.info("Panel mounted at %s", settings.EXPLORER_PATH)


# ============================================================
# 启动入口
# ============================================================
if __name__ == "__main__":
    try:
        sessions.purge_expired()
    except Exception as e:
        logger.warning("startup purge failed | err=%s", e)

    import uvicorn
    ssl_kwargs = {}
    if settings.USE_HTTPS:
        ssl_kwargs = {
            "ssl_certfile": settings.SSL_CERTFILE,
            "ssl_keyfile": settings.SSL_KEYFILE,
        }
        logger.info("HTTPS enabled: cert=%s", settings.SSL_CERTFILE)
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        access_log=False,
        **ssl_kwargs,
    )