import time, math, asyncio, json, threading, csv, os 
from datetime import datetime, timedelta
from pocketoptionapi.stable_api import PocketOption
import pocketoptionapi.global_value as global_value
import talib.abstract as ta
import numpy as np
import pandas as pd
from collections import deque
import warnings
warnings.filterwarnings('ignore')

global_value.loglevel = 'INFO'

# Fixed missing closing quote
ssid = '42["auth",{"session":"4ma5jbmlr5s0tbk76gfojthdb3","isDemo":1,"uid":16781132,"platform":2}]'
demo = True

min_payout = 80
period = 60
expiration = 180
api = PocketOption(ssid, demo)
api.connect()
print("DEBUG: Called api.connect(), waiting for websocket...")

LOG_FILE = "trades_log.csv"

# Enhanced configuration for FCB strategy
FCB_CONFIG = {
    'fractal_period': 5,          # Period for fractal calculation
    'chaos_period': 13,           # Period for chaos oscillator
    'band_multiplier': 1.5,       # Multiplier for band width
    'confirmation_bars': 2,       # Bars for signal confirmation
    'volatility_filter': True,    # Enable volatility filtering
    'trend_filter': True,         # Enable trend filtering
    'min_volatility': 0.0001,     # Minimum volatility threshold
    'max_trades_per_pair': 3,     # Maximum concurrent trades per pair
    'cooldown_period': 300,       # Cooldown between trades (seconds)
}

# Global variables for enhanced strategy
pair_states = {}
trade_history = deque(maxlen=1000)
active_trades = {}

def log_trade(pair, direction, amount, expiration, last, result=None, strategy_data=None):
    """Enhanced trade logging with strategy-specific data"""
    log_data = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "pair": pair,
        "direction": direction,
        "amount": amount,
        "expiration": expiration,
        "signal": "Enhanced FCB",
        "price": round(last['close'], 5),
        "result": result if result else "pending",
        "fractal_upper": strategy_data.get('fractal_upper', 0) if strategy_data else 0,
        "fractal_lower": strategy_data.get('fractal_lower', 0) if strategy_data else 0,
        "chaos_osc": strategy_data.get('chaos_osc', 0) if strategy_data else 0,
        "volatility": strategy_data.get('volatility', 0) if strategy_data else 0,
        "trend_strength": strategy_data.get('trend_strength', 0) if strategy_data else 0
    }

    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, mode='a', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=log_data.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(log_data)

def get_payout():
    """Get available pairs with minimum payout requirement"""
    try:
        d = json.loads(global_value.PayoutData)
        valid_pairs = 0
        for pair in d:
            if len(pair) == 19 and pair[14] and pair[5] >= min_payout and "_otc" in pair[1]:
                global_value.pairs[pair[1]] = {
                    'id': pair[0],
                    'payout': pair[5],
                    'type': pair[3]
                }
                # Initialize pair state
                if pair[1] not in pair_states:
                    pair_states[pair[1]] = {
                        'last_trade_time': 0,
                        'active_trades': 0,
                        'consecutive_losses': 0,
                        'total_trades': 0
                    }
                valid_pairs += 1
        
        global_value.logger(f"Found {valid_pairs} valid pairs with minimum {min_payout}% payout", "INFO")
        return valid_pairs > 0
    except Exception as e:
        global_value.logger(f"Error getting payout data: {e}", "ERROR")
        return False

def get_df():
    """Fetch candle data for all pairs with improved error handling"""
    successful_pairs = 0
    try:
        for i, pair in enumerate(global_value.pairs):
            try:
                df = api.get_candles(pair, period)
                if df is not None and not df.empty:
                    successful_pairs += 1
                global_value.logger(f'{pair} ({i+1}/{len(global_value.pairs)}) - {"Success" if df is not None else "Failed"}', "INFO")
                time.sleep(1)  # Rate limiting
            except Exception as e:
                global_value.logger(f"Error fetching data for {pair}: {e}", "WARNING")
                continue
        
        global_value.logger(f"Successfully fetched data for {successful_pairs}/{len(global_value.pairs)} pairs", "INFO")
        return successful_pairs > 0
    except Exception as e:
        global_value.logger(f"Critical error in get_df: {e}", "ERROR")
        return False

