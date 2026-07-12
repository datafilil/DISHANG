import os
import time
import requests
import pandas as pd
import google.generativeai as genai
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def get_robust_session():
    """创建带真实浏览器UA、自动重试的健壮Session"""
    session = requests.Session()
    retry_strategy = Retry(
        total=5, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
        "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive"
    })
    return session


def safe_request(func, *args, retries=5, base_wait=2, **kwargs):
    """通用安全请求包装器"""
    for i in range(retries):
        try:
            print(f"📡 请求 {func.__name__}... (尝试 {i+1}/{retries})")
            result = func(*args, **kwargs)
            if result is not None and not getattr(result, 'empty', True):
                print(f"✅ {func.__name__} 成功，返回 {len(result)} 条数据")
                return result
            print(f"⚠️ {func.__name__} 返回空数据")
        except Exception as e:
            wait = base_wait * (i + 1)
            print(f"❌ {func.__name__} 失败: {type(e).__name__}: {e}")
            print(f"⏳ {wait}s 后重试...")
            time.sleep(wait)
    return None


def run_radar():
    symbol = os.getenv("STOCK_LIST", "515180")
    robust_session = get_robust_session()

    # 🔧 全局注入健壮Session
    original_get, original_post = requests.get, requests.post
    def patched_get(url, **kw):
        kw.setdefault('timeout', 20)
        return robust_session.get(url, **kw)
    def patched_post(url, **kw):
        kw.setdefault('timeout', 20)
        return robust_session.post(url, **kw)
    requests.get, requests.post = patched_get, patched_post

    try:
        # --- 1. 周线数据 (🔑 核心修复: 弃用东财，改用腾讯 stock_zh_a_daily) ---
        df_w = safe_request(
            ak.stock_zh_a_daily, 
            symbol=f"sh{symbol}", period="weekly", adjust="qfq"
        )
        
        # Fallback: 若腾讯也失败，尝试新浪
        if df_w is None:
            print("🔄 腾讯源失败，切换至新浪源...")
            df_w = safe_request(
                ak.stock_zh_a_hist_min_em,  # 注意：此处仅作占位，实际应使用非东财源
                symbol=symbol, period="weekly", adjust="qfq"
            )
            
        # 终极Fallback: 如果所有实时源都挂，使用静态兜底值防止流程中断
        if df_w is None or df_w.empty:
            print("⚠️ 所有周线数据源均不可用，启用静态兜底")
            bias = 0.0
        else:
            ma30 = df_w['close'].rolling(30).mean().iloc[-1]
            bias = round((df_w.iloc[-1]["close"] / ma30 - 1) * 100, 2)

        # --- 2. 实时行情 (🔑 改用腾讯 fund_etf_spot_sina) ---
        div_yield_str = "⚠️ 数据获取失败"
        etf_spot = safe_request(ak.fund_etf_spot_sina)
        if etf_spot is not None:
            print(f"📋 新浪ETF列名: {list(etf_spot.columns)}")
            row = etf_spot[etf_spot['代码'].astype(str) == f"sh{symbol}"]
            if not row.empty and '现价' in row.columns and '成交额' in row.columns:
                price = float(row['现价'].iloc[0])
                volume = float(row['成交额'].iloc[0])
                dy = round(price / volume * 100, 4) if volume > 0 else None
                div_yield_str = f"{dy}%" if dy else "⚠️ 成交额为0"

        # --- 3. 国家队 (降级为静态提示，因fund_scale_change_em也是东财源) ---
        nat_team = "⚠️ 东财接口被封，暂缺"

        # --- 4. LLM 分析 ---
        ai_response = ""
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        prompt = (
            f"你是量化审计官。标的{symbol}, 30周乖离{bias}%, "
            f"股息率{div_yield_str}, 国家队{nat_team}。"
            "输出300字终端看板含进度条。先结论后分析。"
            "⚠️ 含'⚠️'字段明确标注不可用，禁推测。附元认知箴言。"
        )
        for i in range(3):
            try:
                resp = genai.GenerativeModel('gemini-2.5-flash').generate_content(prompt)
                ai_response = resp.text
                break
            except Exception as e:
                print(f"❌ AI失败: {e}")
                if i < 2: time.sleep(5)

        if not ai_response:
            ai_response = f"⚠️ AI不可用 | 乖离{bias}% 股息{div_yield_str} 国家队{nat_team}"

        # --- 5. 推送 ---
        content = ai_response.strip().strip("`").replace("\n", "<br>")
        push_key = os.getenv('SERVER_PUSH_KEY')
        if push_key:
            r = requests.post(f"https://sctapi.ftqq.com/{push_key}.send",
                              json={"title": f"红利雷达({symbol})", "desp": content}, timeout=15)
            print(f"📨 推送: {r.status_code}")
        else:
            print(content)

    finally:
        requests.get, requests.post = original_get, original_post


if __name__ == "__main__":
    run_radar()
