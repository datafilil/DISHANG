"""
================================================================================
🛡️ 红利雷达 (radar.py) - 架构契约与迭代红线 (严禁删除/修改本注释块)
================================================================================

【核心防御与容错资产清单】(历史踩坑沉淀，重构时必须保留)
1. 数据源: BaoStock (免费/无风控/无UA校验/无IP封杀)，彻底替代akshare+东财。
2. 静态兜底机制 (bias=0.0): 数据源不可用时防止流程中断，确保LLM与推送走完。
3. AI 字段不可用强制占位符: Prompt 含防幻觉铁律，禁止AI将兜底值误判为真实低位。
4. Gemini SDK 旧版兼容 + 警告静默: 锁定 google-generativeai，屏蔽 FutureWarning。
5. 完整恢复版终端看板 Prompt: 五段式结构(结论→信号强度→拆解→箴言→系统摩擦)不可删减。
6. 通用安全请求包装器 (safe_request): 指数级退避重试，统一异常处理。

【后续迭代红线提示】
🚫 禁止回退akshare: BaoStock无风控，无需Session劫持/UA伪装/列名兼容。
🚫 禁止移除国家队降级标记: 无免费平替，必须保留显式降级，禁止AI脑补。
🚫 禁止再次精简 Prompt 结构: 五段式+双进度条是量化审计核心标识。
🚫 禁止升级 google-generativeai 大版本: 锁定当前版本，避免CI导入失败。

================================================================================
"""

import os
import time
import warnings
import baostock as bs
import pandas as pd
import google.generativeai as genai
from datetime import datetime, timedelta

# 🔑 屏蔽 Google SDK 废弃警告
warnings.filterwarnings("ignore", category=FutureWarning, module="google")


def safe_request(func, *args, retries=3, base_wait=3, **kwargs):
    """通用安全请求包装器：指数退避重试"""
    for i in range(retries):
        try:
            print(f"📡 {func.__name__}... ({i+1}/{retries})")
            result = func(*args, **kwargs)
            if result is not None:
                return result
        except Exception as e:
            wait = base_wait * (i + 1)
            print(f"❌ {func.__name__} 失败: {type(e).__name__}: {e}")
            print(f"⏳ {wait}s 后重试...")
            time.sleep(wait)
    return None


def fetch_weekly(symbol, weeks=35):
    """BaoStock拉取最近N周周线（多拉5周确保rolling(30)有值）"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=weeks * 7)).strftime("%Y-%m-%d")

    def _query():
        lg = bs.login()
        if lg.error_code != '0':
            raise ConnectionError(f"BaoStock登录失败: {lg.error_msg}")
        rs = bs.query_history_k_data_plus(
            symbol,
            "date,close,volume,amount",
            start_date=start, end_date=end,
            frequency="w", adjustflag="2"
        )
        rows = []
        while rs.error_code == '0' and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["date", "close", "volume", "amount"])
        df["close"] = df["close"].astype(float)
        df["amount"] = df["amount"].astype(float)
        return df

    df = safe_request(_query)
    if df is not None:
        print(f"✅ 获取 {len(df)} 根周K ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")
    return df


def run_radar():
    symbol = os.getenv("STOCK_LIST", "sz.515180")

    # --- 1. 周线乖离率 ---
    df_w = fetch_weekly(symbol)

    if df_w is None or len(df_w) < 30:
        print("⚠️ 周线数据不足，启用静态兜底")
        bias = 0.0
        current_close = 0.0
        amount_str = "⚠️ 数据获取失败"
    else:
        ma30 = df_w["close"].rolling(30).mean().iloc[-1]
        current_close = float(df_w["close"].iloc[-1])
        bias = round((current_close / ma30 - 1) * 100, 2)
        amount_str = f"{round(df_w['amount'].iloc[-1] / 1e8, 2)}亿元"
        print(f"📊 乖离率: {bias}% | 收盘: {current_close} | 成交额: {amount_str}")

    # --- 2. 国家队 (降级处理，无免费平替) ---
    nat_team = "⚠️ 数据源不可用，暂缺"

    # --- 3. LLM 分析 (完整恢复版终端看板 Prompt) ---
    ai_response = ""
    prompt = (
        f"你是量化审计官。标的: {symbol} (易方达中证红利ETF)\n\n"
        f"【输入数据】\n"
        f"- 30周乖离率: {bias}%\n"
        f"- 最新收盘价: {current_close}\n"
        f"- 本周成交额: {amount_str}\n"
        f"- 国家队动向: {nat_team}\n\n"
        "【输出规范】\n"
        "1. 严格采用终端ASCII看板风格，总字数控制在300字以内。\n"
        "2. 结构必须包含：[核心结论] → [信号强度 ████░░░░░░] → [技术面/资金面拆解] → [元认知箴言] → [系统摩擦 ░░░███████]。\n"
        "3. 先给出明确的多空/观望结论与信号强度进度条，再展开数据验证。\n\n"
        "【防幻觉铁律】\n"
        "⚠️ 若输入字段包含'⚠️'或'暂缺'，必须在对应分析模块明确标注'[数据源不可用]'，绝对禁止推测、脑补或使用历史常识替代。\n"
        "⚠️ 所有数值引用必须与输入完全一致，禁止四舍五入或二次计算。\n\n"
        "【元认知要求】\n"
        "在最后附上针对当前市场环境的元认知箴言（如周期错位、流动性陷阱等），"
        "并用[系统摩擦]进度条量化当前策略的执行阻力。"
    )

    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        for i in range(3):
            try:
                print(f"🤖 请求 Gemini AI... ({i+1}/3)")
                response = model.generate_content(prompt)
                ai_response = response.text
                break
            except Exception as e:
                print(f"❌ AI失败: {e}")
                if i < 2:
                    time.sleep(5 * (i + 1))
    else:
        print("⚠️ 未配置 GEMINI_API_KEY")

    if not ai_response:
        ai_response = f"⚠️ AI不可用 | 乖离{bias}% 价格{current_close} 国家队{nat_team}"

    # --- 4. 推送 ---
    content = ai_response.strip().strip("`")
    push_key = os.getenv("SERVER_PUSH_KEY")
    if push_key:
        import requests
        r = requests.post(
            f"https://sctapi.ftqq.com/{push_key}.send",
            json={"title": f"红利雷达({symbol})", "desp": content.replace("\n", "<br>")},
            timeout=15
        )
        print(f"📨 推送: {r.status_code}")
    else:
        print("\n" + content)


if __name__ == "__main__":
    run_radar()
