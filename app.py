from flask import Flask, jsonify, request, send_from_directory
import json, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=BASE_DIR)

DATA_FILE = os.path.join(BASE_DIR, 'data.json')

def load_data():
    if not os.path.exists(DATA_FILE):
        return {'accounts': [], 'total_loaded': 0, 'history': []}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Serve HTML ──
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

# ── Accounts ──
@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    data = load_data()
    return jsonify({'accounts': data['accounts'], 'total_loaded': data['total_loaded']})

@app.route('/api/accounts/load', methods=['POST'])
def load_accounts():
    """Receive raw text from accounts.txt, parse and save."""
    body = request.get_json()
    text = body.get('text', '')
    accounts = [l.strip() for l in text.splitlines() if l.strip()]
    data = load_data()
    data['accounts'] = accounts
    data['total_loaded'] = len(accounts)
    save_data(data)
    return jsonify({'accounts': accounts, 'total_loaded': len(accounts)})

@app.route('/api/accounts/first', methods=['DELETE'])
def pop_first_account():
    """Remove and return the first account (used for a job)."""
    data = load_data()
    if not data['accounts']:
        return jsonify({'error': 'No accounts left'}), 400
    account = data['accounts'].pop(0)
    save_data(data)
    return jsonify({'account': account, 'remaining': len(data['accounts'])})

@app.route('/api/accounts', methods=['DELETE'])
def clear_accounts():
    data = load_data()
    data['accounts'] = []
    save_data(data)
    return jsonify({'ok': True})

# ── History (video URLs) ──
@app.route('/api/history', methods=['GET'])
def get_history():
    data = load_data()
    return jsonify({'history': data.get('history', [])})

@app.route('/api/history', methods=['POST'])
def add_history():
    body = request.get_json()
    data = load_data()
    data.setdefault('history', []).insert(0, {
        'videoUrl': body.get('videoUrl'),
        'prompt':   body.get('prompt'),
        'time':     body.get('time')
    })
    if len(data['history']) > 50:
        data['history'] = data['history'][:50]
    save_data(data)
    return jsonify({'ok': True})

@app.route('/api/history/<int:idx>', methods=['DELETE'])
def delete_history_item(idx):
    data = load_data()
    history = data.get('history', [])
    if 0 <= idx < len(history):
        history.pop(idx)
    data['history'] = history
    save_data(data)
    return jsonify({'ok': True})

@app.route('/api/history', methods=['DELETE'])
def clear_history():
    data = load_data()
    data['history'] = []
    save_data(data)
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
