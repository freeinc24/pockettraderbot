# dashboard_server.py
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import threading
import time
import json
import csv
import os
from datetime import datetime, timedelta
import pandas as pd
from collections import deque
import subprocess
import sys
import signal
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables for bot management
bot_process = None
bot_running = False
bot_stats = {
    'total_trades': 0,
    'wins': 0,
    'losses': 0,
    'daily_pnl': 0.0,
    'signals_today': 0,
    'balance': 1000.0,
    'active_trades': 0,
    'api_connected': False
}

# Configuration
bot_config = {
    'fractal_period': 5,
    'chaos_period': 13,
    'band_multiplier': 1.5,
    'min_payout': 80,
    'expiration': 180,
    'max_trades_per_pair': 3,
    'cooldown_period': 300,
    'trade_amount': 100
}

# Store recent trades and logs
recent_trades = deque(maxlen=100)
recent_logs = deque(maxlen=200)

# File paths
LOG_FILE = "trades_log.csv"
CONFIG_FILE = "bot_config.json"
BOT_SCRIPT = "fcb_trading_bot.py"  # Your main bot script

def load_config():
    """Load bot configuration from file"""
    global bot_config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                bot_config.update(json.load(f))
        logger.info(f"Configuration loaded: {bot_config}")
    except Exception as e:
        logger.error(f"Error loading config: {e}")

def save_config():
    """Save bot configuration to file"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(bot_config, f, indent=2)
        logger.info("Configuration saved successfully")
    except Exception as e:
        logger.error(f"Error saving config: {e}")

def load_trades_from_csv():
    """Load existing trades from CSV log file"""
    global recent_trades, bot_stats
    
    if not os.path.exists(LOG_FILE):
        return
    
    try:
        df = pd.read_csv(LOG_FILE)
        today = datetime.now().date()
        
        # Reset daily stats
        bot_stats['total_trades'] = 0
        bot_stats['wins'] = 0
        bot_stats['losses'] = 0
        bot_stats['daily_pnl'] = 0.0
        
        for _, row in df.iterrows():
            try:
                trade_date = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S').date()
                
                trade_data = {
                    'timestamp': row['timestamp'],
                    'pair': row['pair'],
                    'direction': row['direction'],
                    'amount': float(row['amount']),
                    'price': float(row['price']),
                    'result': row['result'],
                    'strategy_data': {
                        'fractal_level': row.get('fractal_level', 'N/A'),
                        'chaos_value': row.get('chaos_value', 'N/A'),
                        'volatility': row.get('volatility', 'N/A')
                    }
                }
                
                # Add to recent trades
                recent_trades.append(trade_data)
                
                # Update daily stats
                if trade_date == today:
                    bot_stats['total_trades'] += 1
                    if row['result'] == 'win':
                        bot_stats['wins'] += 1
                        bot_stats['daily_pnl'] += float(row['amount']) * 0.8  # Assuming 80% payout
                    elif row['result'] == 'loss':
                        bot_stats['losses'] += 1
                        bot_stats['daily_pnl'] -= float(row['amount'])
                        
            except Exception as e:
                logger.error(f"Error processing trade row: {e}")
                continue
                
        logger.info(f"Loaded {len(recent_trades)} trades from CSV")
        
    except Exception as e:
        logger.error(f"Error loading trades from CSV: {e}")

def save_trade_to_csv(trade_data):
    """Save a trade to the CSV log file"""
    try:
        file_exists = os.path.exists(LOG_FILE)
        
        with open(LOG_FILE, 'a', newline='') as csvfile:
            fieldnames = ['timestamp', 'pair', 'direction', 'amount', 'price', 'result', 
                         'fractal_level', 'chaos_value', 'volatility']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            if not file_exists:
                writer.writeheader()
            
            # Flatten strategy data for CSV
            row_data = {
                'timestamp': trade_data['timestamp'],
                'pair': trade_data['pair'],
                'direction': trade_data['direction'],
                'amount': trade_data['amount'],
                'price': trade_data['price'],
                'result': trade_data['result'],
                'fractal_level': trade_data.get('strategy_data', {}).get('fractal_level', 'N/A'),
                'chaos_value': trade_data.get('strategy_data', {}).get('chaos_value', 'N/A'),
                'volatility': trade_data.get('strategy_data', {}).get('volatility', 'N/A')
            }
            
            writer.writerow(row_data)
            
    except Exception as e:
        logger.error(f"Error saving trade to CSV: {e}")

def add_log(message, level='info'):
    """Add a log entry"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = {
        'timestamp': timestamp,
        'level': level,
        'message': message
    }
    recent_logs.append(log_entry)
    
    # Emit to connected clients
    socketio.emit('new_log', log_entry)
    logger.info(f"[{level.upper()}] {message}")

