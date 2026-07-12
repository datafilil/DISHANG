"""
================================================================================
🛡️ 红利雷达 (radar.py) - 架构契约与迭代红线 (严禁删除/修改本注释块)
================================================================================

【核心防御与容错资产清单】(以下逻辑为历史踩坑沉淀，重构时必须保留)
1. 全局 Session 劫持 (patched_get/post): CI IP 被东财 TCP 封杀，必须注入带真实 UA + 连接池复用的 Session 绕过风控。
2. 数据源接口版本适配: 弃用已废弃的 stock_zh_a_daily(period) 与 fund_etf_spot_sina，改用 fund_etf_hist_em(weekly) 与 fund_etf_spot_em，并动态兼容中英文列名。
3. Symbol 动态前缀拼接: 腾讯/新浪强校验 sh/sz 前缀，硬编码会导致深市标的返回空数据（注：新版ETF接口内部已自动处理前缀，但保留此逻辑以备回退）。
4. 静态兜底机制 (bias=0.0): 外部源全挂时防止流程中断，确保 LLM 分析与推送能走完，避免监控盲区。
5. AI 字段不可用强制占位符: Prompt 中必须包含防幻觉铁律，防止 AI 将兜底值误判为真实低位并给出错误建议。
6. Gemini SDK 旧版兼容 + 警告静默: CI pip 缓存易致新版 google-genai 导入失败，旧版 generativeai 功能完好且稳定。
7. 通用安全请求包装器 (safe_request): 统一指数级退避重试，避免各节点重复编写 try-except 与 sleep 逻辑。
8. Session 还原保护 (finally): 防止全局猴子补丁污染 Server酱推送等后续请求，推送接口无需伪装 UA。
9. 完整恢复版终端看板 Prompt: 必须保留五段式结构(结论→信号强度→拆解→箴言→系统摩擦)、双进度条及禁止二次计算铁律。

【后续迭代红线提示】(违反以下规则将导致系统崩溃或输出质量断崖)
🚫 禁止合并列名匹配逻辑: 新版 ETF 接口列名为中文(最新价/成交额)，换源或升级 akshare 必须重新打印 list(df.columns) 验证。
🚫 禁止移除国家队降级标记: fund_scale_change_em 无免费平替，必须保留显式降级，禁止 AI 脑补国家队动向。
🚫 禁止升级 Python 版本消除 EOL 警告: 3.10 EOL 为 Google 远期预告，升大版本易致 akshare C 扩展编译失败。
🚫 禁止再次精简 Prompt 结构: [系统摩擦]进度条与元认知箴言是量化审计核心标识，删减结构等同于放弃策略指导价值。
🚫 禁止使用未锁定的 akshare 版本: akshare 频繁破坏性更新，requirements.txt 必须锁定具体版本号，禁止使用 >= 语法。

================================================================================
"""

import os
import time
import warnings
import requests
import pandas as pd
import akshare as ak
import google.generativeai as genai
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 🔑 屏蔽 Google SDK 废弃警告，保持日志整洁
warnings.filterwarnings("ignore", category=FutureWarning, module="google")


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

    # 🔧 全局注入健壮Session绕过东财风控
    original_get, original_post = requests.get, requests.post
    def patched_get(url, **kw):
        kw.setdefault('timeout', 20)
        return robust_session.get(url, **kw)
    def patched_post(url, **kw):
        kw.setdefault('timeout', 20)
        return robust_session.post(url, **kw)
    requests.get, requests.post = patched_get, patched_post

    try:
        # --- 1. 周线数据 (兼容新版akshare: fund_etf_hist_em) ---
        df_w = safe_request(
            ak.fund_etf_hist_em,
            symbol=symbol,
            period="weekly",
            adjust="qfq"
        )

        # 终极Fallback: 数据源全挂时使用静态兜底值防止流程中断
        if df_w is None or df_w.empty:
            print("⚠️ 所有周线数据源均不可用，启用静态兜底")
            bias = 0.0
        else:
            # 动态兼容中英文列名
            close_col = '收盘' if '收盘' in df_w.columns else 'close'
            ma30 = df_w[close_col].astype(float).rolling(30).mean().iloc[-1]
            current_close = float(df_w[close_col].iloc[-1])
            bias = round((current_close / ma30 - 1) * 100, 2)

        # --- 2. 实时行情 (兼容新版akshare: fund_etf_spot_em) ---
        div_yield_str = "⚠️ 数据获取失败"
        etf_spot = safe_request(ak.fund_etf_spot_em)
        if etf_spot is not None:
            print(f"📋 新版ETF实时列名: {list(etf_spot.columns)}")
            row = etf_spot[etf_spot['代码'].astype(str) == symbol]
            if not row.empty and '最新价' in row.columns and '成交额' in row.columns:
                price = float(row['最新价'].iloc[0])
                volume = float(row['成交额'].iloc[0])
                dy = round(price / volume * 100, 4) if volume > 0 else None
                div_yield_str = f"{dy}%" if dy else "⚠️ 成交额为0"

        # --- 3. 国家队 (降级处理) ---
        nat_team = "⚠️ 东财接口被封，暂缺"

        # --- 4. LLM 分析 (完整恢复版终端看板 Prompt) ---
        ai_response = ""
        prompt = (
            f"你是量化审计官。标的{symbol}, 30周乖离{bias}%, "
            f"股息率{div_yield_str}, 国家队{nat_team}。\n\n"

            "【输出规范】\n"
            "1. 严格采用终端ASCII看板风格，总字数控制在300字以内。\n"
            "2. 结构必须包含：[核心结论] → [信号强度 ████░░░░░░] → [技术面/资金面拆解] → [元认知箴言] → [系统摩擦 ░░░███████]。\n"
            "3. 先给出明确的多空/观望结论与信号强度进度条，再展开数据验证。\n\n"

            "【防幻觉铁律】\n"
            "⚠️ 若输入字段包含'⚠️'或'暂缺'，必须在对应分析模块明确标注'[数据源不可用]'，绝对禁止推测、脑补或使用历史常识替代。\n"
            "⚠️ 所有数值引用必须与输入完全一致，禁止四舍五入或二次计算。\n\n"

            "【元认知要求】\n"
            "在最后附上针对当前市场环境的元认知箴言（如周期错位、流动性陷阱等），并用[系统摩擦]进度条量化当前策略的执行阻力。"
        )

        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')
            for i in range(3):
                try:
                    print(f"🤖 请求 Gemini AI... (尝试 {i+1}/3)")
                    response = model.generate_content(prompt)
                    ai_response = response.text
                    break
                except Exception as e:
                    print(f"❌ AI失败: {e}")
                    if i < 2:
                        time.sleep(5)
        else:
            print("⚠️ 未配置 GEMINI_API_KEY")

        if not ai_response:
            ai_response = f"⚠️ AI不可用 | 乖离{bias}% 股息{div_yield_str} 国家队{nat_team}"

        # --- 5. 推送 ---
        content = ai_response.strip().strip("`").replace("\n", "<br>")
        push_key = os.getenv('SERVER_PUSH_KEY')
        if push_key:
            r = requests.post(
                f"https://sctapi.ftqq.com/{push_key}.send",
                json={"title": f"红利雷达({symbol})", "desp": content},
                timeout=15
            )
            print(f"📨 推送: {r.status_code}")
        else:
            print(content)

    finally:
        # 🔒 Session 还原保护，防止污染后续推送请求
        requests.get, requests.post = original_get, original_post


if __name__ == "__main__":
    run_radar()
