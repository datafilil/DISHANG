import os
import time
import requests
import akshare as ak
import google.generativeai as genai
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def get_robust_session():
    """创建带真实浏览器UA、自动重试和连接池管理的健壮Session"""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1.5,
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

    # 🔑 核心修复：添加真实浏览器请求头，绕过GitHub Actions共享IP风控
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0"
    })
    return session


def safe_request(func, *args, retries=5, base_wait=2, **kwargs):
    """通用安全请求包装器，支持指数退避与详细日志"""
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

    # 🔧 全局注入健壮Session（覆盖akshare默认requests行为）
    robust_session = get_robust_session()
    original_get = requests.get
    original_post = requests.post

    def patched_get(url, **kwargs):
        kwargs.setdefault('timeout', 20)
        return robust_session.get(url, **kwargs)

    def patched_post(url, **kwargs):
        kwargs.setdefault('timeout', 20)
        return robust_session.post(url, **kwargs)

    requests.get = patched_get
    requests.post = patched_post

    try:
        # --- 1. 30周线偏离度 (使用 stock_zh_a_hist 替代 fund_etf_hist_em) ---
        df_w = safe_request(
            ak.stock_zh_a_hist,
            symbol=symbol, period="weekly", adjust="qfq"
        )
        if df_w is None:
            raise Exception(f"周线数据获取彻底失败，请检查网络或标的 {symbol}")

        ma30 = df_w['收盘'].rolling(30).mean().iloc[-1]
        bias = round((df_w.iloc[-1]["收盘"] / ma30 - 1) * 100, 2)

        # --- 2. ETF 实时行情 (增强容错 + 列名自适应) ---
        div_yield_str = "⚠️ 数据获取失败"
        etf_spot = safe_request(ak.fund_etf_spot_em)
        if etf_spot is not None:
            print(f"📋 实时行情可用列名: {list(etf_spot.columns)}")
            # 自适应列名匹配
            code_col = next((c for c in ['代码', '基金代码', 'code'] if c in etf_spot.columns), None)
            price_col = next((c for c in ['最新价', '现价', 'price'] if c in etf_spot.columns), None)
            vol_col = next((c for c in ['成交额', '成交金额', 'amount'] if c in etf_spot.columns), None)

            if code_col and price_col and vol_col:
                row = etf_spot[etf_spot[code_col].astype(str) == symbol]
                if not row.empty:
                    price = float(row[price_col].iloc[0])
                    volume = float(row[vol_col].iloc[0])
                    div_yield = round(price / volume * 100, 4) if volume > 0 else None
                    div_yield_str = f"{div_yield}%" if div_yield else "⚠️ 成交额为0"
                    print(f"✅ 股息率计算完成: {div_yield_str}")
            else:
                print(f"❌ 关键列缺失: code={code_col}, price={price_col}, vol={vol_col}")

        # --- 3. 国家队动向 (代理指标510300，带完整Fallback) ---
        nat_team = "⚠️ 暂缺"
        scale_df = safe_request(ak.fund_scale_change_em, retries=3, base_wait=3)
        if scale_df is not None:
            code_col = next((c for c in ['基金代码', '代码', 'fund_code'] if c in scale_df.columns), None)
            share_col = next((c for c in ['净申购份额', '净申购', 'net_share'] if c in scale_df.columns), None)
            if code_col and share_col:
                target = scale_df[scale_df[code_col].astype(str) == '510300']
                if not target.empty:
                    net_share = pd.to_numeric(target.iloc[-1][share_col], errors='coerce')
                    nat_team = "失血" if net_share < 0 else "稳健"
                    print(f"✅ 国家队状态: {nat_team} (净申购: {net_share})")
            else:
                print(f"❌ 国家队列缺失: code={code_col}, share={share_col}")
        else:
            print("⚠️ 国家队接口不可用，使用默认值")

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
            resp = requests.post(
                f"https://sctapi.ftqq.com/{push_key}.send",
                json={"title": f"红利雷达心跳 ({symbol})", "desp": content},
                timeout=15
            )
            print(f"📨 推送结果: {resp.status_code}")
        else:
            print("⚠️ 未配置 SERVER_PUSH_KEY，跳过推送")
            print(content)

    finally:
        # 🔒 恢复原始requests方法，避免污染全局
        requests.get = original_get
        requests.post = original_post


if __name__ == "__main__":
    import pandas as pd  # 延迟导入，确保在patch之后
    run_radar()
