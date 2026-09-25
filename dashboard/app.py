"""Panel 股票 K 线图应用（示例）"""
import nest_asyncio2 as nest_asyncio #import nest_asyncio
nest_asyncio.apply()

import panel as pn
import plotly.graph_objects as go
import pandas as pd
import numpy as np

pn.extension('plotly')

# ---------- 示例数据：实际替换为 crawl4ai / tushare / akshare 获取 ----------
def load_sample_data(days: int = 100) -> pd.DataFrame:
    dates = pd.date_range(end=pd.Timestamp.today(), periods=days, freq='D')
    prices = np.random.randn(days).cumsum() + 100
    return pd.DataFrame({
        'date': dates,
        'open': prices + np.random.randn(days) * 0.5,
        'high': prices + np.random.randn(days) * 1.5,
        'low':  prices - np.random.randn(days) * 1.5,
        'close': prices + np.random.randn(days) * 0.5,
    })

# ---------- 控件 ----------
symbol_select = pn.widgets.Select(
    name='股票代码',
    options=['AAPL', 'GOOG', 'MSFT', 'TSLA'],
    value='AAPL',
)
days_slider = pn.widgets.IntSlider(
    name='显示天数', start=30, end=250, value=100, step=10,
)
refresh_btn = pn.widgets.Button(name='刷新数据', button_type='primary')

# ---------- 数据状态（Panel 的响应式模型） ----------
state = pn.state.as_cached('stock_state', lambda: {'df': load_sample_data(100)})

# ---------- 绘图函数 ----------
def make_candlestick(df: pd.DataFrame, symbol: str) -> go.Figure:
    fig = go.Figure(data=[go.Candlestick(
        x=df['date'],
        open=df['open'], high=df['high'],
        low=df['low'], close=df['close'],
        name=symbol,
    )])
    fig.update_layout(
        title=f'{symbol} K 线图',
        height=500,
        xaxis_rangeslider_visible=False,
        margin=dict(l=40, r=20, t=40, b=40),
    )
    return fig

# ---------- 响应式绑定 ----------
@pn.depends(symbol_select, days_slider, refresh_btn)
def chart_view(symbol, days, _):
    df = state['df'].tail(days)
    return pn.pane.Plotly(make_candlestick(df, symbol), config={'responsive': True})

# ---------- 布局 ----------
template = pn.template.MaterialTemplate(
    title='股票实时分析仪表板',
    sidebar=[
        pn.pane.Markdown('## 参数设置'),
        symbol_select,
        days_slider,
        refresh_btn,
    ],
)
template.main.append(chart_view)
template.servable()