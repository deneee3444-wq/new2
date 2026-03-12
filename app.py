from flask import Flask, request, jsonify, send_from_directory, session
from functools import wraps
import requests
import json
import os
import threading
import time
import base64
from datetime import datetime

app = Flask(__name__, static_folder='.')
app.secret_key = 'clipfly-auto-secret-2024'

ADMIN_PASSWORD = '123'

BASE = 'https://www.clipfly.ai'

# ── CLIPFLY HEADERS (orijinal ile birebir aynı) ──────────────────────────────
def clipfly_headers(token, extra=None):
    h = {
        'accept': 'application/json',
        'accept-language': 'en-US,en;q=0.8',
        'authorization': token,
        'content-type': 'application/json',
        'origin': BASE,
        'platform': 'web',
        'priority': 'u=1, i',
        'referer': BASE + '/aitools/ai-video-generator-v2',
        'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        'zone': '180',
    }
    if extra:
        h.update(extra)
    return h

# ── STATE ────────────────────────────────────────────────────────────────────
state_lock = threading.Lock()
state = {
    'tokens': [],
    'total_loaded': 0,
    'jobs': {},        # jobId -> job dict  (RAM'de yaşar, finished olanlar JSON'a yazılır)
    'history': [],
    'favorites': []
}

DATA_FILE = os.path.join(os.path.dirname(__file__), 'data.json')

def load_from_disk():
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        with state_lock:
            state['tokens']       = saved.get('tokens', [])
            state['total_loaded'] = saved.get('total_loaded', 0)
            state['history']      = saved.get('history', [])
            state['favorites']    = saved.get('favorites', [])
            # Sadece tamamlanan / hata veren job'ları restore et
            for jid, job in saved.get('jobs', {}).items():
                if job.get('status') in ('success', 'error'):
                    state['jobs'][jid] = job
        print(f'[BOOT] {len(state["tokens"])} hesap, {len(state["history"])} geçmiş, {len(state["jobs"])} job yüklendi.')
    except Exception as e:
        print(f'[BOOT] State yüklenemedi: {e}')

def save_to_disk():
    try:
        with state_lock:
            to_save = {
                'tokens':       state['tokens'],
                'total_loaded': state['total_loaded'],
                'history':      state['history'],
                'favorites':    state['favorites'],
                'jobs': {
                    jid: j for jid, j in state['jobs'].items()
                    if j.get('status') in ('success', 'error')
                }
            }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[SAVE] Hata: {e}')

load_from_disk()

# ── AUTH ─────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Yetkisiz erişim', 'auth': False}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json(force=True)
    if data.get('password') == ADMIN_PASSWORD:
        session['logged_in'] = True
        session.permanent = True
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Şifre hatalı'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/auth-check', methods=['GET'])
def auth_check():
    return jsonify({'logged_in': bool(session.get('logged_in'))})

# ── YARDIMCI ─────────────────────────────────────────────────────────────────
def update_job(job_id, **kwargs):
    with state_lock:
        if job_id in state['jobs']:
            state['jobs'][job_id].update(kwargs)

# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'index.html')

# ── STATE ──
@app.route('/api/state', methods=['GET'])
@login_required
def get_state():
    with state_lock:
        return jsonify({
            'tokens':       state['tokens'],
            'total_loaded': state['total_loaded'],
            'jobs':         state['jobs'],
            'history':      state['history'],
            'favorites':    state['favorites'],
        })

# ── FAVORITES ──
@app.route('/api/favorites', methods=['GET'])
@login_required
def get_favorites():
    with state_lock:
        return jsonify(state['favorites'])

@app.route('/api/favorites', methods=['POST'])
@login_required
def add_favorite():
    item = request.get_json(force=True)
    with state_lock:
        if not any(f['videoUrl'] == item['videoUrl'] for f in state['favorites']):
            state['favorites'].insert(0, item)
            if len(state['favorites']) > 100:
                state['favorites'] = state['favorites'][:100]
    save_to_disk()
    return jsonify({'ok': True})

