import akshare as ak
import os
import time
import pandas as pd
import google.generativeai as genai
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def get_robust_session():
    """创建带自动重试和连接池管理的 Session，解决 RemoteDisconnected"""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=20
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def run_radar():
    symbol = os.getenv("STOCK_LIST", "515180")

    # 🔧 注入健壮 Session 到 akshare
    robust_session = get_robust_session()
    ak.requests_cache = None
    try:
        import akshare.stock_feature.stock_zh_a_spot_em as spot_module
        spot_module.session = robust_session
    except Exception:
        pass

    # --- 1. 30周线偏离度 ---
    df_w = None
    for i in range(5):
        try:
            print(f"📡 获取 {symbol} 周线数据... (尝试 {i+1}/5)")
            df_w = ak.fund_etf_hist_em(symbol=symbol, period="weekly", adjust="qfq")
            if df_w is not None and not df_w.empty:
                print(f"✅ 周线数据获取成功，共 {len(df_w)} 条记录")
                break
        except Exception as e:
            wait_time = (i + 1) * 3
            print(f"❌ 获取失败: {type(e).__name__}: {e}")
            print(f"⏳ 等待 {wait_time}s 后重试...")
            time.sleep(wait_time)

    if df_w is None or df_w.empty:
        raise Exception(f"5次尝试后仍无法获取 {symbol} 历史数据，请检查网络或代码有效性")

    ma30 = df_w['收盘'].rolling(30).mean().iloc[-1]
    bias = round((df_w.iloc[-1]["收盘"] / ma30 - 1) * 100, 2)

    # --- 2. ETF 实时行情 (增强容错 + 调试日志) ---
    div_yield_str = "⚠️ 数据获取失败"
    for i in range(5):
        try:
            print(f"📡 获取 {symbol} 实时行情... (尝试 {i+1}/5)")
            etf_spot = ak.fund_etf_spot_em()
            print(f"📋 可用列名: {list(etf_spot.columns)}")

            row = etf_spot[etf_spot['代码'] == symbol]
            if row.empty:
                name_map = {"515180": "红利ETF", "510300": "沪深300ETF"}
                target_name = name_map.get(symbol)
                if target_name:
                    row = etf_spot[etf_spot['名称'].str.contains(target_name, na=False)]
                    if not row.empty:
                        print(f"✅ 通过名称模糊匹配成功: {row.iloc[0]['名称']}")

            if not row.empty:
                price = float(row['最新价'].iloc[0])
                volume = float(row['成交额'].iloc[0])
                div_yield = round(price / volume * 100, 4) if volume > 0 else None
                if div_yield is not None:
                    div_yield_str = f"{div_yield}%"
                    print(f"✅ 实时行情: 价格={price}, 成交额={volume}, 股息率={div_yield_str}")
                break
        except Exception as e:
            wait_time = (i + 1) * 3
            print(f"❌ 行情获取失败: {type(e).__name__}: {e}")
            time.sleep(wait_time)

    # --- 3. 国家队动向 ---
    nat_team = "⚠️ 暂缺"
    try:
        scale_df = ak.fund_scale_change_em()
        target = scale_df[scale_df['基金代码'].astype(str) == '510300']
        if not target.empty:
            net_share = pd.to_numeric(target.iloc[-1].get('净申购份额', 0), errors='coerce')
            nat_team = "失血" if net_share < 0 else "稳健"
    except Exception as e:
        print(f"⚠️ 国家队数据获取失败: {e}")

    # --- 4. LLM 调用 (300字 + 防幻觉) ---
    ai_response = ""
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = (
        f"你是量化审计官。数据：标的{symbol}, "
        f"30周线乖离{bias}%, "
        f"股息率{div_yield_str}, "
        f"国家队{nat_team}。"
        "请按终端风格输出300字内看板，含[█████░░░░░]进度条。"
        "要求：先给出核心结论与信号强度，再展开技术面与资金面分析。"
        "⚠️ 若任何字段包含'⚠️'或'暂缺'，请在看板中明确标注该数据不可用，不要推测或编造。"
        "最后附元认知箴言及[系统摩擦]进度条。"
    )

    for i in range(3):
        try:
            print(f"🤖 请求 Gemini AI... (尝试 {i+1}/3)")
            response = genai.GenerativeModel('gemini-2.5-flash').generate_content(prompt)
            ai_response = response.text
            break
        except Exception as e:
            print(f"❌ AI请求失败: {e}")
            if i < 2:
                time.sleep(5)

    if not ai_response:
        ai_response = (
            f"⚠️ AI分析不可用 | 原始数据: "
            f"乖离{bias}% 股息{div_yield_str} 国家队{nat_team}"
        )

    # --- 5. 推送 ---
    content = ai_response.strip().strip("`").replace("\n", "<br>")
    push_key = os.getenv('SERVER_PUSH_KEY')
    if push_key:
        try:
            resp = requests.post(
                f"https://sctapi.ftqq.com/{push_key}.send",
                json={"title": f"红利雷达心跳 ({symbol})", "desp": content},
                timeout=15
            )
            print(f"📨 推送结果: {resp.status_code}")
        except Exception as e:
            print(f"❌ 推送失败: {e}")
    else:
        print("⚠️ 未配置 SERVER_PUSH_KEY，跳过推送")
        print(content)


if __name__ == "__main__":
    run_radar()