def update_bot_stats(trade_data=None):
    """Update bot statistics"""
    if trade_data:
        bot_stats['total_trades'] += 1
        
        if trade_data['result'] == 'win':
            bot_stats['wins'] += 1
            payout = trade_data['amount'] * (bot_config['min_payout'] / 100)
            bot_stats['daily_pnl'] += payout
            bot_stats['balance'] += payout
        elif trade_data['result'] == 'loss':
            bot_stats['losses'] += 1
            bot_stats['daily_pnl'] -= trade_data['amount']
            bot_stats['balance'] -= trade_data['amount']
    
    # Emit updated stats to clients
    socketio.emit('stats_update', bot_stats)

# Flask Routes

@app.route('/')
def serve_dashboard():
    """Serve the dashboard HTML file"""
    return send_from_directory('.', 'trading_bot_dashboard.html')

@app.route('/api/status')
def get_status():
    """Get bot status"""
    return jsonify({
        'bot_running': bot_running,
        'api_connected': bot_stats['api_connected'],
        'stats': bot_stats
    })

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    """Get or update bot configuration"""
    if request.method == 'GET':
        return jsonify(bot_config)
    
    elif request.method == 'POST':
        try:
            new_config = request.json
            bot_config.update(new_config)
            save_config()
            add_log("Configuration updated successfully")
            return jsonify({'success': True, 'config': bot_config})
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/trades')
def get_trades():
    """Get recent trades"""
    return jsonify(list(recent_trades))

@app.route('/api/logs')
def get_logs():
    """Get recent logs"""
    return jsonify(list(recent_logs))

