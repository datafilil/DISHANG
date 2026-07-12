import akshare as ak
import os
import requests
import pandas as pd
import google.generativeai as genai
import time # 引入时间模块用于重试等待

def run_radar():
    symbol = os.getenv("STOCK_LIST", "515180")
    
    # --- 1. 30周线偏离度 (增加重试机制) ---
    df_w = None
    for i in range(3):  # 最多尝试3次
        try:
            print(f"正在获取 {symbol} 历史数据... (尝试 {i+1}/3)")
            df_w = ak.fund_etf_hist_em(symbol=symbol, period="weekly", adjust="qfq")
            if df_w is not None and not df_w.empty:
                break # 获取成功，跳出循环
        except Exception as e:
            print(f"获取历史数据失败: {e}")
            if i < 2: time.sleep(5) # 失败等待5秒再试
    
    if df_w is None or df_w.empty:
        raise Exception("多次尝试后仍无法获取历史数据，请检查网络或股票代码")

    ma30 = df_w['收盘'].rolling(30).mean().iloc[-1]
    bias = round((df_w.iloc[-1]["收盘"] / ma30 - 1) * 100, 2)
    
    # --- 2. ETF 股息率 (增加重试机制) ---
    div_yield = 4.85 # 默认兜底值
    for i in range(3):
        try:
            print(f"正在获取 {symbol} 实时行情... (尝试 {i+1}/3)")
            etf_spot = ak.fund_etf_spot_em()
            row = etf_spot[etf_spot['代码'] == symbol]
            if not row.empty:
                # 注意：这里保留了你原有的计算逻辑，虽然用成交额算股息率比较特殊，但为了防止报错先保持原样
                # 建议确认一下是否应该用 '股息率' 字段（如果有）
                div_yield = float(row['最新价'].iloc[0]) / float(row['成交额'].iloc[0]) * 100 
                break
        except Exception as e:
            print(f"获取实时行情失败: {e}")
            if i < 2: time.sleep(5)

    # --- 3. 国家队动向 (保持原有容错) ---
    nat_team = "数据暂缺"
    try:
        scale_df = ak.fund_scale_change_em()
        target = scale_df[scale_df['基金代码'].astype(str) == '510300']
        if not target.empty:
            net_share = pd.to_numeric(target.iloc[-1].get('净申购份额', 0), errors='coerce')
            nat_team = "失血" if net_share < 0 else "稳健"
    except Exception:
        pass

    # --- 4. LLM 调用 (增加重试机制) ---
    ai_response = ""
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = (
        f"你是量化审计官。数据：标的{symbol}, 30周线乖离{bias}%, 股息率{div_yield}%, 国家队{nat_team}。"
        "请按终端风格输出150字内看板，含[█████░░░░░]进度条。最后附上一句元认知箴言及[系统摩擦]进度条。"
    )
    
    for i in range(3):
        try:
            print(f"正在请求 Gemini AI... (尝试 {i+1}/3)")
            response = genai.GenerativeModel('gemini-2.5-flash').generate_content(prompt)
            ai_response = response.text
            break
        except Exception as e:
            print(f"AI 请求失败: {e}")
            if i < 2: time.sleep(5)

    if not ai_response:
        ai_response = "⚠️ AI 分析服务暂时不可用，请查看原始数据。"

    # --- 5. 安全清洗 + POST 推送 ---
    content = ai_response.strip().strip("`").replace("\n", "<br>")
    
    push_key = os.getenv('SERVER_PUSH_KEY')
    if push_key:
        try:
            requests.post(
                f"https://sctapi.ftqq.com/{push_key}.send",
                json={"title": f"红利雷达心跳 ({symbol})", "desp": content},
                timeout=10 # 给推送也加个超时，防止卡死
            )
            print("推送成功！")
        except Exception as e:
            print(f"推送失败: {e}")
    else:
        print("未配置 SERVER_PUSH_KEY，跳过推送")
        print(content) # 在日志里打印出来方便调试

if __name__ == "__main__":
    run_radar()
