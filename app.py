import os
import time
import logging
from datetime import datetime, date
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, redirect, session, flash, url_for
)

import mysql.connector
import requests
from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash, check_password_hash

# ------------------- Basic App Config -------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = "supersecretkey"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------- DB Connection -------------------
def get_db():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASS", ""),
            database=os.getenv("DB_NAME", "fake_news_db")
        )
        return conn, conn.cursor(dictionary=True)
    except Exception as e:
        logger.error("DB CONNECTION FAILED: %s", e)
        return None, None

# ------------------- Trust Score -------------------
def simple_score(text):
    if not text:
        return 30

    score = 50
    low = text.lower()
    bad_words = ["shocking", "fake", "click", "scam", "you won't believe"]

    for w in bad_words:
        if w in low:
            score -= 10

    if len(text) < 200:
        score -= 5
    if len(text) > 3000:
        score -= 5

    return max(0, min(100, score))

# ------------------- Fetch Article -------------------
headers = {"User-Agent": "Mozilla/5.0"}

def fetch_article(url):
    try:
        r = requests.get(url, headers=headers, timeout=6)
        r.raise_for_status()
    except:
        return "Unknown Article", "Failed to fetch content", url

    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.string if soup.title else "Untitled"
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]

    content = "\n\n".join(paragraphs[:6]) if paragraphs else "No content"
    return title[:200], content[:3000], r.url

# ------------------- Routes -------------------
@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login_page"))

    conn, cur = get_db()
    if not conn:
        return "DB ERROR"

    cur.execute("SELECT * FROM Articles ORDER BY publish_date DESC LIMIT 50")
    arts = cur.fetchall()
    conn.close()

    safe = [a for a in arts if a["trust_score"] >= 60]
    risky = [a for a in arts if a["trust_score"] < 60]

    return render_template(
        "index.html",
        safe_news=safe,
        risky_news=risky,
        name=session.get("name"),
        today=date.today(),
    )

# ------------ Signup ------------
@app.route("/signup_page")
def signup_page():
    return render_template("signup.html")

@app.route("/signup", methods=["POST"])
def signup():
    name = request.form["name"]
    email = request.form["email"]
    password = generate_password_hash(request.form["password"])

    conn, cur = get_db()
    cur.execute("SELECT * FROM Users WHERE email=%s", (email,))
    if cur.fetchone():
        flash("Email exists", "danger")
        return redirect(url_for("signup_page"))

    cur.execute(
        "INSERT INTO Users (name,email,password,created_at) VALUES (%s,%s,%s,NOW())",
        (name, email, password)
    )
    conn.commit()
    conn.close()
    flash("Signup successful", "success")

    return redirect(url_for("login_page"))

# ------------ Login ------------
@app.route("/login_page")
def login_page():
    return render_template("login.html")

@app.route("/login_user", methods=["POST"])
def login_user():
    email = request.form["email"]
    password = request.form["password"]

    conn, cur = get_db()
    cur.execute("SELECT * FROM Users WHERE email=%s", (email,))
    user = cur.fetchone()

    if user and check_password_hash(user["password"], password):
        session["user_id"] = user["user_id"]
        session["name"] = user["name"]
        return redirect(url_for("home"))

    flash("Invalid login", "danger")
    return redirect(url_for("login_page"))

# ------------ Add Article ------------
@app.route("/add_article", methods=["POST"])
def add_article():
    title = request.form["title"]
    content = request.form["content"]
    url = request.form["url"]
    date_p = request.form.get("publish_date") or datetime.utcnow()

    score = simple_score(content)

    conn, cur = get_db()
    cur.execute(
        "INSERT INTO Articles (title,content,url,publish_date,trust_score,created_at)"
        " VALUES (%s,%s,%s,%s,%s,NOW())",
        (title, content, url, date_p, score)
    )
    conn.commit()
    conn.close()

    flash("Article added", "success")
    return redirect(url_for("home"))

# ------------ Check Online Article ------------
@app.route("/check_online", methods=["POST"])
def check_online():
    url = request.form["url_link"]
    title, content, final_url = fetch_article(url)

    score = simple_score(content)

    conn, cur = get_db()
    cur.execute(
        "INSERT INTO Articles (title,content,url,publish_date,trust_score,created_at)"
        " VALUES (%s,%s,%s,NOW(),%s,NOW())",
        (title, content, final_url, score)
    )
    conn.commit()
    conn.close()

    flash(f"Score: {score}", "info")
    return redirect(url_for("home"))

# ------------ Logout ------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/health")
def health():
    return {"status": "ok"}

# ------------------- Run -------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
