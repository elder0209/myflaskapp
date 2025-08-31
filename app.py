from flask import Flask, render_template, request, redirect, session, flash, url_for
import mysql.connector
from datetime import date
import requests
from bs4 import BeautifulSoup
import os
import random
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth

print("DB_HOST from env:", os.getenv("DB_HOST"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecretkey")  # Prefer env var

# -------------------
# MYSQL CONNECTION
# -------------------
def get_db_connection():
    try:
        db = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            database=os.getenv("DB_NAME"),
            connection_timeout=20
        )
        cursor = db.cursor(dictionary=True)

        for setting in [
            "SET SESSION net_read_timeout = 600",
            "SET SESSION net_write_timeout = 600",
            "SET SESSION wait_timeout = 600",
            "SET SESSION interactive_timeout = 600"
        ]:
            try:
                cursor.execute(setting)
            except Exception as e:
                print(f"Warning: Could not apply setting {setting} - {e}")

        return db, cursor
    except mysql.connector.Error as err:
        print("❌ Database connection failed:", err)
        return None, None

db, cursor = get_db_connection()

# -------------------
# GOOGLE OAUTH SETUP (Authlib)
# -------------------
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    access_token_url='https://accounts.google.com/o/oauth2/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://www.googleapis.com/oauth2/v1/userinfo',
    client_kwargs={'scope': 'openid email profile'}
)

# -------------------
# UTILS: FETCH ARTICLE
# -------------------
def fetch_article_text(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return "Untitled Online Article", f"❌ Failed to fetch article: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title else "Untitled Online Article"
    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    content = " ".join(paragraphs[:6]) if paragraphs else "⚠️ No content found"
    return title[:150], content[:800]

# -------------------
# ROUTES
# -------------------
@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login_page"))

    cursor.execute("SELECT * FROM Articles WHERE trust_score >= 60 ORDER BY publish_date DESC")
    safe_news = cursor.fetchall()

    cursor.execute("SELECT * FROM Articles WHERE trust_score < 60 ORDER BY publish_date DESC")
    risky_news = cursor.fetchall()

    return render_template(
        "index.html",
        name=session.get("name"),
        safe_news=safe_news,
        risky_news=risky_news,
        today=date.today(),
    )

# -------------------
# SIGNUP / LOGIN
# -------------------
@app.route("/signup_page")
def signup_page():
    return render_template("signup.html")

@app.route("/signup", methods=["POST"])
def signup():
    name = request.form["name"]
    email = request.form["email"]
    password = request.form["password"]

    cursor.execute("SELECT * FROM Users WHERE name=%s OR email=%s", (name, email))
    if cursor.fetchone():
        flash("Username or Email already in use!", "danger")
        return redirect(url_for("signup_page"))

    hashed_password = generate_password_hash(password)
    cursor.execute("INSERT INTO Users (name, email, password) VALUES (%s, %s, %s)",
                   (name, email, hashed_password))
    db.commit()
    flash("Signup successful! Please login.", "success")
    return redirect(url_for("login_page"))

@app.route("/login_page")
def login_page():
    return render_template("login.html")

@app.route("/login_user", methods=["POST"])
def login_user():
    email = request.form["email"]
    password = request.form["password"]

    cursor.execute("SELECT * FROM Users WHERE email=%s", (email,))
    user = cursor.fetchone()

    if user and check_password_hash(user["password"], password):
        session["user_id"] = user["user_id"]
        session["name"] = user["name"]
        return redirect(url_for("home"))
    else:
        flash("Invalid credentials!", "danger")
        return redirect(url_for("login_page"))

# -------------------
# GOOGLE LOGIN ROUTES
# -------------------
@app.route("/login/google")
def login_google():
    redirect_uri = url_for('authorized_google', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/login/callback")
def authorized_google():
    token = google.authorize_access_token()
    user_info = google.get('userinfo').json()

    email = user_info['email']
    name = user_info.get('name', email.split("@")[0])

    cursor.execute("SELECT * FROM Users WHERE email=%s", (email,))
    user = cursor.fetchone()

    if not user:
        # Create user if doesn't exist
        cursor.execute("INSERT INTO Users (name, email) VALUES (%s, %s)", (name, email))
        db.commit()
        cursor.execute("SELECT * FROM Users WHERE email=%s", (email,))
        user = cursor.fetchone()

    session["user_id"] = user["user_id"]
    session["name"] = user["name"]
    flash(f"Logged in as {name}", "success")
    return redirect(url_for("home"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# -------------------
# ARTICLE / REPORT / CHECK ONLINE ROUTES
# -------------------
# Copy your existing article routes (add_article, check_trust, check_online, report_article) here
# unchanged from your original code.

# -------------------
# RUN APP
# -------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
