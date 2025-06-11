# shared_state.py - Shared state management between bot and dashboard
import json
import sqlite3
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
import queue

class BotStateManager:
    def __init__(self, db_path: str = "bot_state.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.init_database()
        
    def init_database(self):
        """Initialize the database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_status (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    status TEXT,
                    balance REAL,
                    positions TEXT,
                    last_signal TEXT,
                    pnl REAL,
                    trades_today INTEGER
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol TEXT,
                    side TEXT,
                    size REAL,
                    price REAL,
                    pnl REAL,
                    status TEXT
                )
            ''')
            
            conn.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol TEXT,
                    signal_type TEXT,
                    price REAL,
                    confidence REAL
                )
            ''')
            
    def update_bot_status(self, status_data: Dict[str, Any]):
        """Update bot status in database"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO bot_status 
                    (id, timestamp, status, balance, positions, last_signal, pnl, trades_today)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.now().isoformat(),
                    status_data.get('status', 'unknown'),
                    status_data.get('balance', 0),
                    json.dumps(status_data.get('positions', [])),
                    status_data.get('last_signal', ''),
                    status_data.get('pnl', 0),
                    status_data.get('trades_today', 0)
                ))
                
    def add_trade(self, trade_data: Dict[str, Any]):
        """Add a new trade to database"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO trades (timestamp, symbol, side, size, price, pnl, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.now().isoformat(),
                    trade_data.get('symbol'),
                    trade_data.get('side'),
                    trade_data.get('size'),
                    trade_data.get('price'),
                    trade_data.get('pnl', 0),
                    trade_data.get('status', 'pending')
                ))
                
    def add_signal(self, signal_data: Dict[str, Any]):
        """Add a new signal to database"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO signals (timestamp, symbol, signal_type, price, confidence)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    datetime.now().isoformat(),
                    signal_data.get('symbol'),
                    signal_data.get('signal_type'),
                    signal_data.get('price'),
                    signal_data.get('confidence', 0)
                ))
                
    def get_bot_status(self) -> Optional[Dict[str, Any]]:
        """Get current bot status"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT * FROM bot_status WHERE id = 1')
            row = cursor.fetchone()
            if row:
                return {
                    'timestamp': row[1],
                    'status': row[2],
                    'balance': row[3],
                    'positions': json.loads(row[4]) if row[4] else [],
                    'last_signal': row[5],
                    'pnl': row[6],
                    'trades_today': row[7]
                }
        return None
        
    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent trades"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT * FROM trades 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (limit,))
            
            trades = []
            for row in cursor.fetchall():
                trades.append({
                    'id': row[0],
                    'timestamp': row[1],
                    'symbol': row[2],
                    'side': row[3],
                    'size': row[4],
                    'price': row[5],
                    'pnl': row[6],
                    'status': row[7]
                })
            return trades
            
    def get_recent_signals(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent signals"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT * FROM signals 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (limit,))
            
            signals = []
            for row in cursor.fetchall():
                signals.append({
                    'id': row[0],
                    'timestamp': row[1],
                    'symbol': row[2],
                    'signal_type': row[3],
                    'price': row[4],
                    'confidence': row[5]
                })
            return signals

# bot_integration.py - Integration layer for your fractal_fcb_bot.py
class BotIntegration:
    def __init__(self):
        self.state_manager = BotStateManager()
        self.is_running = False
        
    def start_monitoring(self, bot_instance):
        """Start monitoring bot and updating dashboard"""
        self.is_running = True
        self.bot = bot_instance
        
        # Start background thread to update status
        monitoring_thread = threading.Thread(target=self._monitor_bot)
        monitoring_thread.daemon = True
        monitoring_thread.start()
        
    def _monitor_bot(self):
        """Background monitoring of bot status"""
        while self.is_running:
            try:
                # Get bot status (adapt these to your bot's actual attributes)
                status_data = {
                    'status': getattr(self.bot, 'status', 'running'),
                    'balance': getattr(self.bot, 'balance', 0),
                    'positions': getattr(self.bot, 'positions', []),
                    'last_signal': getattr(self.bot, 'last_signal', ''),
                    'pnl': getattr(self.bot, 'total_pnl', 0),
                    'trades_today': getattr(self.bot, 'trades_today', 0)
                }
                
                self.state_manager.update_bot_status(status_data)
                time.sleep(5)  # Update every 5 seconds
                
            except Exception as e:
                print(f"Error monitoring bot: {e}")
                time.sleep(10)
                
    def log_trade(self, trade_data: Dict[str, Any]):
        """Log a trade to dashboard"""
        self.state_manager.add_trade(trade_data)
        
    def log_signal(self, signal_data: Dict[str, Any]):
        """Log a signal to dashboard"""
        self.state_manager.add_signal(signal_data)
        
    def stop_monitoring(self):
        """Stop monitoring"""
        self.is_running = False

# dashboard_api.py - API endpoints for dashboard
from flask import Flask, jsonify, render_template_string
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Initialize state manager
state_manager = BotStateManager()

@app.route('/api/status')
def get_status():
    """Get current bot status"""
    status = state_manager.get_bot_status()
    return jsonify(status or {})

@app.route('/api/trades')
def get_trades():
    """Get recent trades"""
    trades = state_manager.get_recent_trades()
    return jsonify(trades)

@app.route('/api/signals')
def get_signals():
    """Get recent signals"""
    signals = state_manager.get_recent_signals()
    return jsonify(signals)

@app.route('/')
def dashboard():
    """Main dashboard page"""
    return render_template_string(DASHBOARD_HTML)

# Simple HTML dashboard template
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Fractal FCB Bot Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #1a1a1a; color: white; }
        .container { max-width: 1200px; margin: 0 auto; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .status-card { background: #2d2d2d; padding: 20px; border-radius: 8px; text-align: center; }
        .status-value { font-size: 2em; font-weight: bold; color: #4CAF50; }
        .table-container { background: #2d2d2d; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #444; }
        th { background: #3d3d3d; }
        .positive { color: #4CAF50; }
        .negative { color: #f44336; }
        .refresh-btn { background: #4CAF50; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Fractal FCB Bot Dashboard</h1>
        
        <div class="status-grid">
            <div class="status-card">
                <h3>Status</h3>
                <div class="status-value" id="bot-status">Loading...</div>
            </div>
            <div class="status-card">
                <h3>Balance</h3>
                <div class="status-value" id="balance">$0</div>
            </div>
            <div class="status-card">
                <h3>P&L Today</h3>
                <div class="status-value" id="pnl">$0</div>
            </div>
            <div class="status-card">
                <h3>Trades Today</h3>
                <div class="status-value" id="trades-count">0</div>
            </div>
        </div>
        
        <button class="refresh-btn" onclick="refreshData()">Refresh Data</button>
        
        <div class="table-container">
            <h3>Recent Trades</h3>
            <table id="trades-table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Size</th>
                        <th>Price</th>
                        <th>P&L</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <div class="table-container">
            <h3>Recent Signals</h3>
            <table id="signals-table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Signal</th>
                        <th>Price</th>
                        <th>Confidence</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>
    
    <script>
        function formatTime(timestamp) {
            return new Date(timestamp).toLocaleTimeString();
        }
        
        function formatCurrency(value) {
            return '$' + parseFloat(value).toFixed(2);
        }
        
        function updateStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('bot-status').textContent = data.status || 'Unknown';
                    document.getElementById('balance').textContent = formatCurrency(data.balance || 0);
                    
                    const pnlElement = document.getElementById('pnl');
                    const pnl = data.pnl || 0;
                    pnlElement.textContent = formatCurrency(pnl);
                    pnlElement.className = 'status-value ' + (pnl >= 0 ? 'positive' : 'negative');
                    
                    document.getElementById('trades-count').textContent = data.trades_today || 0;
                });
        }
        
        function updateTrades() {
            fetch('/api/trades')
                .then(response => response.json())
                .then(trades => {
                    const tbody = document.querySelector('#trades-table tbody');
                    tbody.innerHTML = '';
                    
                    trades.forEach(trade => {
                        const row = tbody.insertRow();
                        row.innerHTML = `
                            <td>${formatTime(trade.timestamp)}</td>
                            <td>${trade.symbol}</td>
                            <td>${trade.side}</td>
                            <td>${trade.size}</td>
                            <td>${formatCurrency(trade.price)}</td>
                            <td class="${trade.pnl >= 0 ? 'positive' : 'negative'}">${formatCurrency(trade.pnl)}</td>
                            <td>${trade.status}</td>
                        `;
                    });
                });
        }
        
        function updateSignals() {
            fetch('/api/signals')
                .then(response => response.json())
                .then(signals => {
                    const tbody = document.querySelector('#signals-table tbody');
                    tbody.innerHTML = '';
                    
                    signals.forEach(signal => {
                        const row = tbody.insertRow();
                        row.innerHTML = `
                            <td>${formatTime(signal.timestamp)}</td>
                            <td>${signal.symbol}</td>
                            <td>${signal.signal_type}</td>
                            <td>${formatCurrency(signal.price)}</td>
                            <td>${(signal.confidence * 100).toFixed(1)}%</td>
                        `;
                    });
                });
        }
        
        function refreshData() {
            updateStatus();
            updateTrades();
            updateSignals();
        }
        
        // Initial load
        refreshData();
        
        // Auto-refresh every 5 seconds
        setInterval(refreshData, 5000);
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