@app.route('/api/favorites/<int:idx>', methods=['DELETE'])
@login_required
def delete_favorite(idx):
    with state_lock:
        if 0 <= idx < len(state['favorites']):
            state['favorites'].pop(idx)
    save_to_disk()
    return jsonify({'ok': True})

@app.route('/api/favorites', methods=['DELETE'])
@login_required
def clear_favorites():
    with state_lock:
        state['favorites'] = []
    save_to_disk()
    return jsonify({'ok': True})

# ── ACCOUNTS ──
@app.route('/api/accounts', methods=['POST'])
@login_required
def set_accounts():
    data       = request.get_json(force=True)
    new_tokens = [t for t in data.get('tokens', []) if t.strip()]
    append     = data.get('append', False)
    with state_lock:
        if append:
            existing_set = set(state['tokens'])
            for t in new_tokens:
                if t not in existing_set:
                    state['tokens'].append(t)
                    existing_set.add(t)
        else:
            state['tokens'] = new_tokens
        state['total_loaded'] = len(state['tokens'])
        count = len(state['tokens'])
    save_to_disk()
    return jsonify({'ok': True, 'count': count})

@app.route('/api/accounts', methods=['DELETE'])
@login_required
def clear_accounts():
    with state_lock:
        state['tokens']       = []
        state['total_loaded'] = 0
    save_to_disk()
    return jsonify({'ok': True})

# ── HISTORY ──
@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    with state_lock:
        return jsonify(state['history'])

@app.route('/api/history', methods=['POST'])
@login_required
def add_history():
    item = request.get_json(force=True)
    with state_lock:
        state['history'].insert(0, item)
        if len(state['history']) > 50:
            state['history'] = state['history'][:50]
    save_to_disk()
    return jsonify({'ok': True})

@app.route('/api/history/<int:idx>', methods=['DELETE'])
@login_required
def delete_history_item(idx):
    with state_lock:
        if 0 <= idx < len(state['history']):
            state['history'].pop(idx)
    save_to_disk()
    return jsonify({'ok': True})

@app.route('/api/history', methods=['DELETE'])
@login_required
def clear_history():
    with state_lock:
        state['history'] = []
    save_to_disk()
    return jsonify({'ok': True})

# ── JOBS ──
@app.route('/api/jobs', methods=['GET'])
@login_required
def get_jobs():
    with state_lock:
        return jsonify(state['jobs'])

@app.route('/api/jobs/<job_id>', methods=['GET'])
@login_required
def get_job(job_id):
    with state_lock:
        job = state['jobs'].get(job_id)
    if not job:
        return jsonify({'error': 'Bulunamadı'}), 404
    return jsonify(job)

@app.route('/api/jobs/<job_id>', methods=['DELETE'])
@login_required
def delete_job(job_id):
    with state_lock:
        state['jobs'].pop(job_id, None)
    save_to_disk()
    return jsonify({'ok': True})

@app.route('/api/jobs', methods=['DELETE'])
@login_required
def clear_finished_jobs():
    """Sadece tamamlanan / hata veren job'ları sil (aktif olanlar korunur)."""
    with state_lock:
        state['jobs'] = {
            jid: j for jid, j in state['jobs'].items()
            if j.get('status') == 'running'
        }
    save_to_disk()
    return jsonify({'ok': True})