def calculate_fractals(df, period=5):
    """Calculate fractal levels using Williams Fractals method"""
    if len(df) < period * 2 + 1:
        return np.nan, np.nan
    
    highs = df['high'].values
    lows = df['low'].values
    
    fractal_highs = []
    fractal_lows = []
    
    for i in range(period, len(df) - period):
        # Check for fractal high
        is_fractal_high = all(highs[i] >= highs[j] for j in range(i-period, i+period+1) if j != i)
        if is_fractal_high:
            fractal_highs.append((i, highs[i]))
        
        # Check for fractal low
        is_fractal_low = all(lows[i] <= lows[j] for j in range(i-period, i+period+1) if j != i)
        if is_fractal_low:
            fractal_lows.append((i, lows[i]))
    
    # Get most recent fractal levels
    recent_high = fractal_highs[-1][1] if fractal_highs else np.nan
    recent_low = fractal_lows[-1][1] if fractal_lows else np.nan
    
    return recent_high, recent_low

def calculate_chaos_oscillator(df, period=13):
    """Calculate Chaos Oscillator (AO - AC)"""
    try:
        # Awesome Oscillator
        ao = ta.AO(df)
        
        # Accelerator Oscillator (AC)
        ac = ao - ta.SMA(ao, timeperiod=5)
        
        # Chaos Oscillator
        chaos_osc = ao.iloc[-1] - ac.iloc[-1] if len(ao) > 0 and len(ac) > 0 else 0
        
        return chaos_osc
    except:
        return 0

def calculate_volatility(df, period=14):
    """Calculate normalized volatility using ATR"""
    try:
        atr = ta.ATR(df, timeperiod=period)
        current_price = df['close'].iloc[-1]
        volatility = atr.iloc[-1] / current_price if current_price > 0 else 0
        return volatility
    except:
        return 0

def calculate_trend_strength(df, period=20):
    """Calculate trend strength using ADX"""
    try:
        adx = ta.ADX(df, timeperiod=period)
        return adx.iloc[-1] if len(adx) > 0 else 0
    except:
        return 0

def enhanced_fcb_strategy(df, pair):
    """Enhanced Fractal Chaos Bands strategy with multiple confirmations"""
    if len(df) < 50:  # Need sufficient data
        return None, {}
    
    try:
        # Calculate fractal levels
        fractal_upper, fractal_lower = calculate_fractals(df, FCB_CONFIG['fractal_period'])
        
        if np.isnan(fractal_upper) or np.isnan(fractal_lower):
            return None, {}
        
        # Calculate Chaos Oscillator
        chaos_osc = calculate_chaos_oscillator(df, FCB_CONFIG['chaos_period'])
        
        # Calculate volatility
        volatility = calculate_volatility(df)
        
        # Calculate trend strength
        trend_strength = calculate_trend_strength(df)
        
        # Current price data
        current = df.iloc[-1]
        previous = df.iloc[-2] if len(df) > 1 else current
        
        # Create dynamic bands based on volatility
        band_width = (fractal_upper - fractal_lower) * FCB_CONFIG['band_multiplier']
        upper_band = fractal_upper + (band_width * volatility * 10)
        lower_band = fractal_lower - (band_width * volatility * 10)
        
        # Strategy data for logging
        strategy_data = {
            'fractal_upper': round(fractal_upper, 5),
            'fractal_lower': round(fractal_lower, 5),
            'chaos_osc': round(chaos_osc, 5),
            'volatility': round(volatility, 6),
            'trend_strength': round(trend_strength, 2),
            'upper_band': round(upper_band, 5),
            'lower_band': round(lower_band, 5)
        }
        
        # Signal generation with multiple confirmations
        signal = None
        
        # Primary condition: Price breakout
        price_above_upper = current['close'] > upper_band
        price_below_lower = current['close'] < lower_band
        
        # Confirmation filters
        volatility_ok = volatility >= FCB_CONFIG['min_volatility'] if FCB_CONFIG['volatility_filter'] else True
        trend_ok = trend_strength >= 25 if FCB_CONFIG['trend_filter'] else True
        
        # Momentum confirmation using Chaos Oscillator
        momentum_bullish = chaos_osc > 0
        momentum_bearish = chaos_osc < 0
        
        # Volume-like confirmation using price action
        volume_confirmation = abs(current['close'] - current['open']) > abs(previous['close'] - previous['open'])
        
        # Signal logic with confirmations
        if (price_above_upper and momentum_bullish and volatility_ok and 
            trend_ok and volume_confirmation):
            signal = "call"
        elif (price_below_lower and momentum_bearish and volatility_ok and 
              trend_ok and volume_confirmation):
            signal = "put"
        
        # Additional confirmation: Check for false breakouts
        if signal:
            # Look for sustained breakout over confirmation bars
            confirmation_count = 0
            for i in range(1, min(FCB_CONFIG['confirmation_bars'] + 1, len(df))):
                past_candle = df.iloc[-i]
                if signal == "call" and past_candle['close'] > fractal_upper:
                    confirmation_count += 1
                elif signal == "put" and past_candle['close'] < fractal_lower:
                    confirmation_count += 1
            
            if confirmation_count < FCB_CONFIG['confirmation_bars']:
                signal = None  # Not enough confirmation
        
        return signal, strategy_data
        
    except Exception as e:
        global_value.logger(f"Error in enhanced FCB strategy for {pair}: {e}", "ERROR")
        return None, {}

