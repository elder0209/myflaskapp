from flask import Flask, render_template, request, redirect, session, flash, url_for
import mysql.connector
from datetime import date
import requests
from bs4 import BeautifulSoup
import os
import random
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecretkey")

# -------------------
# MYSQL CONNECTION (Safe version)
# -------------------
def get_db_connection():
    try:
        db = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            database=os.getenv("DB_NAME"),
            connection_timeout=10
        )
        cursor = db.cursor(dictionary=True)
        return db, cursor
    except mysql.connector.Error as err:
        print("❌ Database connection failed:", err)
        return None, None


# -------------------
# UTILS: FETCH ARTICLE
# -------------------
def fetch_article_text(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
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

    db, cursor = get_db_connection()
    if not db:
        flash("Database connection error!", "danger")
        return redirect(url_for("login_page"))

    cursor.execute("SELECT * FROM Articles WHERE trust_score >= 60 ORDER BY publish_date DESC")
    safe_news = cursor.fetchall()

    cursor.execute("SELECT * FROM Articles WHERE trust_score < 60 ORDER BY publish_date DESC")
    risky_news = cursor.fetchall()

    cursor.close()
    db.close()

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

    db, cursor = get_db_connection()
    if not db:
        flash("Database connection error!", "danger")
        return redirect(url_for("signup_page"))

    cursor.execute("SELECT * FROM Users WHERE name=%s OR email=%s", (name, email))
    if cursor.fetchone():
        flash("Username or Email already in use!", "danger")
        cursor.close()
        db.close()
        return redirect(url_for("signup_page"))

    hashed_password = generate_password_hash(password)
    cursor.execute("INSERT INTO Users (name, email, password) VALUES (%s, %s, %s)",
                   (name, email, hashed_password))
    db.commit()

    cursor.close()
    db.close()

    flash("Signup successful! Please login.", "success")
    return redirect(url_for("login_page"))


@app.route("/login_page")
def login_page():
    return render_template("login.html")


@app.route("/login_user", methods=["POST"])
def login_user():
    email = request.form["email"]
    password = request.form["password"]

    db, cursor = get_db_connection()
    if not db:
        flash("Database connection error!", "danger")
        return redirect(url_for("login_page"))

    cursor.execute("SELECT * FROM Users WHERE email=%s", (email,))
    user = cursor.fetchone()

    if user and check_password_hash(user["password"], password):
        session["user_id"] = user["user_id"]
        session["name"] = user["name"]
        flash("Welcome back!", "success")
        redirect_page = url_for("home")
    else:
        flash("Invalid credentials!", "danger")
        redirect_page = url_for("login_page")

    cursor.close()
    db.close()
    return redirect(redirect_page)


@app.route("/logout")
def logout():
    session.clear()
    flash("You’ve been logged out.", "info")
    return redirect(url_for("login_page"))


# -------------------
# ARTICLE / REPORT ROUTES
# -------------------
@app.route("/add_article", methods=["POST"])
def add_article():
    title = request.form["title"]
    content = request.form["content"]
    url_link = request.form["url"]
    publish_date = request.form["publish_date"]

    db, cursor = get_db_connection()
    if not db:
        flash("Database connection error!", "danger")
        return redirect(url_for("home"))

    cursor.execute("SELECT source_id FROM Sources LIMIT 1")
    source = cursor.fetchone()
    source_id = source["source_id"] if source else 1

    cursor.execute(
        "INSERT INTO Articles (title, content, url, publish_date, source_id, trust_score, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (title, content, url_link, publish_date, source_id, 50, "manual")
    )
    db.commit()
    cursor.close()
    db.close()

    flash("Article submitted!", "success")
    return redirect(url_for("home"))


@app.route("/check_trust", methods=["POST"])
def check_trust():
    article_id = request.form["article_id"]
    db, cursor = get_db_connection()
    if not db:
        flash("Database connection error!", "danger")
        return redirect(url_for("home"))

    cursor.execute("SELECT trust_score FROM Articles WHERE article_id=%s", (article_id,))
    article = cursor.fetchone()
    if article:
        flash(f"Trust Score: {article['trust_score']}", "info")
    else:
        flash("Article not found!", "danger")

    cursor.close()
    db.close()
    return redirect(url_for("home"))


@app.route("/check_online", methods=["POST"])
def check_online():
    url_link = request.form["url_link"]
    db, cursor = get_db_connection()
    if not db:
        flash("Database connection error!", "danger")
        return redirect(url_for("home"))

    try:
        title, snippet = fetch_article_text(url_link)
        score = random.randint(40, 95)
        cursor.execute(
            "INSERT INTO Articles (title, content, url, publish_date, trust_score, source) "
            "VALUES (%s, %s, %s, NOW(), %s, %s)",
            (title, snippet, url_link, score, "online")
        )
        db.commit()
        flash(f"✅ Online article checked! Trust Score: {score}", "success")
    except Exception as e:
        flash(f"❌ Failed to fetch online article. Error: {str(e)}", "danger")

    cursor.close()
    db.close()
    return redirect(url_for("home"))


@app.route("/report_article", methods=["POST"])
def report_article():
    article_id = request.form["article_id"]
    reason = request.form["reason"]
    user_id = session.get("user_id")
    if not user_id:
        flash("You must be logged in to report!", "danger")
        return redirect(url_for("login_page"))

    db, cursor = get_db_connection()
    if not db:
        flash("Database connection error!", "danger")
        return redirect(url_for("home"))

    cursor.execute(
        "INSERT INTO Reports (article_id, user_id, reason) VALUES (%s, %s, %s)",
        (article_id, user_id, reason)
    )
    db.commit()

    cursor.execute("SELECT COUNT(*) AS report_count FROM Reports WHERE article_id=%s", (article_id,))
    report_data = cursor.fetchone()
    report_count = report_data["report_count"] if report_data else 0
    new_score = max(0, 100 - (report_count * 10))

    cursor.execute("UPDATE Articles SET trust_score=%s WHERE article_id=%s", (new_score, article_id))
    db.commit()

    cursor.close()
    db.close()

    flash(f"Report submitted! Trust Score updated to {new_score}.", "success")
    return redirect(url_for("home"))


# -------------------
# RUN APP
# -------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))  # Render/Railway default port
    app.run(host="0.0.0.0", port=port, debug=False)
