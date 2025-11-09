import os
import time
import queue
import threading
import logging
from datetime import datetime, date, timedelta
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, redirect, session, flash, url_for, jsonify
)
import mysql.connector
from mysql.connector import pooling
import requests
from bs4 import BeautifulSoup
import random
from werkzeug.security import generate_password_hash, check_password_hash

# Optional imports
try:
    from transformers import pipeline
    sentiment_analyzer = pipeline("sentiment-analysis")
except Exception as e:
    sentiment_analyzer = None
    app.logger.warning(f"Transformers pipeline init failed: {e}")

try:
    from flask_jwt_extended import (
        JWTManager, create_access_token, jwt_required, get_jwt_identity
    )
    JWT_AVAILABLE = True
except Exception:
    JWT_AVAILABLE = False

try:
    import openai
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False

# ------------------- App & Config -------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "supersecretkey")
app.config["DB_POOL_NAME"] = os.getenv("DB_POOL_NAME", "app_pool")
app.config["DB_POOL_SIZE"] = int(os.getenv("DB_POOL_SIZE", 5))
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "superjwtsecret")

if JWT_AVAILABLE:
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", app.config["JWT_SECRET_KEY"]) 
    jwt = JWTManager(app)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------- Database Connection Pool -------------------
dbconfig = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASS", ""),
    "database": os.getenv("DB_NAME", "fake_news_db"),
}

try:
    db_pool = mysql.connector.pooling.MySQLConnectionPool(
        pool_name=app.config["DB_POOL_NAME"],
        pool_size=app.config["DB_POOL_SIZE"],
        **dbconfig
    )
    logger.info("📦 Database connection pool created")
except Exception as e:
    db_pool = None
    logger.exception("Failed to create DB pool: %s", e)


def get_db_connection():
    if not db_pool:
        logger.error("DB pool not available")
        return None, None
    try:
        conn = db_pool.get_connection()
        cursor = conn.cursor(dictionary=True)
        return conn, cursor
    except Exception as e:
        logger.exception("Failed to get DB connection: %s", e)
        return None, None

# ------------------- Background Scoring Queue -------------------
scoring_queue = queue.Queue()


def background_worker():
    """Worker that processes articles in the background to compute trust scores."""
    while True:
        item = scoring_queue.get()
        if item is None:
            break
        article_id, content, url = item
        try:
            score, explanation = compute_trust_score(content, url)
            conn, cur = get_db_connection()
            if conn:
                cur.execute("UPDATE Articles SET trust_score=%s, ai_explanation=%s WHERE article_id=%s",
                            (score, explanation, article_id))
                conn.commit()
                cur.close()
                conn.close()
                logger.info("Background updated article %s with score %s", article_id, score)
        except Exception as e:
            logger.exception("Error processing background item: %s", e)
        scoring_queue.task_done()


worker_thread = threading.Thread(target=background_worker, daemon=True)
worker_thread.start()

# ------------------- AI Trust Scorer -------------------
if TRANSFORMERS_AVAILABLE:
    try:
        # lightweight pipeline; model can be swapped with a fine-tuned fake-news model
        classifier = pipeline("text-classification", return_all_scores=False)
        logger.info("🔮 Transformers pipeline initialized")
    except Exception:
        classifier = None
        logger.warning("Transformers installed but pipeline init failed")
else:
    classifier = None