def can_trade_pair(pair):
    """Check if pair is eligible for trading based on risk management rules"""
    current_time = time.time()
    state = pair_states.get(pair, {})
    
    # Check cooldown period
    if current_time - state.get('last_trade_time', 0) < FCB_CONFIG['cooldown_period']:
        return False, "Cooldown period active"
    
    # Check maximum concurrent trades
    if state.get('active_trades', 0) >= FCB_CONFIG['max_trades_per_pair']:
        return False, "Maximum concurrent trades reached"
    
    # Check consecutive losses (optional risk management)
    if state.get('consecutive_losses', 0) >= 3:
        return False, "Too many consecutive losses"
    
    return True, "OK"

def buy(amount, pair, action, expiration, last, strategy_data):
    """Enhanced buy function with improved error handling and logging"""
    try:
        global_value.logger(f'Placing {action.upper()} on {pair} for ${amount} with {expiration}s expiration', "INFO")
        
        # Update pair state
        pair_states[pair]['active_trades'] += 1
        pair_states[pair]['last_trade_time'] = time.time()
        pair_states[pair]['total_trades'] += 1
        
        result = api.buy(amount=amount, active=pair, action=action, expirations=expiration)
        
        if result and len(result) > 1:
            trade_id = result[1]
            
            # Store trade for monitoring
            active_trades[trade_id] = {
                'pair': pair,
                'action': action,
                'amount': amount,
                'start_time': time.time(),
                'expiration': expiration
            }
            
            # Log trade immediately
            log_trade(pair, action, amount, expiration, last, "placed", strategy_data)
            
            # Monitor trade result in separate thread
            threading.Thread(target=monitor_trade, args=(trade_id, pair, action, amount, expiration, last, strategy_data)).start()
            
            print(
                f"TRADE_PLACED|{pair}|{action}|{amount}|{last['close']}|placed|"
                f"{strategy_data.get('fractal_upper', 0)}|{strategy_data.get('chaos_osc', 0)}|{strategy_data.get('volatility', 0)}"
            )
        else:
            global_value.logger(f'Failed to place trade on {pair}', "ERROR")
            pair_states[pair]['active_trades'] -= 1
            
    except Exception as e:
        global_value.logger(f'Error placing trade on {pair}: {e}', "ERROR")
        pair_states[pair]['active_trades'] -= 1