@app.route('/api/start', methods=['POST'])
def start_bot():
    """Start the trading bot"""
    global bot_process, bot_running
    
    try:
        if bot_running:
            return jsonify({'success': False, 'error': 'Bot is already running'})
        
        if not os.path.exists(BOT_SCRIPT):
            return jsonify({'success': False, 'error': f'Bot script {BOT_SCRIPT} not found'})
        
        # Start the bot process
        bot_process = subprocess.Popen([
            sys.executable, BOT_SCRIPT
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        bot_running = True
        bot_stats['api_connected'] = True
        
        add_log("Bot starting...")
        add_log("API connection established", "info")
        
        # Start monitoring thread
        threading.Thread(target=monitor_bot_output, daemon=True).start()
        
        return jsonify({'success': True, 'message': 'Bot started successfully'})
    
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    """Stop the trading bot"""
    global bot_process, bot_running
    
    try:
        if not bot_running:
            return jsonify({'success': False, 'error': 'Bot is not running'})
        
        if bot_process:
            bot_process.terminate()
            bot_process.wait(timeout=10)
            bot_process = None
        
        bot_running = False
        bot_stats['api_connected'] = False
        bot_stats['active_trades'] = 0
        
        add_log("Bot stopped successfully", "warning")
        
        return jsonify({'success': True, 'message': 'Bot stopped successfully'})
    
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/restart', methods=['POST'])
def restart_bot():
    """Restart the trading bot"""
    try:
        # Stop first
        stop_response = stop_bot()
        if not stop_response.get_json().get('success', False):
            return stop_response
        
        # Wait a moment
        time.sleep(2)
        
        # Start again
        return start_bot()
    
    except Exception as e:
        logger.error(f"Error restarting bot: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def monitor_bot_output():
    """Monitor bot process output"""
    global bot_process, bot_running
    
    if not bot_process:
        return
    
    try:
        while bot_running and bot_process.poll() is None:
            output = bot_process.stdout.readline()
            if output:
                # Parse bot output and extract relevant information
                line = output.strip()
                add_log(f"Bot: {line}")
                
                # Try to parse trade information from bot output
                if "TRADE_PLACED" in line:
                    parse_trade_output(line)
                elif "SIGNAL_DETECTED" in line:
                    bot_stats['signals_today'] += 1
                    socketio.emit('stats_update', bot_stats)
                    
            time.sleep(0.1)
            
    except Exception as e:
        logger.error(f"Error monitoring bot output: {e}")
    finally:
        if bot_process and bot_process.poll() is not None:
            bot_running = False
            bot_stats['api_connected'] = False
            add_log("Bot process terminated", "warning")

def parse_trade_output(output_line):
    """Parse trade information from bot output"""
    try:
        # Expected format: TRADE_PLACED|EURUSD_otc|call|100|1.12345|win|fractal_data
        parts = output_line.split('|')
        if len(parts) >= 6:
            trade_data = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'pair': parts[1],
                'direction': parts[2],
                'amount': float(parts[3]),
                'price': float(parts[4]),
                'result': parts[5],
                'strategy_data': {
                    'fractal_level': parts[6] if len(parts) > 6 else 'N/A',
                    'chaos_value': parts[7] if len(parts) > 7 else 'N/A',
                    'volatility': parts[8] if len(parts) > 8 else 'N/A'
                }
            }
            
            # Add to recent trades
            recent_trades.append(trade_data)
            
            # Save to CSV
            save_trade_to_csv(trade_data)
            
            # Update stats
            update_bot_stats(trade_data)
            
            # Emit to clients
            socketio.emit('new_trade', trade_data)
            
            add_log(f"Trade processed: {trade_data['pair']} {trade_data['direction']} - {trade_data['result']}", "trade")
            
    except Exception as e:
        logger.error(f"Error parsing trade output: {e}")

# Socket.IO Events

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info('Client connected')
    # Send current status to newly connected client
    emit('stats_update', bot_stats)
    emit('config_update', bot_config)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('Client disconnected')

@socketio.on('request_data')
def handle_data_request():
    """Handle request for current data"""
    emit('stats_update', bot_stats)
    emit('trades_update', list(recent_trades)[-10:])  # Last 10 trades
    emit('logs_update', list(recent_logs)[-50:])      # Last 50 logs

# Periodic tasks

def periodic_stats_update():
    """Periodically update and broadcast stats"""
    while True:
        try:
            if bot_running:
                # Calculate win rate
                win_rate = 0
                if bot_stats['total_trades'] > 0:
                    win_rate = (bot_stats['wins'] / bot_stats['total_trades']) * 100
                
                # Update balance based on P&L
                bot_stats['balance'] = 1000.0 + bot_stats['daily_pnl']
                
                # Broadcast to all connected clients
                socketio.emit('stats_update', bot_stats)
                
            time.sleep(5)  # Update every 5 seconds
            
        except Exception as e:
            logger.error(f"Error in periodic stats update: {e}")
            time.sleep(5)

def cleanup_old_logs():
    """Clean up old log entries daily"""
    while True:
        try:
            time.sleep(86400)  # Run daily
            
            # Keep only logs from last 7 days
            cutoff_date = datetime.now() - timedelta(days=7)
            
            if os.path.exists(LOG_FILE):
                df = pd.read_csv(LOG_FILE)
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df_filtered = df[df['timestamp'] >= cutoff_date]
                df_filtered.to_csv(LOG_FILE, index=False)
                
                logger.info(f"Cleaned up old logs. Kept {len(df_filtered)} recent trades.")
                
        except Exception as e:
            logger.error(f"Error cleaning up logs: {e}")

# Initialize and start server

def initialize_server():
    """Initialize the server"""
    try:
        # Load configuration and existing data
        load_config()
        load_trades_from_csv()
        
        # Add initial log
        add_log("Dashboard server initialized")
        
        # Start background threads
        threading.Thread(target=periodic_stats_update, daemon=True).start()
        threading.Thread(target=cleanup_old_logs, daemon=True).start()
        
        logger.info("Server initialized successfully")
        
    except Exception as e:
        logger.error(f"Error initializing server: {e}")

# Signal handlers for graceful shutdown

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info("Received shutdown signal")
    if bot_process:
        try:
            bot_process.terminate()
            bot_process.wait(timeout=5)
        except:
            bot_process.kill()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    try:
        initialize_server()
        logger.info("Starting Flask-SocketIO server on http://localhost:5000")
        socketio.run(app, debug=False, host='0.0.0.0', port=5000)
    except Exception as e:
        logger.error(f"Error starting server: {e}")
        sys.exit(1)