# ── GENERATE ──
@app.route('/api/generate', methods=['POST'])
@login_required
def generate():
    data         = request.get_json(force=True)
    image_data   = data.get('imageBase64', '')
    image_name   = data.get('imageName', 'image.jpg')
    prompt       = data.get('prompt', '').strip()
    model_id     = str(data.get('model_id', '17'))
    duration     = str(data.get('duration', '10'))
    voice        = bool(data.get('voice', False))
    audio_type   = int(data.get('audio_type', 0))

    if not image_data or not prompt:
        return jsonify({'error': 'Eksik parametre'}), 400

    with state_lock:
        if not state['tokens']:
            return jsonify({'error': 'Hesap listesi boş. accounts.txt yükleyin.'}), 400
        token_line = state['tokens'][0]
        parts      = token_line.split(':')
        token      = parts[2].strip() if len(parts) > 2 else token_line.strip()
        state['tokens'].pop(0)

    save_to_disk()

    job_id = str(int(time.time() * 1000))

    with state_lock:
        state['jobs'][job_id] = {
            'id':        job_id,
            'prompt':    prompt,
            'model_id':  model_id,
            'duration':  duration,
            'voice':     voice,
            'status':    'running',
            'step':      'Başlatılıyor...',
            'stepIndex': -1,
            'videoUrl':  None,
            'error':     None,
            'createdAt': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        }

    t = threading.Thread(
        target=run_generation,
        args=(job_id, token, image_data, image_name, prompt, model_id, duration, voice, audio_type),
        daemon=True
    )
    t.start()

    return jsonify({'jobId': job_id})


# ── ARKA PLANDA ÇALIŞAN ÜRETİM DÖNGÜSÜ ──────────────────────────────────────
STEPS = [
    'Signed URL alınıyor...',
    'Resim yükleniyor (base64)...',
    'Resim yükleniyor (raw)...',
    'Materyal oluşturuluyor...',
    'Görev kuyruğa alınıyor...',
    'Video bekleniyor...'
]

