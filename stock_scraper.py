import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# =========================================================================
# ⚙️ 安全第一・低速巡回設定
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

MIN_MARKET_CAP = 100_000_000      # $100M
MAX_MARKET_CAP = 1_500_000_000    # $1.5B

THEME_KEYWORDS = {
    "AI_DataCenter": ["data center", "liquid cooling", "hbm", "optical interconnect"],
    "Power_Infrastructure": ["electrical grid", "transformer", "substation", "power distribution"],
    "Semiconductor_Equipment": ["wafer", "lithography", "etching", "semiconductor packaging"],
    "Telecom": ["5g", "6g", "telecommunication", "fiber optic"],
    "Space": ["satellite", "low earth orbit", "aerospace", "payload"],
    "Defense": ["drone", "electronic warfare", "hypersonic", "missile", "defense contract"],
    "Energy_Security": ["lng", "natural gas", "grid resilience", "energy storage"],
    "SMR": ["small modular reactor", "nuclear", "reactor", "fission"],
    "Uranium": ["uranium", "u3o8", "yellowcake"],
    "Rare_Metal": ["rare earth", "critical mineral", "lithium", "neodymium"],
    "Quantum": ["quantum computing", "qubit", "quantum cryptography"],
    "BTC_System": ["bitcoin", "crypto mining", "hashrate", "asic"]
}

def get_sec_all_tickers():
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers)
        data = res.json()
        return [item["ticker"].upper() for item in data.values() if "." not in item["ticker"] and "-" not in item["ticker"]]
    except Exception as e:
        print(f"❌ SECからのティッカーリスト取得に失敗: {e}")
        return []

def get_or_create_sheet():
    secret_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    creds = Credentials.from_service_account_info(
        json.loads(secret_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows="2000", cols="6")
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        return ws

def main():
    tickers = get_sec_all_tickers()
    if not tickers:
        print("📭 スキャン対象のティッカーが空です。")
        return
        
    print(f"🐢 並列処理を全廃し、安全第一の低速シングルスレッドモードで全米スキャンを開始します...")
    discovered_gems = []
    current_date = time.strftime("%Y-%m-%d")
    
    # Yahooを騙すための標準ブラウザセッション
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    for count, ticker in enumerate(tickers, 1):
        try:
            stock = yf.Ticker(ticker, session=session)
            info = stock.info
            
            # 1. 時価総額フィルター
            market_cap = info.get("marketCap", 0)
            if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
                # 弾く場合も、相手のサーバーに負荷をかけないよう超微小なウェイト
                time.sleep(0.1)
                continue
                
            summary = info.get("longBusinessSummary", "").lower()
            if not summary:
                time.sleep(0.1)
                continue
            
            # 2. 12テーマの走査
            matched_theme = None
            for theme, keywords in THEME_KEYWORDS.items():
                if any(kw in summary for kw in keywords):
                    matched_theme = theme
                    break
            
            if matched_theme:
                print(f" ✨ 【原石発見】[{matched_theme}] {ticker} - ${market_cap/1e6:.1f}M")
                discovered_gems.append([
                    matched_theme,
                    ticker,
                    info.get("longName", ticker),
                    round(market_cap / 1_000_000, 2),
                    info.get("longBusinessSummary", ""),
                    current_date
                ])
            
            # 🌟 【最重要】人間と同じ速度に見せるため、1社終わるごとに「丸々1秒」確実に休む
            time.sleep(1.0)
            
        except Exception:
            # エラー銘柄は静かにスルーして1秒休む
            time.sleep(1.0)
            continue
            
        # 進行状況を100社ごとにログ出力
        if count % 100 == 0:
            print(f" 🟩 進捗: {count} / {len(tickers)} 社を安全に走査完了... (現在発見数: {len(discovered_gems)}件)")

    # 3. スプレッドシートへの書き込み（ガード付き）
    if len(discovered_gems) > 0:
        ws = get_or_create_sheet()
        ws.clear()
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print(f"🎉 処理完了！安全に全米を走破し、{len(discovered_gems)} 件の原石を縦型マッピングしました！")
    else:
        print("📭 条件に合致する銘柄が見つかりませんでした。")

if __name__ == "__main__":
    main()
