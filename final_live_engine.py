import os
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import requests
import io
import warnings
import time
import alpaca_trade_api as tradeapi

warnings.filterwarnings('ignore')

print("🚀 [Live Quant Master V15.4] Occam's Razor: 단순한 알파 + 정교한 리스크 통제 (오리지널 복원)\n")

# =====================================================================
# [1] 시스템 글로벌 파라미터 
# =====================================================================
TEST_MODE = False           
AUM = 10_000_000            
REBALANCE_FREQ = 10         
MIN_TRADE_THRESHOLD = 0.005 
ATR_MULTIPLIER = 3.0        

MAX_TURNOVER = 0.35         
EXIT_RANK_QUANTILE = 0.3    

# =====================================================================
# [2] S&P 500 유니버스 및 섹터 스크래핑
# =====================================================================
print("🌍 S&P 500 유니버스 및 섹터 맵핑 중...")
def get_sp500_universe():
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers)
        df = pd.read_html(io.StringIO(response.text))[0]
        return dict(zip(df['Symbol'].astype(str).str.replace('.', '-'), df['GICS Sector'].astype(str)))
    except: return {}

sector_map = get_sp500_universe()
universe = list(sector_map.keys())[:50] if TEST_MODE else list(sector_map.keys())

# =====================================================================
# [3] 데이터 가공, 알파 팩터 및 리스크 데이터 생성 (오리지널 5년 지정 방식)
# =====================================================================
print("🏭 가격 데이터 가공 및 V15.1 핵심 팩터 생성 중 (약 5~10분 소요)...")
spy = yf.Ticker("SPY").history(period="5y").tz_localize(None)
spy.index.name = 'date' 
spy['spy_ret'] = spy['Close'].pct_change()

spy['spy_vol_20d'] = spy['spy_ret'].rolling(20).std() * np.sqrt(252)
spy['spy_vol_252d_mean'] = spy['spy_vol_20d'].rolling(252).mean()
spy['is_high_vol_regime'] = spy['spy_vol_20d'] > spy['spy_vol_252d_mean']

spy['spy_ma200'] = spy['Close'].rolling(200).mean()
spy['is_bear_market'] = spy['Close'] < spy['spy_ma200']

all_data = []
for i, ticker in enumerate(universe):
    try:
        if i % 50 == 0: print(f"   [{i}/{len(universe)}] {ticker} 데이터 가공 중...")
        df = yf.Ticker(ticker).history(period="5y").tz_localize(None).reset_index()
        if len(df) < 252: continue
        
        df.columns = [col.lower() for col in df.columns]
        df['ticker'] = ticker
        df['sector'] = sector_map.get(ticker, 'Unknown')
        df['corr_cluster'] = df['sector'] 
        
        df = df.sort_values('date').drop_duplicates(subset=['date']).reset_index(drop=True)
        df = df.merge(spy[['spy_ret', 'spy_vol_20d', 'is_high_vol_regime', 'is_bear_market']].reset_index(), on='date', how='left')
        
        df['target_1d'] = df['close'].shift(-1) / df['close'] - 1
        daily_ret = df['close'].pct_change()
        
        df['atr'] = (df['high'] - df['low']).rolling(14).mean()
        
        df['momentum_60d'] = df['close'].pct_change(60)
        df['quality_score'] = daily_ret.rolling(126).mean() / daily_ret.rolling(126).std()
        df['momentum_252d'] = df['close'].pct_change(252) 
        
        df['vol_20d'] = daily_ret.rolling(20).std() * np.sqrt(252)
        df['dollar_volume_20d'] = (df['close'] * df['volume']).rolling(20).mean()
        df['dollar_volume_60d'] = (df['close'] * df['volume']).rolling(60).mean() 
        
        cov = daily_ret.rolling(60).cov(df['spy_ret'])
        var = df['spy_ret'].rolling(60).var()
        df['beta_60d'] = cov / var
        df['idio_vol'] = (daily_ret - (df['beta_60d'] * df['spy_ret'])).rolling(60).std() * np.sqrt(252)
        
        features = ['momentum_60d', 'momentum_252d', 'quality_score', 'vol_20d', 
                    'beta_60d', 'idio_vol', 'dollar_volume_20d', 'dollar_volume_60d', 'atr']
        
        df[features] = df[features].shift(1) 
        df = df.dropna(subset=features + ['target_1d', 'spy_vol_20d'])
        all_data.append(df)
    except: continue

master_df = pd.concat(all_data, ignore_index=True)

