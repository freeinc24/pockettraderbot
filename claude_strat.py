import time, math, asyncio, json, threading, configparser, os
from datetime import datetime
from pocketoptionapi.stable_api import PocketOption
import pocketoptionapi.global_value as global_value
import talib.abstract as ta
import numpy as np
import pandas as pd
import freqtrade.vendor.qtpylib.indicators as qtpylib

global_value.loglevel = 'INFO'

# Configuration and global variables
config = configparser.ConfigParser()
martingale_data = {}  # Track martingale state for each pair
session_results = {}  # Track session results

def load_config():
    """Load configuration from config.ini file"""
    global config
    
    if not os.path.exists('config.ini'):
        create_default_config()
    
    config.read('config.ini')
    
    # Validate configuration
    required_sections = ['TRADING', 'ACCOUNT', 'MARTINGALE']
    for section in required_sections:
        if not config.has_section(section):
            raise ValueError(f"Missing required section: {section}")
    
    return config

def create_default_config():
    """Create a default configuration file"""
    config = configparser.ConfigParser()
    
    config['ACCOUNT'] = {
        'ssid': '42["auth",{"session":"4ma5jbmlr5s0tbk76gfojthdb3","isDemo":1,"uid":16781132,"platform":2}]',
        'demo': 'yes',
        'min_payout': '80'
    }
    
    config['TRADING'] = {
        'strategy': '9',
        'amount': '100',
        'period': '30',
        'expiration': '60'
    }
    
    config['MARTINGALE'] = {
        'enabled': 'no',
        'multiplier': '2.0',
        'max_steps': '3',
        'reset_on_win': 'yes'
    }
    
    with open('config.ini', 'w') as configfile:
        config.write(configfile)
    
    print("Created default config.ini file. Please edit it with your settings.")

def get_config_value(section, key, fallback=None):
    """Get configuration value with fallback"""
    try:
        return config.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback

def get_config_bool(section, key, fallback=False):
    """Get boolean configuration value"""
    try:
        return config.getboolean(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback

def get_config_float(section, key, fallback=0.0):
    """Get float configuration value"""
    try:
        return config.getfloat(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback

def get_config_int(section, key, fallback=0):
    """Get integer configuration value"""
    try:
        return config.getint(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback

# Initialize configuration
load_config()

# Session configuration
start_counter = time.perf_counter()

# Load settings from config
ssid = get_config_value('ACCOUNT', 'ssid')
demo = get_config_bool('ACCOUNT', 'demo', True)
min_payout = get_config_int('ACCOUNT', 'min_payout', 80)
strategy_number = get_config_int('TRADING', 'strategy', 9)
base_amount = get_config_float('TRADING', 'amount', 100)
period = get_config_int('TRADING', 'period', 30)
expiration = get_config_int('TRADING', 'expiration', 60)

# Martingale settings
martingale_enabled = get_config_bool('MARTINGALE', 'enabled', False)
martingale_multiplier = get_config_float('MARTINGALE', 'multiplier', 2.0)
max_martingale_steps = get_config_int('MARTINGALE', 'max_steps', 3)
reset_on_win = get_config_bool('MARTINGALE', 'reset_on_win', True)

api = PocketOption(ssid, demo)

# Connect to API
api.connect()

def initialize_martingale(pair):
    """Initialize martingale data for a pair"""
    if pair not in martingale_data:
        martingale_data[pair] = {
            'step': 0,
            'current_amount': base_amount,
            'total_loss': 0,
            'last_trade_id': None,
            'waiting_result': False
        }

def calculate_martingale_amount(pair, is_loss=False):
    """Calculate the amount for martingale strategy"""
    initialize_martingale(pair)
    
    if not martingale_enabled:
        return base_amount
    
    data = martingale_data[pair]
    
    if is_loss and data['step'] < max_martingale_steps:
        data['step'] += 1
        data['current_amount'] *= martingale_multiplier
        data['total_loss'] += data['current_amount'] / martingale_multiplier
    elif not is_loss and reset_on_win:
        # Reset on win
        data['step'] = 0
        data['current_amount'] = base_amount
        data['total_loss'] = 0
    
    return data['current_amount']

def reset_martingale(pair):
    """Reset martingale data for a pair"""
    if pair in martingale_data:
        martingale_data[pair] = {
            'step': 0,
            'current_amount': base_amount,
            'total_loss': 0,
            'last_trade_id': None,
            'waiting_result': False
        }

def check_trade_results():
    """Check results of pending trades for martingale"""
    if not martingale_enabled:
        return
    
    for pair in martingale_data.copy():
        data = martingale_data[pair]
        if data['waiting_result'] and data['last_trade_id']:
            try:
                result = api.check_win(data['last_trade_id'])
                if result is not None:
                    data['waiting_result'] = False
                    
                    if result['win'] == 'win':
                        global_value.logger(f"Trade WON for {pair}: {result}", "INFO")
                        if reset_on_win:
                            reset_martingale(pair)
                    elif result['win'] == 'loose':
                        global_value.logger(f"Trade LOST for {pair}: {result}", "INFO")
                        # Next trade will use martingale amount
                    else:
                        global_value.logger(f"Trade result for {pair}: {result}", "INFO")
            except Exception as e:
                global_value.logger(f"Error checking trade result for {pair}: {e}", "ERROR")

def get_payout():
    try:
        d = global_value.PayoutData
        d = json.loads(d)
        for pair in d:
            if len(pair) == 19:
                global_value.logger('id: %s, name: %s, typ: %s, active: %s' % (str(pair[1]), str(pair[2]), str(pair[3]), str(pair[14])), "DEBUG")
                if pair[14] == True and pair[5] >= min_payout and "_otc" in pair[1]:
                    p = {}
                    p['id'] = pair[0]
                    p['payout'] = pair[5]
                    p['type'] = pair[3]
                    global_value.pairs[pair[1]] = p
        return True
    except:
        return False

def get_df():
    try:
        i = 0
        for pair in global_value.pairs:
            i += 1
            df = api.get_candles(pair, period)
            global_value.logger('%s (%s/%s)' % (str(pair), str(i), str(len(global_value.pairs))), "INFO")
            time.sleep(1)
        return True
    except:
        return False

def buy_with_martingale(amount, pair, action, expiration):
    """Enhanced buy function with martingale support"""
    initialize_martingale(pair)
    
    # Check if we have a pending result to process
    if martingale_enabled and martingale_data[pair]['waiting_result']:
        data = martingale_data[pair]
        if data['last_trade_id']:
            try:
                result = api.check_win(data['last_trade_id'])
                if result is not None:
                    data['waiting_result'] = False
                    if result['win'] == 'loose':
                        # Calculate martingale amount for next trade
                        amount = calculate_martingale_amount(pair, is_loss=True)
                    elif result['win'] == 'win':
                        # Reset martingale on win
                        if reset_on_win:
                            reset_martingale(pair)
                        amount = calculate_martingale_amount(pair, is_loss=False)
            except:
                pass
    
    # Use martingale amount if enabled
    if martingale_enabled:
        amount = martingale_data[pair]['current_amount']
    
    global_value.logger('%s, %s, %s, %s (Martingale Step: %s)' % 
                       (str(amount), str(pair), str(action), str(expiration), 
                        martingale_data[pair]['step'] if pair in martingale_data else 0), "INFO")
    
    try:
        result = api.buy(amount=amount, active=pair, action=action, expirations=expiration)
        if result and len(result) > 1:
            trade_id = result[1]
            if martingale_enabled:
                martingale_data[pair]['last_trade_id'] = trade_id
                martingale_data[pair]['waiting_result'] = True
            return trade_id
    except Exception as e:
        global_value.logger(f"Error placing trade: {e}", "ERROR")
    
    return None

def buy2(amount, pair, action, expiration):
    """Simple buy function without result checking"""
    # Apply martingale if enabled
    if martingale_enabled:
        initialize_martingale(pair)
        amount = martingale_data[pair]['current_amount']
    
    global_value.logger('%s, %s, %s, %s' % (str(amount), str(pair), str(action), str(expiration)), "INFO")
    try:
        result = api.buy(amount=amount, active=pair, action=action, expirations=expiration)
        if result and len(result) > 1 and martingale_enabled:
            trade_id = result[1]
            martingale_data[pair]['last_trade_id'] = trade_id
            martingale_data[pair]['waiting_result'] = True
        return result
    except Exception as e:
        global_value.logger(f"Error placing trade: {e}", "ERROR")
        return None

def make_df(df0, history):
    df1 = pd.DataFrame(history).reset_index(drop=True)
    df1 = df1.sort_values(by='time').reset_index(drop=True)
    df1['time'] = pd.to_datetime(df1['time'], unit='s')
    df1.set_index('time', inplace=True)

    df = df1['price'].resample(f'{period}s').ohlc()
    df.reset_index(inplace=True)
    df = df.loc[df['time'] < datetime.fromtimestamp(wait(False))]

    if df0 is not None:
        ts = datetime.timestamp(df.loc[0]['time'])
        for x in range(0, len(df0)):
            ts2 = datetime.timestamp(df0.loc[x]['time'])
            if ts2 < ts:
                df = df._append(df0.loc[x], ignore_index = True)
            else:
                break
        df = df.sort_values(by='time').reset_index(drop=True)
        df.set_index('time', inplace=True)
        df.reset_index(inplace=True)

    return df

def accelerator_oscillator(dataframe, fastPeriod=5, slowPeriod=34, smoothPeriod=5):
    ao = ta.SMA(dataframe["hl2"], timeperiod=fastPeriod) - ta.SMA(dataframe["hl2"], timeperiod=slowPeriod)
    ac = ta.SMA(ao, timeperiod=smoothPeriod)
    return ac

def DeMarker(dataframe, Period=14):
    dataframe['dem_high'] = dataframe['high'] - dataframe['high'].shift(1)
    dataframe['dem_low'] = dataframe['low'].shift(1) - dataframe['low']
    dataframe.loc[(dataframe['dem_high'] < 0), 'dem_high'] = 0
    dataframe.loc[(dataframe['dem_low'] < 0), 'dem_low'] = 0

    dem = ta.SMA(dataframe['dem_high'], Period) / (ta.SMA(dataframe['dem_high'], Period) + ta.SMA(dataframe['dem_low'], Period))
    return dem

def vortex_indicator(dataframe, Period=14):
    vm_plus = abs(dataframe['high'] - dataframe['low'].shift(1))
    vm_minus = abs(dataframe['low'] - dataframe['high'].shift(1))

    tr1 = dataframe['high'] - dataframe['low']
    tr2 = abs(dataframe['high'] - dataframe['close'].shift(1))
    tr3 = abs(dataframe['low'] - dataframe['close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    sum_vm_plus = vm_plus.rolling(window=Period).sum()
    sum_vm_minus = vm_minus.rolling(window=Period).sum()
    sum_tr = tr.rolling(window=Period).sum()

    vi_plus = sum_vm_plus / sum_tr
    vi_minus = sum_vm_minus / sum_tr

    return vi_plus, vi_minus

def supertrend(df, multiplier, period):
    df['TR'] = ta.TRANGE(df)
    df['ATR'] = ta.SMA(df['TR'], period)

    st = 'ST'
    stx = 'STX'

    # Compute basic upper and lower bands
    df['basic_ub'] = (df['high'] + df['low']) / 2 + multiplier * df['ATR']
    df['basic_lb'] = (df['high'] + df['low']) / 2 - multiplier * df['ATR']

    # Compute final upper and lower bands
    df['final_ub'] = 0.00
    df['final_lb'] = 0.00
    for i in range(period, len(df)):
        df['final_ub'].iat[i] = df['basic_ub'].iat[i] if df['basic_ub'].iat[i] < df['final_ub'].iat[i - 1] or df['close'].iat[i - 1] > df['final_ub'].iat[i - 1] else df['final_ub'].iat[i - 1]
        df['final_lb'].iat[i] = df['basic_lb'].iat[i] if df['basic_lb'].iat[i] > df['final_lb'].iat[i - 1] or df['close'].iat[i - 1] < df['final_lb'].iat[i - 1] else df['final_lb'].iat[i - 1]

    # Set the Supertrend value
    df[st] = 0.00
    for i in range(period, len(df)):
        df[st].iat[i] = df['final_ub'].iat[i] if df[st].iat[i - 1] == df['final_ub'].iat[i - 1] and df['close'].iat[i] <= df['final_ub'].iat[i] else \
                        df['final_lb'].iat[i] if df[st].iat[i - 1] == df['final_ub'].iat[i - 1] and df['close'].iat[i] >  df['final_ub'].iat[i] else \
                        df['final_lb'].iat[i] if df[st].iat[i - 1] == df['final_lb'].iat[i - 1] and df['close'].iat[i] >= df['final_lb'].iat[i] else \
                        df['final_ub'].iat[i] if df[st].iat[i - 1] == df['final_lb'].iat[i - 1] and df['close'].iat[i] <  df['final_lb'].iat[i] else 0.00
    # Mark the trend direction up/down
    df[stx] = np.where((df[st] > 0.00), np.where((df['close'] < df[st]), 'down',  'up'), np.NaN)

    # Remove basic and final bands from the columns
    df.drop(['basic_ub', 'basic_lb', 'final_ub', 'final_lb'], inplace=True, axis=1)

    df.fillna(0, inplace=True)

    return df

def execute_strategy(df, pair, strategy_num):
    """Execute the specified strategy"""
    
    if strategy_num == 9:
        # Strategy 9, period: 30
        heikinashi = qtpylib.heikinashi(df)
        df['open'] = heikinashi['open']
        df['close'] = heikinashi['close']
        df['high'] = heikinashi['high']
        df['low'] = heikinashi['low']
        df = supertrend(df, 1.3, 13)
        df['ma1'] = ta.EMA(df["close"], timeperiod=16)
        df['ma2'] = ta.EMA(df["close"], timeperiod=165)
        df['buy'], df['cross'] = 0, 0
        df.loc[(qtpylib.crossed_above(df['ST'], df['ma1'])), 'cross'] = 1
        df.loc[(qtpylib.crossed_below(df['ST'], df['ma1'])), 'cross'] = -1
        df.loc[(
                (df['STX'] == "up") &
                (df['ma1'] > df['ma2']) &
                (df['cross'] == 1)
            ), 'buy'] = 1
        df.loc[(
                (df['STX'] == "down") &
                (df['ma1'] < df['ma2']) &
                (df['cross'] == -1)
            ), 'buy'] = -1
        if df.loc[len(df)-1]['buy'] != 0:
            t = threading.Thread(target=buy2, args=(base_amount, pair, "call" if df.loc[len(df)-1]['buy'] == 1 else "put", expiration,))
            t.start()
    
    elif strategy_num == 8:
        # Strategy 8, period: 15
        df['ma1'] = ta.SMA(df["close"], timeperiod=7)
        df['ma2'] = ta.SMA(df["close"], timeperiod=9)
        df['ma3'] = ta.SMA(df["close"], timeperiod=14)
        df['buy'], df['ma13c'], df['ma23c'] = 0, 0, 0
        df.loc[(qtpylib.crossed_above(df['ma1'], df['ma3'])), 'ma13c'] = 1
        df.loc[(qtpylib.crossed_below(df['ma1'], df['ma3'])), 'ma13c'] = -1
        df.loc[(qtpylib.crossed_above(df['ma2'], df['ma3'])), 'ma23c'] = 1
        df.loc[(qtpylib.crossed_below(df['ma2'], df['ma3'])), 'ma23c'] = -1
        df.loc[(
                (df['ma23c'] == 1) &
                (
                    (df['ma13c'] == 1) |
                    (df['ma13c'].shift(1) == 1)
                )
            ), 'buy'] = 1
        df.loc[(
                (df['ma23c'] == -1) &
                (
                    (df['ma13c'] == -1) |
                    (df['ma13c'].shift(1) == -1)
                )
            ), 'buy'] = -1
        if df.loc[len(df)-1]['buy'] != 0:
            t = threading.Thread(target=buy2, args=(base_amount, pair, "call" if df.loc[len(df)-1]['buy'] == 1 else "put", expiration,))
            t.start()
    
    elif strategy_num == 5:
        # Strategy 5, period: 120
        heikinashi = qtpylib.heikinashi(df)
        df['open'] = heikinashi['open']
        df['close'] = heikinashi['close']
        df['high'] = heikinashi['high']
        df['low'] = heikinashi['low']
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(df), window=6, stds=1.3)
        df['bb_low'] = bollinger['lower']
        df['bb_mid'] = bollinger['mid']
        df['bb_up'] = bollinger['upper']
        df['macd'], df['macdsignal'], df['macdhist'] = ta.MACD(df['close'], 6, 19, 6)
        df['macd_cross'], df['hist_cross'], df['buy'] = 0, 0, 0
        df.loc[(
                (df['macd'].shift(1) < df['macdsignal'].shift(1)) &
                (df['macd'] > df['macdsignal'])
            ), 'macd_cross'] = 1
        df.loc[(
                (df['macd'].shift(1) > df['macdsignal'].shift(1)) &
                (df['macd'] < df['macdsignal'])
            ), 'macd_cross'] = -1
        df.loc[(
                (df['macdhist'].shift(1) < 0) &
                (df['macdhist'] > 0)
            ), 'hist_cross'] = 1
        df.loc[(
                (df['macdhist'].shift(1) > 0) &
                (df['macdhist'] < 0)
            ), 'hist_cross'] = -1
        df.loc[(
                (df['close'] > df['bb_up']) &
                (
                    (df['macd_cross'] == 1) |
                    (df['macd_cross'].shift(1) == 1) |
                    (df['macd_cross'].shift(2) == 1)
                ) &
                (
                    (df['hist_cross'] == 1) |
                    (df['hist_cross'].shift(1) == 1) |
                    (df['hist_cross'].shift(2) == 1)
                )
            ), 'buy'] = 1
        df.loc[(
                (df['close'] < df['bb_low']) &
                (
                    (df['macd_cross'] == -1) |
                    (df['macd_cross'].shift(1) == -1) |
                    (df['macd_cross'].shift(2) == -1)
                ) &
                (
                    (df['hist_cross'] == -1) |
                    (df['hist_cross'].shift(1) == -1) |
                    (df['hist_cross'].shift(2) == -1)
                )
            ), 'buy'] = -1
        if df.loc[len(df)-1]['buy'] != 0:
            t = threading.Thread(target=buy2, args=(base_amount, pair, "call" if df.loc[len(df)-1]['buy'] == 1 else "put", expiration,))
            t.start()
    
    # Add more strategies as needed...
    else:
        global_value.logger(f"Strategy {strategy_num} not implemented", "WARNING")

def strategie():
    # Check pending trade results for martingale
    check_trade_results()
    
    for pair in global_value.pairs:
        if 'history' in global_value.pairs[pair]:
            history = []
            history.extend(global_value.pairs[pair]['history'])
            if 'dataframe' in global_value.pairs[pair]:
                df = make_df(global_value.pairs[pair]['dataframe'], history)
            else:
                df = make_df(None, history)

            # Execute the configured strategy
            execute_strategy(df, pair, strategy_number)

            global_value.pairs[pair]['dataframe'] = df

def prepare_get_history():
    try:
        data = get_payout()
        if data: return True
        else: return False
    except:
        return False

def prepare():
    try:
        data = get_payout()
        if data:
            data = get_df()
            if data: return True
            else: return False
        else: return False
    except:
        return False

def wait(sleep=True):
    dt = int(datetime.now().timestamp()) - int(datetime.now().second)
    if period == 60:
        dt += 60
    elif period == 30:
        if datetime.now().second < 30: dt += 30
        else: dt += 60
        if not sleep: dt -= 30
    elif period == 15:
        if datetime.now().second >= 45: dt += 60
        elif datetime.now().second >= 30: dt += 45
        elif datetime.now().second >= 15: dt += 30
        else: dt += 15
        if not sleep: dt -= 15
    elif period == 10:
        if datetime.now().second >= 50: dt += 60
        elif datetime.now().second >= 40: dt += 50
        elif datetime.now().second >= 30: dt += 40
        elif datetime.now().second >= 20: dt += 30
        elif datetime.now().second >= 10: dt += 20
        else: dt += 10
        if not sleep: dt -= 10
    elif period == 5:
        if datetime.now().second >= 55: dt += 60
        elif datetime.now().second >= 50: dt += 55
        elif datetime.now().second >= 45: dt += 50
        elif datetime.now().second >= 40: dt += 45
        elif datetime.now().second >= 35: dt += 40
        elif datetime.now().second >= 30: dt += 35
        elif datetime.now().second >= 25: dt += 30
        elif datetime.now().second >= 20: dt += 25
        elif datetime.now().second >= 15: dt += 20
        elif datetime.now().second >= 10: dt += 15
        elif datetime.now().second >= 5: dt += 10
        else: dt += 5
        if not sleep: dt -= 5
    elif period == 120:
        dt = int(datetime(int(datetime.now().year), int(datetime.now().month), int(datetime.now().day), int(datetime.now().hour), int(math.floor(int(datetime.now().minute) / 2) * 2), 0).timestamp())
        dt += 120
    elif period == 180:
        dt = int(datetime(int(datetime.now().year), int(datetime.now().month), int(datetime.now().day), int(datetime.now().hour), int(math.floor(int(datetime.now().minute) / 3) * 3), 0).timestamp())
        dt += 180
    elif period == 300:
        dt = int(datetime(int(datetime.now().year), int(datetime.now().month), int(datetime.now().day), int(datetime.now().hour), int(math.floor(int(datetime.now().minute) / 5) * 5), 0).timestamp())
        dt += 300
    elif period == 600:
        dt = int(datetime(int(datetime.now().year), int(datetime.now().month), int(datetime.now().day), int(datetime.now().hour), int(math.floor(int(datetime.now().minute) / 10) * 10), 0).timestamp())
        dt += 600
    if sleep:
        global_value.logger('======== Sleeping %s Seconds ========' % str(dt - int(datetime.now().timestamp())), "INFO")
        return dt - int(datetime.now().timestamp())

    return dt

def print_config_summary():
    """Print current configuration summary"""
    global_value.logger("=== CONFIGURATION SUMMARY ===", "INFO")
    global_value.logger(f"Demo Mode: {'Yes' if demo else 'No'}", "INFO")
    global_value.logger(f"Strategy: {strategy_number}", "INFO")
    global_value.logger(f"Base Amount: {base_amount}", "INFO")
    global_value.logger(f"Period: {period}s", "INFO")
    global_value.logger(f"Expiration: {expiration}s", "INFO")
    global_value.logger(f"Min Payout: {min_payout}%", "INFO")
    global_value.logger(f"Martingale: {'Enabled' if martingale_enabled else 'Disabled'}", "INFO")
    if martingale_enabled:
        global_value.logger(f"Martingale Multiplier: {martingale_multiplier}x", "INFO")
        global_value.logger(f"Max Martingale Steps: {max_martingale_steps}", "INFO")
        global_value.logger(f"Reset on Win: {'Yes' if reset_on_win else 'No'}", "INFO")
    global_value.logger("=============================", "INFO")

def start():
    while global_value.websocket_is_connected is False:
        time.sleep(0.1)
    time.sleep(2)
    
    # Print configuration summary
    print_config_summary()
    
    saldo = api.get_balance()
    global_value.logger('Account Balance: %s' % str(saldo), "INFO")
    prep = prepare()
    if prep:
        while True:
            try:
                strategie()
                time.sleep(wait())
            except KeyboardInterrupt:
                global_value.logger("Bot stopped by user", "INFO")
                break
            except Exception as e:
                global_value.logger(f"Error in main loop: {e}", "ERROR")
                time.sleep(5)  # Wait 5 seconds before continuing

def start_get_history():
    while global_value.websocket_is_connected is False:
        time.sleep(0.1)
    time.sleep(2)
    saldo = api.get_balance()
    global_value.logger('Account Balance: %s' % str(saldo), "INFO")
    prep = prepare_get_history()
    if prep:
        i = 0
        for pair in global_value.pairs:
            i += 1
            global_value.logger('%s (%s/%s)' % (str(pair), str(i), str(len(global_value.pairs))), "INFO")
            if not global_value.check_cache(str(global_value.pairs[pair]["id"])):
                time_red = int(datetime.now().timestamp()) - 86400 * 7
                df = api.get_history(pair, period, end_time=time_red)


if __name__ == "__main__":
    start()
    end_counter = time.perf_counter()
    rund = math.ceil(end_counter - start_counter)
    # print(f'CPU-gebundene Task-Zeit: {rund} {end_counter - start_counter} Sekunden')
    global_value.logger("CPU-gebundene Task-Zeit: %s Sekunden" % str(int(end_counter - start_counter)), "INFO")