def domain_from_url(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def heuristic_trust_score(text, url=None):
    """Fallback heuristic scoring if Transformers not available."""
    score = 50
    length = len(text or "")
    # penalize extremely short or overly long noisy content
    if length < 200:
        score -= 10
    if length > 3000:
        score -= 5

    # basic keyword checks
    lower = (text or "").lower()
    suspicious_keywords = ["shocking", "you won't believe", "click here", "conspiracy"]
    for kw in suspicious_keywords:
        if kw in lower:
            score -= 15

    # domain reputation heuristics
    domain = domain_from_url(url or "")
    known_trusted = ["bbc.", "reuters.", "nytimes.", "theguardian."]
    known_untrusted = ["randomclicks.", "buzzfake."]
    if any(d in domain for d in known_trusted):
        score += 20
    if any(d in domain for d in known_untrusted):
        score -= 25

    # clamp
    return max(0, min(100, score))


def compute_trust_score(text, url=None):
    """Compute trust score using transformers when available, else heuristic."""
    explanation = []
    if classifier:
        try:
            truncated = (text or "")[:1000]
            res = classifier(truncated)
            # pipeline returns a list like [{'label': 'LABEL_1', 'score': 0.98}]
            label = res[0].get('label') if isinstance(res, list) else res.get('label')
            conf = res[0].get('score') if isinstance(res, list) else res.get('score', 0.5)
            explanation.append(f"model_label={label}")
            # A small mapping heuristic
            if label and label.lower().startswith("real"):
                score = int(65 + (conf * 35))
            else:
                score = int(35 - (conf * 20))
            score = max(0, min(100, score))
            return score, "; ".join(explanation)
        except Exception as e:
            logger.exception("AI scoring failed: %s", e)
            explanation.append("ai_error")

    # fallback
    score = heuristic_trust_score(text, url)
    explanation.append("heuristic_fallback")
    return score, "; ".join(explanation)

# ------------------- Utility: Scraping / Summarization -------------------

headers = {"User-Agent": "Mozilla/5.0 (compatible; AI-FakeNews/1.0)"}


def fetch_article_text(url, max_paragraphs=8):
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return "Untitled Online Article", f"❌ Failed to fetch article: {e}", ""

    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title else "Untitled Online Article"
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    content = "\n\n".join(paragraphs[:max_paragraphs]) if paragraphs else "⚠️ No content found"
    return title[:200], content[:4000], resp.url


def summarize_text(text, max_tokens=150):
    if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
        try:
            openai.api_key = os.getenv("OPENAI_API_KEY")
            prompt = f"Summarize the following article in a short paragraph:\n\n{text[:3000]}"
            resp = openai.Completion.create(
                model="text-davinci-003",
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.2,
            )
            summary = resp.choices[0].text.strip()
            return summary
        except Exception as e:
            logger.warning("OpenAI summarization failed: %s", e)
    # fallback simple summarization: first 2 paragraphs
    return "\n\n".join(text.split('\n\n')[:2])

# ------------------- Routes (Web) -------------------

@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login_page"))

    conn, cur = get_db_connection()
    if not conn:
        flash("Database connection error!", "danger")
        return redirect(url_for("login_page"))

    cur.execute("SELECT * FROM Articles ORDER BY publish_date DESC LIMIT 50")
    articles = cur.fetchall()
    cur.close()
    conn.close()

    safe_news = [a for a in articles if a.get("trust_score", 0) >= 60]
    risky_news = [a for a in articles if a.get("trust_score", 0) < 60]

    return render_template("index.html", name=session.get("name"), safe_news=safe_news, risky_news=risky_news, today=date.today())


@app.route('/signup_page')
def signup_page():
    return render_template('signup.html')


@app.route('/signup', methods=['POST'])
def signup():
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')
    if not (name and email and password):
        flash('Please provide all fields', 'danger')
        return redirect(url_for('signup_page'))

    conn, cur = get_db_connection()
    if not conn:
        flash('DB connection error', 'danger')
        return redirect(url_for('signup_page'))

    cur.execute('SELECT * FROM Users WHERE email=%s LIMIT 1', (email,))
    if cur.fetchone():
        flash('Email already registered', 'warning')
        cur.close(); conn.close()
        return redirect(url_for('signup_page'))

    hashed = generate_password_hash(password)
    cur.execute('INSERT INTO Users (name, email, password, reputation, created_at) VALUES (%s, %s, %s, %s, %s)',
                (name, email, hashed, 50, datetime.utcnow()))
    conn.commit()
    cur.close(); conn.close()

    flash('Signup successful — please login', 'success')
    return redirect(url_for('login_page'))


@app.route('/login_page')
def login_page():
    return render_template('login.html')


@app.route('/login_user', methods=['POST'])
def login_user():
    email = request.form.get('email')
    password = request.form.get('password')
    conn, cur = get_db_connection()
    if not conn:
        flash('DB error', 'danger')
        return redirect(url_for('login_page'))

    cur.execute('SELECT * FROM Users WHERE email=%s LIMIT 1', (email,))
    user = cur.fetchone()
    cur.close(); conn.close()

    if user and check_password_hash(user['password'], password):
        session['user_id'] = user['user_id']
        session['name'] = user['name']
        flash('Welcome back!', 'success')
        return redirect(url_for('home'))

    flash('Invalid credentials', 'danger')
    return redirect(url_for('login_page'))


@app.route('/logout')
def logout():
    session.clear()
    flash("You’ve been logged out.", "info")
    return redirect(url_for('login_page'))

# ------------------- Article Endpoints -------------------

@app.route('/add_article', methods=['POST'])
def add_article():
    title = request.form.get('title')
    content = request.form.get('content')
    url_link = request.form.get('url')
    publish_date = request.form.get('publish_date') or datetime.utcnow()

    if not (title and (content or url_link)):
        flash('Title and content/url required', 'danger')
        return redirect(url_for('home'))

    conn, cur = get_db_connection()
    if not conn:
        flash('DB error', 'danger')
        return redirect(url_for('home'))

    # use a source id if available
    cur.execute('SELECT source_id FROM Sources LIMIT 1')
    s = cur.fetchone()
    source_id = s['source_id'] if s else 1

    cur.execute(
        'INSERT INTO Articles (title, content, url, publish_date, source_id, trust_score, source, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
        (title, content, url_link, publish_date, source_id, 50, 'manual', datetime.utcnow())
    )
    article_id = cur.lastrowid
    conn.commit()
    cur.close(); conn.close()

    # queue background scoring
    scoring_queue.put((article_id, content or title, url_link))

    flash('Article submitted! It will be analysed shortly.', 'success')
    return redirect(url_for('home'))


@app.route('/check_online', methods=['POST'])
def check_online():
    url_link = request.form.get('url_link')
    if not url_link:
        flash('URL is required', 'danger')
        return redirect(url_for('home'))

    title, snippet, final_url = fetch_article_text(url_link)
    score, explanation = compute_trust_score(snippet, final_url)

    conn, cur = get_db_connection()
    if not conn:
        flash('DB error', 'danger')
        return redirect(url_for('home'))

    cur.execute(
        'INSERT INTO Articles (title, content, url, publish_date, trust_score, source, ai_explanation, created_at) VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s)',
        (title, snippet, final_url, score, 'online', explanation, datetime.utcnow())
    )
    conn.commit()
    cur.close(); conn.close()

    flash(f'✅ Online article checked! Trust Score: {score}', 'success')
    return redirect(url_for('home'))


@app.route('/report_article', methods=['POST'])
def report_article():
    article_id = request.form.get('article_id')
    reason = request.form.get('reason') or 'reported'
    user_id = session.get('user_id')
    if not user_id:
        flash('You must be logged in to report!', 'danger')
        return redirect(url_for('login_page'))

    conn, cur = get_db_connection()
    if not conn:
        flash('DB error', 'danger')
        return redirect(url_for('home'))

    cur.execute('INSERT INTO Reports (article_id, user_id, reason, created_at) VALUES (%s,%s,%s,%s)',
                (article_id, user_id, reason, datetime.utcnow()))
    conn.commit()

    cur.execute('SELECT COUNT(*) AS report_count FROM Reports WHERE article_id=%s', (article_id,))
    report_data = cur.fetchone()
    report_count = report_data['report_count'] if report_data else 0
    new_score = max(0, 100 - (report_count * 10))

    cur.execute('UPDATE Articles SET trust_score=%s WHERE article_id=%s', (new_score, article_id))
    conn.commit()
    cur.close(); conn.close()

    flash(f'Report submitted! Trust Score updated to {new_score}.', 'success')
    return redirect(url_for('home'))

# ------------------- Admin / Dashboard -------------------

@app.route('/dashboard')
def dashboard():
    # Basic aggregated stats for charts
    conn, cur = get_db_connection()
    if not conn:
        flash('DB error', 'danger')
        return redirect(url_for('home'))

    cur.execute('SELECT trust_score, COUNT(*) as cnt FROM Articles GROUP BY trust_score')
    rows = cur.fetchall()

    # transform into buckets
    buckets = {'safe': 0, 'risky': 0}
    for r in rows:
        if r['trust_score'] >= 60:
            buckets['safe'] += r['cnt']
        else:
            buckets['risky'] += r['cnt']

    cur.close(); conn.close()
    return render_template('dashboard.html', buckets=buckets)

# ------------------- API Endpoints (JWT) -------------------

if JWT_AVAILABLE:
    @app.route('/api/login', methods=['POST'])
    def api_login():
        data = request.get_json() or {}
        email = data.get('email')
        password = data.get('password')
        conn, cur = get_db_connection()
        if not conn:
            return jsonify({'msg': 'DB error'}), 500
        cur.execute('SELECT * FROM Users WHERE email=%s LIMIT 1', (email,))
        user = cur.fetchone()
        cur.close(); conn.close()
        if user and check_password_hash(user['password'], password):
            access_token = create_access_token(identity={'user_id': user['user_id'], 'email': user['email']}, expires_delta=timedelta(hours=12))
            return jsonify(access_token=access_token)
        return jsonify({'msg': 'Invalid credentials'}), 401

    @app.route('/api/articles', methods=['GET'])
    @jwt_required()
    def api_articles():
        args = request.args
        limit = int(args.get('limit', 20))
        conn, cur = get_db_connection()
        if not conn:
            return jsonify({'msg': 'DB error'}), 500
        cur.execute('SELECT article_id, title, url, trust_score, publish_date FROM Articles ORDER BY publish_date DESC LIMIT %s', (limit,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return jsonify(rows)

# ------------------- Summarize / Explain Endpoint -------------------

@app.route('/summarize', methods=['POST'])
def summarize_article():
    article_id = request.form.get('article_id')
    conn, cur = get_db_connection()
    if not conn:
        return jsonify({'msg': 'DB error'}), 500
    cur.execute('SELECT content, url FROM Articles WHERE article_id=%s LIMIT 1', (article_id,))
    art = cur.fetchone()
    cur.close(); conn.close()
    if not art:
        flash('Article not found', 'danger')
        return redirect(url_for('home'))

    summary = summarize_text(art['content'] or '')
    flash('Summary generated', 'info')
    return render_template('summary.html', summary=summary, article_id=article_id)

# ------------------- Health & Admin Utilities -------------------

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.utcnow().isoformat()})

@app.route('/admin/recompute/<int:article_id>')
def admin_recompute(article_id):
    # recompute immediately (admin only — in production, protect with auth)
    conn, cur = get_db_connection()
    if not conn:
        flash('DB error', 'danger')
        return redirect(url_for('home'))
    cur.execute('SELECT content, url FROM Articles WHERE article_id=%s LIMIT 1', (article_id,))
    art = cur.fetchone()
    if not art:
        cur.close(); conn.close()
        flash('Article not found', 'danger')
        return redirect(url_for('home'))

    score, explanation = compute_trust_score(art['content'], art['url'])
    cur.execute('UPDATE Articles SET trust_score=%s, ai_explanation=%s WHERE article_id=%s', (score, explanation, article_id))
    conn.commit()
    cur.close(); conn.close()
    flash(f'Article recomputed — new score {score}', 'success')
    return redirect(url_for('home'))

# ------------------- Shutdown Handler -------------------

@app.route('/shutdown_worker')
def shutdown_worker():
    # CAUTION: in production protect this endpoint
    scoring_queue.put(None)
    flash('Worker shutdown requested', 'info')
    return redirect(url_for('home'))

# ------------------- Run App -------------------

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'true').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=port, debug=debug)
