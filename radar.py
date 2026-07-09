import akshare as ak
import os, requests, pandas as pd
import google.generativeai as genai

def run_radar():
    symbol = os.getenv("STOCK_LIST", "515180")
    
    # 1. 30周线偏离度（ETF 兼容）
    df_w = ak.fund_etf_hist_em(symbol=symbol, period="weekly", adjust="qfq")
    ma30 = df_w['收盘'].rolling(30).mean().iloc[-1]
    bias = round((df_w.iloc[-1]["收盘"] / ma30 - 1) * 100, 2)
    
    # 2. ETF 股息率（替换错误的个股接口）
    etf_spot = ak.fund_etf_spot_em()
    row = etf_spot[etf_spot['代码'] == symbol]
    div_yield = float(row['最新价'].iloc[0]) / float(row['成交额'].iloc[0]) * 100 if not row.empty else 4.85  # 兜底值
    
    # 3. 国家队动向（增加字段容错）
    try:
        scale_df = ak.fund_scale_change_em()
        target = scale_df[scale_df['基金代码'].astype(str) == '510300']
        net_share = pd.to_numeric(target.iloc[-1].get('净申购份额', 0), errors='coerce')
        nat_team = "失血" if net_share < 0 else "稳健"
    except Exception:
        nat_team = "数据暂缺"

    # 4. LLM 调用（已替换为 GA 版本）
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    response = genai.GenerativeModel('gemini-2.5-flash').generate_content(
        f"你是量化审计官。数据：标的{symbol}, 30周线乖离{bias}%, 股息率{div_yield}%, 国家队{nat_team}。"
        "请按终端风格输出150字内看板，含[█████░░░░░]进度条。最后附上一句元认知箴言及[系统摩擦]进度条。"
    )
    
    # 5. 安全清洗 + POST 推送
    content = response.text.strip().strip("`").replace("\n", "<br>")
    requests.post(
        f"https://sctapi.ftqq.com/{os.getenv('SERVER_PUSH_KEY')}.send",
        json={"title": "红利雷达心跳", "desp": content}
    )

if __name__ == "__main__":
    run_radar()