def run_generation(job_id, token, image_data_url, image_name, prompt,
                   model_id='17', duration='10', voice=False, audio_type=0):
    try:
        # Base64 verisini ayır
        if ',' in image_data_url:
            b64_data = image_data_url.split(',')[1]
        else:
            b64_data = image_data_url

        file_ext = image_name.split('.')[-1].lower()
        if file_ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            file_ext = 'jpeg'

        # ── ADIM 0: Signed URL ──────────────────────────────────────────────
        update_job(job_id, step=STEPS[0], stepIndex=0)
        print(f'[{job_id}] Adım 0: Signed URL alınıyor...')

        sign_res = requests.get(
            f'{BASE}/api/v1/common/upload/signed-url',
            params={'filename': image_name},
            headers=clipfly_headers(token),
            timeout=30
        )
        sign_json = sign_res.json()
        if sign_json.get('code') and sign_json['code'] != 200:
            raise Exception(
                f"Token geçersiz veya süresi dolmuş ({sign_json.get('message')}). "
                "Lütfen accounts.txt dosyasını yeniden yükleyin."
            )
        signing = sign_json.get('data', '')
        if not isinstance(signing, str) or '.com' not in signing:
            raise Exception('Signed URL alınamadı: ' + json.dumps(signing))

        # ── ADIM 1: Base64 upload ────────────────────────────────────────────
        update_job(job_id, step=STEPS[1], stepIndex=1)
        print(f'[{job_id}] Adım 1: Base64 yükleniyor...')

        b64_res = requests.post(
            f'{BASE}/api/v1/common/upload/base64',
            headers=clipfly_headers(token),
            json={
                'content':          image_data_url,   # tam data URL
                'file_type':        'image',
                'is_original_name': 0,
                'name':             image_name,
                'prefix_path':      '/uploads'
            },
            timeout=60
        )
        source_path = b64_res.json()['data']['url']

        # ── ADIM 2: Raw (binary) S3 upload ──────────────────────────────────
        update_job(job_id, step=STEPS[2], stepIndex=2)
        print(f'[{job_id}] Adım 2: S3\'e raw yükleniyor...')

        image_bytes = base64.b64decode(b64_data)
        requests.put(
            signing,          # tam S3 URL
            data=image_bytes,
            headers={
                'content-type': f'image/{file_ext}',
                'origin':       BASE,
                'referer':      BASE + '/aitools/ai-video-generator-v2',
                'user-agent':   'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
            },
            timeout=120
        )

        # ── ADIM 3: Materyal oluştur ─────────────────────────────────────────
        update_job(job_id, step=STEPS[3], stepIndex=3)
        print(f'[{job_id}] Adım 3: Materyal oluşturuluyor...')

        mat_res = requests.post(
            f'{BASE}/api/v1/user/materials/create',
            headers=clipfly_headers(token, {'x-country': 'TR'}),
            json={
                'is_ai': -1,
                'name':  image_name,
                'type':  'image',
                'attrs': {},
                'urls': {
                    'thumb': source_path.replace(
                        'https://video-clipfly-west2.s3.us-west-2.amazonaws.com', ''
                    ),
                    'url': signing.split('?')[0].split('.com')[1]
                }
            },
            timeout=30
        )
        material_id = mat_res.json()['data']['id']

        # ── ADIM 4: Görev kuyruğa al ─────────────────────────────────────────
        update_job(job_id, step=STEPS[4], stepIndex=4)
        print(f'[{job_id}] Adım 4: Görev kuyruğa alınıyor...')

        source_image = signing.split('?')[0].split('.com')[1]
        task_res = requests.post(
            f'{BASE}/api/v1/user/ai-task-queues',
            headers=clipfly_headers(token),
            json={
                'type': 17,
                'attrs': [{
                    'enhance':         False,
                    'prompt':          prompt,
                    'camera_control':  'auto',
                    'from':            'image',
                    'imageFrom':       'cloud',
                    'is_scale':        0,
                    'materialId':      str(material_id),
                    'negative_prompt': '',
                    'voice':           voice,
                    'model_id':        model_id,
                    'biz_type':        17,
                    'duration':        duration,
                    'audio_type':      audio_type,
                    'camerafixed':     False,
                    'source_image':    source_image,
                    'urls':            {'url': source_image}
                }]
            },
            timeout=30
        )
        if not task_res.ok:
            raise Exception('Görev oluşturulamadı: ' + json.dumps(task_res.json()))

        task_json = task_res.json()
        # Task ID'yi yakala — parallel job'lar birbirinin sonucunu almasın
        task_id = None
        try:
            task_id = str(task_json['data']['id'])
        except (KeyError, TypeError):
            pass
        print(f'[{job_id}] Görev kuyruğa alındı. task_id={task_id}')

        # ── ADIM 5: Video URL bekle ──────────────────────────────────────────
        update_job(job_id, step=STEPS[5], stepIndex=5)
        print(f'[{job_id}] Adım 5: Video bekleniyor...')

        poll_url = (
            f'{BASE}/api/v1/user/ai-tasks/video-generate-list'
            '?page=1&page_size=10&paranoid=1&task_type=7&version=1.0&clear=true'
        )

        video_url = None
        for i in range(300):
            time.sleep(2)
            poll_res  = requests.get(poll_url, headers=clipfly_headers(token), timeout=30)
            poll_data = poll_res.json()
            try:
                groups = poll_data.get('data', [])
                found  = None
                if task_id:
                    # ID'ye göre ara — tüm grup ve task'lara bak
                    for group in groups:
                        for task in (group.get('tasks') or []):
                            if str(task.get('id')) == task_id or str(task.get('queue_id')) == task_id:
                                found = task
                                break
                        if found:
                            break
                    # Bulunamadıysa ilk task'ı dene (fallback)
                    if not found and groups:
                        found = groups[0]['tasks'][0]
                else:
                    found = groups[0]['tasks'][0]

                if found:
                    url = found['after_material']['urls']['url']
                    if url:
                        video_url = BASE + url
                        break
            except (KeyError, IndexError, TypeError):
                pass

        if not video_url:
            raise Exception('Video URL alınamadı — zaman aşımı (10 dk)')

        print(f'[{job_id}] ✓ Video hazır: {video_url}')
        update_job(job_id, status='success', videoUrl=video_url, step='Tamamlandı', stepIndex=6)

        # Geçmişe ekle
        history_item = {
            'videoUrl': video_url,
            'prompt':   prompt,
            'time':     datetime.now().strftime('%d.%m.%Y %H:%M')
        }
        with state_lock:
            state['history'].insert(0, history_item)
            if len(state['history']) > 50:
                state['history'] = state['history'][:50]
        save_to_disk()

    except Exception as e:
        print(f'[{job_id}] ✗ Hata: {e}')
        update_job(job_id, status='error', error=str(e), step='Hata')
        save_to_disk()