z_factors = ['momentum_60d', 'quality_score']
def zscore_robust(x):
    c = x.clip(lower=x.quantile(0.02), upper=x.quantile(0.98))
    return (c - c.mean()) / c.std()

master_df[z_factors] = master_df.groupby('date')[z_factors].transform(zscore_robust)
master_df = master_df.dropna(subset=z_factors)

# =====================================================================
# [4] 포트폴리오 옵티마이저 함수
# =====================================================================
def optimize_portfolio(daily_data, AUM=10_000_000):
    df = daily_data.copy()
    df = df.drop_duplicates(subset=['ticker'])
    
    df['adv_ratio'] = df['dollar_volume_20d'] / df['dollar_volume_60d']
    df = df[(df['close'] > 5) & (df['dollar_volume_20d'] > 5_000_000) & (df['adv_ratio'] > 0.6)]
    if len(df) < 11: return pd.DataFrame() 

    df['alpha'] = (0.6 * df['momentum_60d'] + 0.4 * df['quality_score'])
    df['sector_alpha'] = df.groupby('sector')['alpha'].transform(lambda x: (x - x.mean()) / x.std()).fillna(0)
    df = df[df['momentum_252d'] > -0.5]

    long_universe = (
        df.sort_values("sector_alpha", ascending=False)
          .groupby("sector")
          .head(3)
          .copy()
    )

    long_universe['score'] = long_universe['sector_alpha'] / long_universe['vol_20d']
    min_score = long_universe['score'].min()
    if min_score < 0: long_universe['score'] = long_universe['score'] - min_score + 0.01

    long_universe['weight'] = long_universe['score'] / long_universe['score'].sum()

    MAX_WEIGHT = 0.10
    long_universe['weight'] = long_universe['weight'].clip(upper=MAX_WEIGHT)
    long_universe['weight'] /= long_universe['weight'].sum()

    ADV_LIMIT = 0.1
    long_universe['capital_alloc'] = long_universe['weight'] * AUM
    long_universe['capital_alloc'] = np.minimum(long_universe['capital_alloc'], long_universe['dollar_volume_20d'] * ADV_LIMIT)
    long_universe['weight'] = long_universe['capital_alloc'] / AUM

    portfolio = long_universe.copy()
    portfolio['market_impact_bps'] = (portfolio['vol_20d']/np.sqrt(252) * np.sqrt(portfolio['capital_alloc'] / portfolio['dollar_volume_20d']) * 10000)
    portfolio['transaction_cost_bps'] = portfolio['market_impact_bps'] + 1.5 

    if 'corr_cluster' in portfolio.columns:
        cluster_weight = portfolio.groupby('corr_cluster')['weight'].sum()
        MAX_CLUSTER = 0.3 
        for cluster in cluster_weight.index:
            if cluster_weight[cluster] > MAX_CLUSTER:
                scale = MAX_CLUSTER / cluster_weight[cluster]
                portfolio.loc[portfolio['corr_cluster'] == cluster, 'weight'] *= scale

    port_beta = (portfolio['weight'] * portfolio['beta_60d']).sum()
    market_vol = df['spy_vol_20d'].iloc[0]
    idio_var_sum = np.sum((portfolio['weight'] * portfolio['idio_vol'])**2)
    portfolio_vol = np.sqrt((port_beta * market_vol)**2 + idio_var_sum)

    if portfolio_vol > 0:
        TARGET_VOL = 0.10 if df['is_high_vol_regime'].iloc[0] else 0.18
        leverage = TARGET_VOL / portfolio_vol
        if df['is_bear_market'].iloc[0]: leverage *= 0.5 
        leverage = min(leverage, 1.0) 
        portfolio['weight'] *= leverage
        portfolio['capital_alloc'] *= leverage

    return portfolio[['ticker', 'sector', 'alpha', 'weight', 'capital_alloc', 'transaction_cost_bps']]

# =====================================================================
# [5] 시뮬레이션 및 체결 엔진
# =====================================================================
print("\n🛡️ 강력한 알파 엔진에 정교한 브레이크(Turnover 35%, Exit 30%)를 결합하여 시뮬레이션 중...")
trading_days = sorted(master_df['date'].unique())
rebalance_dates = set(trading_days[::REBALANCE_FREQ])

daily_results = []
current_weights = pd.Series(dtype=float)
trailing_stops = {} 

