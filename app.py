from flask import Flask, render_template, request, redirect, session, flash, url_for
import mysql.connector
from datetime import date
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

# MySQL connection
db = mysql.connector.connect(
    host="DB_HOST",
    user="DB_NAME",
    password="DB_PASS",
    database="DB_NAME"
)
cursor = db.cursor(dictionary=True)

# -------------------
# HOME PAGE
# -------------------
@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login_page"))

    cursor.execute("SELECT * FROM Articles WHERE trust_score >= 60")
    safe_news = cursor.fetchall()

    cursor.execute("SELECT * FROM Articles WHERE trust_score < 60")
    risky_news = cursor.fetchall()

    return render_template(
        "index.html",
        name=session["name"],
        safe_news=safe_news,
        risky_news=risky_news,
        today=date.today()
    )

# -------------------
# SIGNUP PAGE
# -------------------
@app.route("/signup_page")
def signup_page():
    return render_template("signup.html")

@app.route("/signup", methods=["POST"])
def signup():
    name = request.form["name"]
    email = request.form["email"]
    password = request.form["password"]

    cursor.execute("SELECT * FROM Users WHERE name=%s", (name,))
    existing_name = cursor.fetchone()

    if existing_name:
        flash("Username already in use!", "danger")
        return redirect(url_for("signup_page"))

    cursor.execute("SELECT * FROM Users WHERE email=%s", (email,))
    existing_email = cursor.fetchone()

    if existing_email:
        flash("Email already in use!", "danger")
        return redirect(url_for("signup_page"))


    cursor.execute("INSERT INTO Users (name, email, password) VALUES (%s, %s, %s)", 
                   (name, email, password))
    db.commit()
    flash("Signup successful! Please login.", "success")
    return redirect(url_for("login_page"))


# -------------------
# LOGIN PAGE
# -------------------
@app.route("/login_page")
def login_page():
    return render_template("login.html")

@app.route("/login_user", methods=["POST"])
def login_user():
    email = request.form["email"]
    password = request.form["password"]

    cursor.execute(
        "SELECT * FROM Users WHERE email=%s AND password=%s",
        (email, password)
    )
    user = cursor.fetchone()

    if user:
        session["user_id"] = user["user_id"]
        session["name"] = user["name"]
        return redirect(url_for("home"))
    else:
        flash("Invalid credentials!", "danger")
        return redirect(url_for("login_page"))

# -------------------
# LOGOUT
# -------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# -------------------
# ADD ARTICLE
# -------------------
@app.route("/add_article", methods=["POST"])
def add_article():
    title = request.form["title"]
    content = request.form["content"]
    url_link = request.form["url"]
    publish_date = request.form["publish_date"]

    # Default source id
    cursor.execute("SELECT source_id FROM Sources LIMIT 1")
    source = cursor.fetchone()
    source_id = source["source_id"] if source else 1

    cursor.execute(
        "INSERT INTO Articles (title, content, url, publish_date, source_id) VALUES (%s,%s,%s,%s,%s)",
        (title, content, url_link, publish_date, source_id)
    )
    db.commit()
    flash("Article submitted!", "success")
    return redirect(url_for("home"))

# -------------------
# REPORT ARTICLE
# -------------------
@app.route("/report_article", methods=["POST"])
def report_article():
    article_id = request.form["article_id"]
    reason = request.form["reason"]
    user_id = session["user_id"]

    cursor.execute(
        "INSERT INTO Reports (article_id, user_id, reason) VALUES (%s,%s,%s)",
        (article_id, user_id, reason)
    )
    db.commit()
    flash("Report submitted!", "success")
    return redirect(url_for("home"))

# -------------------
# CHECK TRUST SCORE
# -------------------
@app.route("/check_trust", methods=["POST"])
def check_trust():
    article_id = request.form["article_id"]
    cursor.execute("SELECT trust_score FROM Articles WHERE article_id=%s", (article_id,))
    article = cursor.fetchone()
    if article:
        flash(f"Trust Score: {article['trust_score']}", "info")
    else:
        flash("Article not found!", "danger")
    return redirect(url_for("home"))

# -------------------
# CHECK ONLINE NEWS
# -------------------
@app.route("/check_online", methods=["POST"])
def check_online():
    url_link = request.form.get("url_link")  # safer with .get()
    if not url_link:
        flash("Please provide a URL!", "warning")
        return redirect(url_for("home"))
    try:
        r = requests.get(url_link)
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator=' ', strip=True)[:500]  # first 500 chars
        fake_keywords = ["fake", "hoax", "false"]
        score = 100
        for word in fake_keywords:
            if word in text.lower():
                score -= 30
        score = max(min(score, 100), 0)
        flash(f"Online Article Trust Score (simple check): {score}", "info")
    except:
        flash("Failed to fetch online article.", "danger")
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
    )