# ── IMAGE-TO-IMAGE ÜRETIM DÖNGÜSÜ ────────────────────────────────────────────
IMG_STEPS = [
    'Signed URL alınıyor...',
    'Resim yükleniyor (base64)...',
    'Resim yükleniyor (raw)...',
    'Materyal oluşturuluyor...',
    'Görev kuyruğa alınıyor...',
    'Görsel bekleniyor...'
]

@app.route('/api/generate-image', methods=['POST'])
@login_required
def generate_image():
    data       = request.get_json(force=True)
    image_data = data.get('imageBase64', '')
    image_name = data.get('imageName', 'image.jpg')
    prompt     = data.get('prompt', '').strip()

    if not image_data or not prompt:
        return jsonify({'error': 'Eksik parametre'}), 400

    with state_lock:
        if not state['tokens']:
            return jsonify({'error': 'Hesap listesi boş. accounts.txt yükleyin.'}), 400
        token_line = state['tokens'][0]
        parts      = token_line.split(':')
        token      = parts[2].strip() if len(parts) > 2 else token_line.strip()
        state['tokens'].pop(0)

    save_to_disk()

    job_id = str(int(time.time() * 1000))
    with state_lock:
        state['jobs'][job_id] = {
            'id':        job_id,
            'prompt':    prompt,
            'job_type':  'image',
            'status':    'running',
            'step':      'Başlatılıyor...',
            'stepIndex': -1,
            'videoUrl':  None,
            'error':     None,
            'createdAt': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        }

    t = threading.Thread(
        target=run_image_generation,
        args=(job_id, token, image_data, image_name, prompt),
        daemon=True
    )
    t.start()
    return jsonify({'jobId': job_id})