for date in trading_days:
    group = master_df[master_df['date'] == date]
    trade_costs = 0
    turnover = 0
    current_prices = group.set_index('ticker')['close']
    current_atrs = group.set_index('ticker')['atr']
    
    if not current_weights.empty:
        group['alpha'] = (0.6 * group['momentum_60d'] + 0.4 * group['quality_score'])
        group['sector_alpha'] = group.groupby('sector')['alpha'].transform(lambda x: (x - x.mean()) / x.std()).fillna(0)
        alpha_cutline = group['sector_alpha'].quantile(EXIT_RANK_QUANTILE)
        
        for ticker in list(current_weights.index):
            weight = current_weights[ticker]
            if weight > 0 and ticker in current_prices:
                exit_triggered = False
                
                if ticker in trailing_stops and current_prices[ticker] < trailing_stops[ticker]:
                    exit_triggered = True
                
                if ticker in group['ticker'].values:
                    ticker_alpha = group.loc[group['ticker'] == ticker, 'sector_alpha'].values[0]
                    if ticker_alpha < alpha_cutline:
                        exit_triggered = True
                        
                if exit_triggered:
                    trade_costs += abs(weight) * 0.0010 
                    current_weights[ticker] = 0.0 
                    if ticker in trailing_stops: del trailing_stops[ticker] 
                
                elif ticker in trailing_stops and ticker in current_atrs:
                    new_stop = current_prices[ticker] - (ATR_MULTIPLIER * current_atrs[ticker])
                    if new_stop > trailing_stops[ticker]:
                        trailing_stops[ticker] = new_stop
                    
    if date in rebalance_dates:
        opt_port = optimize_portfolio(group, AUM)
        if not opt_port.empty:
            target_weights = opt_port.set_index('ticker')['weight']
            all_tickers = list(set(current_weights.index).union(set(target_weights.index)))
            current_weights = current_weights.reindex(all_tickers).fillna(0)
            target_weights = target_weights.reindex(all_tickers).fillna(0)
            
            weight_diff = target_weights - current_weights
            weight_diff[abs(weight_diff) < MIN_TRADE_THRESHOLD] = 0
            actual_trades = weight_diff
            
            turnover = actual_trades.abs().sum() / 2.0
            
            if turnover > MAX_TURNOVER:
                scale = MAX_TURNOVER / turnover
                actual_trades *= scale
                turnover = MAX_TURNOVER 
            
            for ticker, delta_w in actual_trades.items():
                if abs(delta_w) > 0 and ticker in opt_port['ticker'].values:
                    bps_cost = opt_port.loc[opt_port['ticker'] == ticker, 'transaction_cost_bps'].values[0]
                    trade_costs += abs(delta_w) * (bps_cost / 10000)
            
            current_weights = current_weights + actual_trades
            
            active_tickers = current_weights[current_weights > 0].index
            for ticker in active_tickers:
                if ticker not in trailing_stops and ticker in current_prices and ticker in current_atrs:
                    trailing_stops[ticker] = current_prices[ticker] - (ATR_MULTIPLIER * current_atrs[ticker])
            
            tickers_to_remove = [t for t in list(trailing_stops.keys()) if t not in active_tickers]
            for t in tickers_to_remove: del trailing_stops[t]

    raw_ret = (current_weights * group.set_index('ticker')['target_1d']).sum() if not current_weights.empty else 0
    net_ret = raw_ret - trade_costs
    
    is_bear_flag = group['is_bear_market'].iloc[0] if not group.empty else False
    is_high_vol_flag = group['is_high_vol_regime'].iloc[0] if not group.empty else False
    
    daily_results.append({
        'date': date, 'net_return': net_ret, 'turnover': turnover, 
        'bear_market': is_bear_flag, 'high_vol': is_high_vol_flag
    })

# =====================================================================
# [6] 최종 리포팅
# =====================================================================
res_df = pd.DataFrame(daily_results).set_index('date')
cum_net = (1 + res_df['net_return']).cumprod()
sharpe = (res_df['net_return'].mean() / res_df['net_return'].std()) * np.sqrt(252) if len(res_df) > 0 else 0
cagr = (cum_net.iloc[-1] ** (252 / len(res_df))) - 1 if len(res_df) > 0 else 0
mdd = (cum_net / cum_net.cummax() - 1).min()

print("\n" + "="*55)
print("🏆 [V15.4 파이널: Occam's Razor - 진정한 실전형 스마트 베타]")
print("="*55)
print(f"✅ 연평균 수익률 (CAGR): {cagr * 100:.2f}% (비용 완전 차감 Net Return)")
print(f"✅ 연환산 샤프 지수 (Sharpe): {sharpe:.2f}")
print(f"✅ 최대 낙폭 (MDD): {mdd * 100:.2f}%")
print("="*55)