def monitor_trade(trade_id, pair, action, amount, expiration, last, strategy_data):
    """Monitor trade and update results"""
    try:
        # Wait for trade to complete
        time.sleep(expiration + 10)  # Add buffer time
        
        outcome = api.check_win(trade_id)
        
        # Update pair state
        pair_states[pair]['active_trades'] -= 1
        
        if outcome == "win":
            pair_states[pair]['consecutive_losses'] = 0
            global_value.logger(f'✅ WIN: {pair} {action.upper()} - ${amount}', "INFO")
        elif outcome == "loose":  # API uses "loose" instead of "lose"
            pair_states[pair]['consecutive_losses'] += 1
            global_value.logger(f'❌ LOSS: {pair} {action.upper()} - ${amount}', "INFO")
        else:
            global_value.logger(f'🔄 TIE: {pair} {action.upper()} - ${amount}', "INFO")
        
        # Log final result
        log_trade(pair, action, amount, expiration, last, outcome, strategy_data)
        
        # Remove from active trades
        if trade_id in active_trades:
            del active_trades[trade_id]
            
    except Exception as e:
        global_value.logger(f'Error monitoring trade {trade_id}: {e}', "ERROR")
        pair_states[pair]['active_trades'] -= 1

                # ...existing code...
        print(
            f"TRADE_PLACED|{pair}|{action}|{amount}|{last['close']}|placed|"
            f"{strategy_data.get('fractal_upper', 0)}|{strategy_data.get('chaos_osc', 0)}|{strategy_data.get('volatility', 0)}"
        )
        # ...existing code...