def run_image_generation(job_id, token, image_data_url, image_name, prompt):
    try:
        if ',' in image_data_url:
            b64_data = image_data_url.split(',')[1]
        else:
            b64_data = image_data_url

        file_ext = image_name.split('.')[-1].lower()
        if file_ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            file_ext = 'jpeg'

        # ── ADIM 0: Signed URL ──────────────────────────────────────────────
        update_job(job_id, step=IMG_STEPS[0], stepIndex=0)
        sign_res  = requests.get(
            f'{BASE}/api/v1/common/upload/signed-url',
            params={'filename': image_name},
            headers=clipfly_headers(token),
            timeout=30
        )
        sign_json = sign_res.json()
        if sign_json.get('code') and sign_json['code'] != 200:
            raise Exception(f"Token geçersiz veya süresi dolmuş ({sign_json.get('message')})")
        signing = sign_json.get('data', '')
        if not isinstance(signing, str) or '.com' not in signing:
            raise Exception('Signed URL alınamadı: ' + json.dumps(signing))

        # ── ADIM 1: Base64 upload ────────────────────────────────────────────
        update_job(job_id, step=IMG_STEPS[1], stepIndex=1)
        b64_res     = requests.post(
            f'{BASE}/api/v1/common/upload/base64',
            headers=clipfly_headers(token),
            json={
                'content':          image_data_url,
                'file_type':        'image',
                'is_original_name': 0,
                'name':             image_name,
                'prefix_path':      '/uploads'
            },
            timeout=60
        )
        source_path = b64_res.json()['data']['url']

        # ── ADIM 2: Raw (binary) S3 upload ──────────────────────────────────
        update_job(job_id, step=IMG_STEPS[2], stepIndex=2)
        image_bytes = base64.b64decode(b64_data)
        requests.put(
            signing,
            data=image_bytes,
            headers={
                'content-type':   f'image/{file_ext}',
                'origin':         BASE,
                'referer':        BASE + '/aitools/ai-video-generator-v2',
                'user-agent':     'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
            },
            timeout=120
        )

        # ── ADIM 3: Materyal oluştur ─────────────────────────────────────────
        update_job(job_id, step=IMG_STEPS[3], stepIndex=3)
        source_image = signing.split('?')[0].split('.com')[1]
        mat_res      = requests.post(
            f'{BASE}/api/v1/user/materials/create',
            headers=clipfly_headers(token, {'x-country': 'TR'}),
            json={
                'is_ai': -1,
                'name':  image_name,
                'type':  'image',
                'attrs': {},
                'urls': {
                    'thumb': source_path.replace(
                        'https://video-clipfly-west2.s3.us-west-2.amazonaws.com', ''
                    ),
                    'url': source_image
                }
            },
            timeout=30
        )
        material_id = mat_res.json()['data']['id']

        # ── ADIM 4: Image-to-image görevi kuyruğa al ────────────────────────
        update_job(job_id, step=IMG_STEPS[4], stepIndex=4)
        task_res  = requests.post(
            f'{BASE}/api/v1/user/ai-tasks/image-generator/create',
            headers=clipfly_headers(token),
            json={
                'type':             22,
                'prompt':           prompt,
                'negative_prompt':  '',
                'gnum':             1,
                'style_id':         '',
                'size_id':          'Auto',
                'source_image':     source_image,
                'materialId':       str(material_id),
                'model_id':         'qwen',
                'is_scale':         '0',
                'width':            0,
                'height':           0,
            },
            timeout=30
        )
        task_json = task_res.json()
        task_id   = None
        try:
            task_id = str(task_json['data'][0]['id'])
        except (KeyError, IndexError, TypeError):
            pass
        print(f'[{job_id}] Image görevi kuyruğa alındı. task_id={task_id}')

        # ── ADIM 5: Görsel URL bekle ─────────────────────────────────────────
        update_job(job_id, step=IMG_STEPS[5], stepIndex=5)
        poll_url = (
            f'{BASE}/api/v1/user/ai-tasks/ai-generator/queue-list'
            '?page=1&page_size=10&paranoid=1&kiwi_locale=en-US'
        )

        image_url = None
        for i in range(150):   # max ~5 dakika
            time.sleep(2)
            poll_res  = requests.get(poll_url, headers=clipfly_headers(token), timeout=30)
            poll_data = poll_res.json()
            try:
                groups = poll_data.get('data', [])
                found  = None
                if task_id:
                    for group in groups:
                        for task in (group.get('tasks') or []):
                            if str(task.get('id')) == task_id:
                                found = task
                                break
                        if found:
                            break
                    if not found and groups:
                        found = (groups[0].get('tasks') or [None])[0]
                else:
                    found = (groups[0].get('tasks') or [None])[0]

                if found and found.get('status') == 2:
                    url = found['after_material']['urls']['url']
                    if url:
                        image_url = BASE + url
                        break
            except (KeyError, IndexError, TypeError):
                pass

        if not image_url:
            raise Exception('Görsel URL alınamadı — zaman aşımı (5 dk)')

        print(f'[{job_id}] ✓ Görsel hazır: {image_url}')
        update_job(job_id, status='success', videoUrl=image_url, step='Tamamlandı', stepIndex=6)

        history_item = {
            'videoUrl': image_url,
            'prompt':   prompt,
            'time':     datetime.now().strftime('%d.%m.%Y %H:%M'),
            'job_type': 'image'
        }
        with state_lock:
            state['history'].insert(0, history_item)
            if len(state['history']) > 50:
                state['history'] = state['history'][:50]
        save_to_disk()

    except Exception as e:
        print(f'[{job_id}] ✗ Image Hata: {e}')
        update_job(job_id, status='error', error=str(e), step='Hata')
        save_to_disk()


if __name__ == '__main__':
    print('ClipFly Auto başlatılıyor → http://localhost:5000')
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