plt.figure(figsize=(12, 6))
plt.plot(cum_net, label='Live V15.4 Final (V15.1 Core + V15.3 Brakes)', color='indigo', linewidth=2)

high_vol_dates = res_df[res_df['high_vol']].index
for date in high_vol_dates: plt.axvspan(date, date + pd.Timedelta(days=1), color='yellow', alpha=0.1)

bear_dates = res_df[res_df['bear_market']].index
for date in bear_dates: plt.axvspan(date, date + pd.Timedelta(days=1), color='red', alpha=0.1)

plt.title('V15.4 Institutional Quant System - Yellow: High Vol, Red: Bear Market')
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig('v15_4_live_final.png')
print(" 'v15_4_live_final.png' 파일 생성 완료.")

# =====================================================================
# [7] Alpaca Paper Trading 자동 주문 전송 (Execution Bot)
# =====================================================================
print("\n 오늘 자 최종 포트폴리오 산출 및 주문 전송 준비...")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")          
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")    
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

try:
    api = tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, api_version='v2')
    
    # [안전장치] 주말/에러로 인해 묶인 기존 미체결 주문 싹 취소
    api.cancel_all_orders()
    time.sleep(2)
    print(" 기존 미체결 주문(Pending)을 모두 취소하여 Buying Power를 초기화했습니다.")

    account = api.get_account()
    equity = float(account.equity)
    print(f" Alpaca 계좌 연결 성공! 현재 총 자산(Equity): ${equity:,.2f}")

    last_date = master_df['date'].max()
    latest_data = master_df[master_df['date'] == last_date]
    
    target_port = optimize_portfolio(latest_data, AUM=equity)
    
    if target_port.empty:
        print("⚠️ 오늘 자 유효한 타겟 포트폴리오가 없습니다. (현금 보유 유지)")
        sells, buys = {}, {}
    else:
        target_weights = target_port.set_index('ticker')['weight']
        current_prices = latest_data.set_index('ticker')['close']
        
        target_shares = {}
        for ticker, weight in target_weights.items():
            if weight > 0 and ticker in current_prices:
                target_dollar = equity * weight
                shares = int(target_dollar / current_prices[ticker])
                if shares > 0:
                    target_shares[ticker] = shares
                    
        positions = api.list_positions()
        current_holdings = {p.symbol: float(p.qty) for p in positions}
        
        sells = {}
        buys = {}
        
        for ticker, current_qty in current_holdings.items():
            if ticker not in target_shares:
                sells[ticker] = current_qty 
            elif current_qty > target_shares[ticker]:
                sells[ticker] = current_qty - target_shares[ticker] 
                
        for ticker, t_qty in target_shares.items():
            current_qty = current_holdings.get(ticker, 0)
            if t_qty > current_qty:
                buys[ticker] = t_qty - current_qty
                
        print("\n [주문 생성 내역]")
        if not sells and not buys:
            print("   포트폴리오 변동 없음. 주문을 전송하지 않습니다.")
        else:
            for ticker, qty in sells.items():
                print(f"   🔴 매도 (SELL): {ticker} {qty}주")
                api.submit_order(symbol=ticker, qty=qty, side='sell', type='market', time_in_force='day')
                time.sleep(0.5) 
                
            for ticker, qty in buys.items():
                print(f"   🟢 매수 (BUY) : {ticker} {qty}주")
                api.submit_order(symbol=ticker, qty=qty, side='buy', type='market', time_in_force='day')
                time.sleep(0.5) 
                
            print("\n🎉 모든 주문 계산 및 실제 API 전송 완료!")

    # =====================================================================
    # [8] Telegram 메신저 일일 보고서 전송
    # =====================================================================
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        print("\n텔레그램으로 일일 보고서를 전송합니다...")
        
        msg = f"[V15.4 퀀트 봇 라이브 리포트]\n"
        msg += f" 현재 총 자산: ${equity:,.2f}\n"
        msg += "-" * 30 + "\n"
        
        if not sells and not buys:
            msg += " 오늘은 포트폴리오 변동(매매)이 없습니다."
        else:
            msg += " [오늘의 체결(예정) 내역]\n"
            for ticker, qty in sells.items():
                msg += f"🔴 매도: {ticker} {qty}주\n"
            for ticker, qty in buys.items():
                msg += f"🟢 매수: {ticker} {qty}주\n"
                
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        
        try:
            requests.post(url, json=payload)
            print("✅ 텔레그램 알림 전송 완료!")
        except Exception as e:
            print(f"⚠️ 텔레그램 전송 실패: {e}")

except Exception as e:
    print(f"\n❌ Alpaca API 연동 중 오류 발생: {e}")