def make_df(df0, history):
    """Improved DataFrame creation with better error handling"""
    if not history or not isinstance(history, list):
        global_value.logger("No history data or invalid format. Skipping pair.", "WARNING")
        return pd.DataFrame()
    
    try:
        df1 = pd.DataFrame(history).reset_index(drop=True)
        
        if df1.empty:
            return pd.DataFrame()
            
        # Sort by time and set index
        df1 = df1.sort_values(by='time').reset_index(drop=True)
        df1['time'] = pd.to_datetime(df1['time'], unit='s')
        df1.set_index('time', inplace=True)

        # Resample to create OHLC data
        df = df1['price'].resample(f'{period}s').ohlc()
        
        if df.empty:
            return pd.DataFrame()
        
        df.reset_index(inplace=True)
        
        # Filter out future data
        current_time = datetime.fromtimestamp(wait(False))
        df = df[df['time'] < current_time]

        if df.empty:
            return pd.DataFrame()

        # Merge with existing data if available
        if df0 is not None and not df0.empty:
            # Combine dataframes and remove duplicates
            combined = pd.concat([df0, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=['time'], keep='last')
            combined = combined.sort_values(by='time').reset_index(drop=True)
            return combined.tail(200)  # Keep last 200 candles for memory efficiency
        
        return df.tail(200)
        
    except Exception as e:
        global_value.logger(f"Error creating DataFrame: {e}", "WARNING")
        return pd.DataFrame()

def strategie():
    """Enhanced strategy execution with improved signal processing"""
    signals_found = 0
    
    for pair in global_value.pairs:
        try:
            if 'history' not in global_value.pairs[pair]:
                continue
                
            # Check if pair can be traded
            can_trade, reason = can_trade_pair(pair)
            if not can_trade:
                global_value.logger(f"[{pair}] Skipping trade - {reason}", "DEBUG")
                continue
            
            history = global_value.pairs[pair]['history']
            existing_df = global_value.pairs[pair].get('dataframe', None)
            df = make_df(existing_df, history)
            
            if df.empty or len(df) < 50:
                global_value.logger(f"[{pair}] Insufficient data", "DEBUG")
                continue

            # Store updated dataframe
            global_value.pairs[pair]['dataframe'] = df
            
            # Apply enhanced FCB strategy
            signal, strategy_data = enhanced_fcb_strategy(df, pair)
            
            if signal:
                signals_found += 1
                global_value.logger(
                    f"[{pair}] 🎯 SIGNAL: {signal.upper()} | Price: {df.iloc[-1]['close']:.5f} | "
                    f"Chaos: {strategy_data.get('chaos_osc', 0):.3f} | "
                    f"Volatility: {strategy_data.get('volatility', 0):.4f} | "
                    f"Trend: {strategy_data.get('trend_strength', 0):.1f}", 
                    "INFO"
                )
                
                print(
                    f"SIGNAL_DETECTED|{pair}|{strategy_data.get('trend_strength', 0)}"
                )
                
                # Calculate dynamic trade amount based on confidence
                base_amount = 100
                confidence_multiplier = min(strategy_data.get('trend_strength', 25) / 25, 2.0)
                trade_amount = int(base_amount * confidence_multiplier)
                
                # Execute trade
                threading.Thread(
                    target=buy, 
                    args=(trade_amount, pair, signal, expiration, df.iloc[-1], strategy_data)
                ).start()
            else:
                global_value.logger(f"[{pair}] No signal - Price: {df.iloc[-1]['close']:.5f}", "DEBUG")
                
        except Exception as e:
            global_value.logger(f"Error processing {pair}: {e}", "ERROR")
            continue
    
    if signals_found > 0:
        global_value.logger(f"📊 Found {signals_found} trading signals this cycle", "INFO")

def prepare():
    """Prepare trading session with enhanced validation"""
    try:
        global_value.logger("🔄 Preparing trading session...", "INFO")
        
        payout_success = get_payout()
        if not payout_success:
            global_value.logger("❌ Failed to get payout data", "ERROR")
            return False
        
        df_success = get_df()
        if not df_success:
            global_value.logger("❌ Failed to get candle data", "ERROR")
            return False
        
        global_value.logger("✅ Trading session prepared successfully", "INFO")
        return True
        
    except Exception as e:
        global_value.logger(f"❌ Error preparing trading session: {e}", "ERROR")
        return False

def wait(sleep=True):
    """Fixed wait function with correct timing calculation"""
    now = datetime.now()
    # Calculate next minute boundary
    next_minute = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
    dt = int(next_minute.timestamp())
    
    if sleep:
        sleep_time = dt - int(now.timestamp())
        if sleep_time > 0:
            global_value.logger(f"⏰ Sleeping {sleep_time} seconds until next candle close...", "INFO")
            time.sleep(sleep_time)
    
    return dt

def start():
    """Enhanced start function with better connection handling and monitoring"""
    global_value.logger("🚀 Starting Enhanced FCB Trading Bot", "INFO")
    global_value.logger(f"📊 Strategy Configuration: {FCB_CONFIG}", "INFO")
    
    # Wait for WebSocket connection
    connection_timeout = 30
    start_time = time.time()
    
    while not global_value.websocket_is_connected:
        print("DEBUG: Waiting for websocket connection...")
        if time.time() - start_time > connection_timeout:
            global_value.logger("❌ WebSocket connection timeout", "ERROR")
            return
        time.sleep(0.5)
    
    time.sleep(2)  # Allow connection to stabilize
    
    try:
        # Get account balance
        balance = api.get_balance()
        global_value.logger(f'💰 Account Balance: ${balance}', "INFO")
        
        if not prepare():
            global_value.logger("❌ Failed to prepare trading session", "ERROR")
            return
        
        cycle_count = 0
        global_value.logger("🎯 Starting trading loop...", "INFO")
        
        while True:
            try:
                cycle_count += 1
                global_value.logger(f"📈 Trading Cycle #{cycle_count} - {datetime.now().strftime('%H:%M:%S')}", "INFO")
                
                # Execute strategy
                strategie()
                
                # Print active trades summary
                if active_trades:
                    global_value.logger(f"📊 Active trades: {len(active_trades)}", "INFO")
                
                # Wait for next cycle
                wait()
                
                # Periodic re-preparation (every 10 cycles)
                if cycle_count % 10 == 0:
                    global_value.logger("🔄 Refreshing market data...", "INFO")
                    prepare()
                    
            except KeyboardInterrupt:
                global_value.logger("🛑 Bot stopped by user", "INFO")
                break
            except Exception as e:
                global_value.logger(f"❌ Error in trading loop: {e}", "ERROR")
                time.sleep(5)  # Brief pause before retrying
                
    except Exception as e:
        global_value.logger(f"❌ Critical error in start function: {e}", "ERROR")

def log(level, message):
    print(f"LOG|{level}|{message}")
    global_value.logger(message, level)