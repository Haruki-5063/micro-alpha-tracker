import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI

# =========================================================================
# ⚙️ 設定値（APIキー・スプレッドシート）
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
MASTER_SHEET_NAME = "Master_Watchlist"
ELITE_SHEET_NAME = "Elite_Watchlist"  # 厳選シート（新設タブ）

def get_google_sheets_client():
    secret_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    creds = Credentials.from_service_account_info(
        json.loads(secret_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    return gspread.authorize(creds)

def get_or_create_elite_sheet(sh):
    headers = [
        'Theme', 'Ticker', 'Company_Name', 'Value_Chain', 'Market_Cap_M', 
        'Volume_Ratio', 'Rev_YoY_Q0(最新)', 'Rev_YoY_Q1(-1Q)', 'Rev_YoY_Q2(-2Q)', 'Last_Updated'
    ]
    try:
        return sh.worksheet(ELITE_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=ELITE_SHEET_NAME, rows="1000", cols="10")
        ws.update(range_name='A1', values=[headers])
        return ws

def ask_ai_value_chain(ticker, summary):
    """LLMを用いて、事業概要からバリューチェーン上の位置付けを厳格にハルシネーション無しで判定する"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ OPENAI_API_KEY が未設定のため、AI判定をスキップします。")
        return "4_General/Unclassified"
        
    client = OpenAI(api_key=api_key)
    
    prompt = f"""
You are a cold, precise financial data analyst. Analyze the following US tech company's business summary and classify its position in the industry value chain.

[Target Company]
Ticker: {ticker}
Business Summary: {summary}

[Classification Rules]
- "1_Upstream (Raw/Fuel/Core)": Companies providing raw materials, mining, refining, uranium/critical minerals, nuclear fuel fabrication, or core foundational technologies.
- "2_Midstream (Equip/Component)": Companies manufacturing hardware components, industrial equipment, turbines, grid transformers, semiconductor wafers/etching tools, or satellite payloads.
- "3_Downstream (Utility/Service)": Companies providing end-user services, utilities, operating power generation plants, SaaS/software platforms, system integration, telecom services, or government defense contracts.

[Strict Constraints]
- Output ONLY the raw label string from the choices: "1_Upstream", "2_Midstream", or "3_Downstream".
- If it doesn't fit any or text is empty, output "4_General".
- Do not include any explanations, greetings, or punctuation. Your output must be exactly one of the labels.
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # コスト・速度のバランスが最強のモデル
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        result = response.choices[0].message.content.strip()
        return result if result in ["1_Upstream", "2_Midstream", "3_Downstream"] else "4_General"
    except Exception as e:
        print(f" 🚨 AI API エラー ({ticker}): {e}")
        return "4_General"

def fetch_financials_and_volume(ticker):
    """PER1000倍の赤字急成長株を炙り出すための尖った財務・需給解析"""
    try:
        stock = yf.Ticker(ticker)
        
        # 1. 需給：出来高比率（直近1日 vs 30日平均）
        history = stock.history(period="30d")
        volume_ratio = round(history['Volume'].iloc[-1] / history['Volume'].mean(), 2) if len(history) >= 2 else 1.0
            
        # 2. 財務（四半期決算・バランスシート）のハック
        q_financials = stock.quarterly_financials
        q_balance = stock.quarterly_balance_sheet
        
        # インデックス名から柔軟に行を取得
        rev_idx = [i for i in q_financials.index if "Revenue" in i]
        rd_idx = [i for i in q_financials.index if "Research" in i or "R&D" in i]
        net_inc_idx = [i for i in q_financials.index if "Net Income" in i]
        cash_idx = [i for i in q_balance.index if "Cash And Cash Equivalents" in i or "Cash" in i]
        
        rev_yoy_str = "N/A"
        rd_ratio_str = "N/A"
        cash_runway = "N/A"
        
        # 📈 売上高モメンタム加速率の算出 (古い順 ➔ 新しい順でpct_change)
        if rev_idx:
            rev_series = q_financials.loc[rev_idx[0]].dropna().iloc[::-1]
            if len(rev_series) >= 5:
                yoy_series = rev_series.pct_change(periods=4).iloc[::-1] # 最新順に戻す
                trends = []
                for i in range(min(3, len(yoy_series))):
                    val = yoy_series.iloc[i]
                    trends.append(f"{round(val * 100, 1)}%" if not os.sys.math.isnan(val) else "N/A")
                # 最新が右側に来るように並べ替え (例: "-10% -> 20% -> 55%")
                rev_yoy_str = " -> ".join(reversed(trends))

        # 🧪 R&D比率の算出 (直近四半期)
        if rev_idx and rd_idx:
            latest_rev = q_financials.loc[rev_idx[0]].iloc[0]
            latest_rd = q_financials.loc[rd_idx[0]].iloc[0]
            if latest_rev > 0 and not os.sys.math.isnan(latest_rd):
                rd_ratio_str = f"{round((abs(latest_rd) / latest_rev) * 100, 1)}%"

        # 🛡️ Cash Runway（生存可能四半期数）の算出
        if cash_idx and net_inc_idx:
            latest_cash = q_balance.loc[cash_idx[0]].iloc[0]
            latest_loss = q_financials.loc[net_inc_idx[0]].iloc[0]
            
            # 赤字（Net Incomeがマイナス）の場合のみRunwayを計算
            if latest_loss < 0 and not os.sys.math.isnan(latest_cash):
                runway_val = abs(latest_cash) / abs(latest_loss)
                cash_runway = f"{round(runway_val, 1)} Q"
            elif latest_loss >= 0:
                cash_runway = "Black (黒字)" # 黒字の場合は安全

        return volume_ratio, rev_yoy_str, rd_ratio_str, cash_runway
        
    except Exception as e:
        return 1.0, "Error", "Error", "Error"
def main():
    gc = get_google_sheets_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    
    # 1. 厳選シート（Elite）の読み込み
    elite_ws = get_or_create_sheet_elite(sh)
    elite_data = elite_ws.get_all_records()
    
    # 既存の登録状況（バリューチェーンの記憶）をマッピング
    # ➔ { 'RGTI': '2_Midstream', ... }
    existing_vc_map = {row['Ticker']: row['Value_Chain'] for row in elite_data if row['Ticker']}
    existing_rows_map = {row['Ticker']: row for row in elite_data if row['Ticker']}
    
    # 2. Masterシートの読み込み
    master_ws = sh.worksheet(MASTER_SHEET_NAME)
    master_records = master_ws.get_all_records()
    
    print(f"📋 厳選シートの既存数: {len(existing_vc_map)} 件 | Masterシートの母集団: {len(master_records)} 件")
    
    updated_elite_rows = []
    current_date = time.strftime("%Y-%m-%d")
    
    # 3. 各銘柄の精査（Masterシート基準でループ）
    for item in master_records:
        ticker = item.get("Ticker")
        if not ticker:
            continue
            
        print(f"🔍 銘柄スキャン中: {ticker}...")
        
        # 💡 【コアロジック】AIによるバリューチェーン確定は「初回（新規銘柄）のみ」
        if ticker in existing_vc_map and existing_vc_map[ticker] != "" and "4_General" not in existing_vc_map[ticker]:
            # 既存の判定を冷酷に再利用（APIを叩かない）
            vc_layer = existing_vc_map[ticker]
            print(f" ➔ 既存のValue Chainを再利用: {vc_layer}")
        else:
            # 未記入、または未分類の場合のみAIを召喚
            print(f" ➔ 💡 新規/未記入銘柄を特定。AI判定を実行します...")
            summary = item.get("Business_Summary", "")
            vc_layer = ask_ai_value_chain(ticker, summary)
            print(f" ➔ AI確定結果: {vc_layer}")
            
        # 💡 【コアロジック】決算推移と出来高は「全銘柄について毎回更新」
        print(f" ➔ 財務モメンタム ＆ 需給を解析中...")
        volume_ratio, rev_yoy_trends = fetch_financials_and_volume(ticker)
        
        # 最新のデータ構造に統合
        updated_elite_rows.append([
            item.get("Theme"),
            ticker,
            item.get("Company_Name"),
            vc_layer,
            item.get("Market_Cap_M"),
            volume_ratio,
            rev_yoy_trends[0],  # 最新四半期 YoY
            rev_yoy_trends[1],  # 1期前 YoY
            rev_yoy_trends[2],  # 2期前 YoY
            current_date
        ])
        
        # 相手サーバーへのマイルド・ウェイト
        time.sleep(1.0)
        
    # 4. 厳選シートへの出力
    if len(updated_elite_rows) > 0:
        elite_ws.clear()
        headers = [
            'Theme', 'Ticker', 'Company_Name', 'Value_Chain', 'Market_Cap_M', 
            'Volume_Ratio', 'Rev_YoY_Q0(最新)', 'Rev_YoY_Q1(-1Q)', 'Rev_YoY_Q2(-2Q)', 'Last_Updated'
        ]
        elite_ws.update(range_name='A1', values=[headers])
        elite_ws.append_rows(updated_elite_rows)
        print(f"🎉 成功！厳選シート（Elite_Watchlist）への最新データの同期が完了しました。")
    else:
        print("📭 同期するデータがありませんでした。")

if __name__ == "__main__":
    main()
