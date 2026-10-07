from dotenv import load_dotenv
import os
load_dotenv()
from flask import jsonify, Flask, render_template, send_from_directory, request, redirect, session, flash
from flask_wtf.csrf import CSRFProtect
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
csrf = CSRFProtect(app)


@app.before_request
def maintenance_gate():
    # Admin login must always remain accessible.
    if request.path == "/admin/login":
        return None

    connection = get_db()
    setting = connection.execute(
        "SELECT value FROM settings WHERE key = 'maintenance_mode'"
    ).fetchone()
    connection.close()

    maintenance_enabled = setting and setting["value"] == "1"

    if not maintenance_enabled:
        return None

    # Logged-in admins can continue using the admin panel.
    # Do not bypass normal user pages such as /login.
    if (request.path == "/admin" or request.path.startswith("/admin/")) and "admin_id" in session:
        return None

    # Everyone else sees the maintenance page.
    return render_template("maintenance.html"), 503

app.secret_key = os.getenv("SECRET_KEY")

VIDEO_FOLDER = "videos"
PROFILE_FOLDER = "static/profile"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

ALLOWED_VIDEO_EXTENSIONS = {
    "mp4",
    "webm",
    "mov",
    "mkv"
}


def allowed_video(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_VIDEO_EXTENSIONS
    )
DATABASE = "database.db"


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def get_maintenance_mode():
    connection = get_db()
    setting = connection.execute(
        "SELECT value FROM settings WHERE key = 'maintenance_mode'"
    ).fetchone()
    connection.close()

    return bool(setting and setting["value"] == "1")


def init_database():
    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER,
            password TEXT NOT NULL,
            bio TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            user_id INTEGER,
            uploaded_by_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL,
            video_id INTEGER NOT NULL,
            UNIQUE(user_id, video_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(video_id) REFERENCES videos(id)
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            video_id INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(video_id) REFERENCES videos(id)
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    connection.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('maintenance_mode', '0')
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            video_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_one_id INTEGER NOT NULL,
            user_two_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_one_id, user_two_id)
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
        ON messages(conversation_id, created_at)
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_conversation_read
        ON messages(conversation_id, is_read)
    """)

    for column, definition in [
        ("bio", "TEXT DEFAULT ''"),
        ("avatar", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'active'"),
        ("role", "TEXT DEFAULT 'user'")
    ]:
        try:
            connection.execute(
                f"ALTER TABLE users ADD COLUMN {column} {definition}"
            )
        except sqlite3.OperationalError:
            pass

    for column, definition in [
        ("user_id", "INTEGER"),
        ("uploaded_by_admin", "INTEGER DEFAULT 0")
    ]:
        try:
            connection.execute(
                f"ALTER TABLE videos ADD COLUMN {column} {definition}"
            )
        except sqlite3.OperationalError:
            pass

    connection.commit()
    connection.close()

def sync_videos():
    connection = get_db()

    for filename in os.listdir(VIDEO_FOLDER):

        if filename.lower().endswith(
            (".mp4", ".webm", ".mkv", ".mov")
        ):

            existing = connection.execute(
                "SELECT id FROM videos WHERE filename = ?",
                (filename,)
            ).fetchone()

            if not existing:

                title = os.path.splitext(filename)[0]

                connection.execute(
                    """
                    INSERT INTO videos
                    (filename, title)
                    VALUES (?, ?)
                    """,
                    (filename, title)
                )

    connection.commit()
    connection.close()

# =========================
# USER WEBSITE
# =========================

@app.route("/")
def home():

    if "user_id" not in session:
        return redirect("/login")

    connection = get_db()

    videos = connection.execute(
        """
        SELECT videos.id,
               videos.filename,
               videos.title,
               videos.user_id,
               videos.uploaded_by_admin,
               users.name AS uploader_name
        FROM videos
        LEFT JOIN users
        ON videos.user_id = users.id
        ORDER BY videos.id DESC
        """
    ).fetchall()

    comments = connection.execute(
        """
        SELECT comments.video_id,
               comments.comment,
               comments.created_at,
               users.name
        FROM comments
        JOIN users
        ON comments.user_id = users.id
        ORDER BY comments.id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "index.html",
        videos=videos,
        comments=comments,
        username=session.get("username")
    )

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form["name"].strip()
        age = request.form["age"]
        password = request.form["password"]

        connection = get_db()

        existing_user = connection.execute(
            "SELECT id FROM users WHERE name = ?",
            (name,)
        ).fetchone()

        if existing_user:
            connection.close()
            return "Username already exists."

        password_hash = generate_password_hash(password)

        connection.execute(
            """
            INSERT INTO users
            (name, age, password)
            VALUES (?, ?, ?)
            """,
            (name, age, password_hash)
        )

        connection.commit()
        connection.close()

        return redirect("/login")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        name = request.form["name"].strip()
        password = request.form["password"]

        connection = get_db()

        user = connection.execute(
            "SELECT * FROM users WHERE name = ?",
            (name,)
        ).fetchone()

        if user and check_password_hash(
            user["password"],
            password
        ):

            if user["status"] == "banned":
                connection.close()
                return render_template(
                    "login.html",
                    error="Your account has been banned by an administrator."
                ), 403

            session["user_id"] = user["id"]
            session["username"] = user["name"]

            connection.close()
            return redirect("/hub")

        connection.close()

        return render_template(
            "login.html",
            error="Incorrect username or password.",
            entered_name=name
        ), 401

    return render_template("login.html")


@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


@app.route("/profile")
def profile():

    if "user_id" not in session:
        return redirect("/login")

    connection = get_db()

    user = connection.execute(
        """
        SELECT id, name, age, bio, avatar, created_at
        FROM users
        WHERE id = ?
        """,
        (session["user_id"],)
    ).fetchone()

    if not user:
        connection.close()
        session.clear()
        return redirect("/login")

    my_videos = connection.execute(
        """
        SELECT
            videos.id,
            videos.filename,
            videos.title,
            videos.created_at,
            (SELECT COUNT(*) FROM views WHERE views.video_id = videos.id) AS view_count,
            (SELECT COUNT(*) FROM likes WHERE likes.video_id = videos.id) AS like_count,
            (SELECT COUNT(*) FROM comments WHERE comments.video_id = videos.id) AS comment_count
        FROM videos
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (session["user_id"],)
    ).fetchall()

    video_count = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM videos
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    like_count = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM likes
        WHERE video_id IN (
            SELECT id FROM videos WHERE user_id = ?
        )
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    view_count = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM views
        WHERE video_id IN (
            SELECT id FROM videos WHERE user_id = ?
        )
        """,
        (session["user_id"],)
    ).fetchone()["count"]

    connection.close()

    return render_template(
        "profile.html",
        user=user,
        my_videos=my_videos,
        video_count=video_count,
        like_count=like_count,
        view_count=view_count
    )

@app.route("/delete-video/<int:video_id>", methods=["POST"])
def delete_own_video(video_id):

    if "user_id" not in session:
        return redirect("/login")

    connection = get_db()

    video = connection.execute(
        """
        SELECT id, filename
        FROM videos
        WHERE id = ? AND user_id = ?
        """,
        (video_id, session["user_id"])
    ).fetchone()

    if not video:
        connection.close()
        return "You can only delete your own videos.", 403

    connection.execute(
        "DELETE FROM likes WHERE video_id = ?",
        (video_id,)
    )

    connection.execute(
        "DELETE FROM comments WHERE video_id = ?",
        (video_id,)
    )

    connection.execute(
        "DELETE FROM views WHERE video_id = ?",
        (video_id,)
    )

    connection.execute(
        "DELETE FROM videos WHERE id = ? AND user_id = ?",
        (video_id, session["user_id"])
    )

    connection.commit()
    connection.close()

    video_path = os.path.join(VIDEO_FOLDER, video["filename"])

    if os.path.exists(video_path):
        os.remove(video_path)

    return redirect("/profile")

@app.route("/profile/edit", methods=["GET", "POST"])
def edit_profile():

    if "user_id" not in session:
        return redirect("/login")

    connection = get_db()

    user = connection.execute(
        "SELECT * FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    if not user:
        connection.close()
        session.clear()
        return redirect("/login")

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        age = request.form.get("age", "").strip()
        bio = request.form.get("bio", "").strip()

        if not name:
            connection.close()
            return "Username cannot be empty.", 400

        existing = connection.execute(
            "SELECT id FROM users WHERE name = ? AND id != ?",
            (name, session["user_id"])
        ).fetchone()

        if existing:
            connection.close()
            return "Username already exists.", 400

        connection.execute(
            """
            UPDATE users
            SET name = ?, age = ?, bio = ?
            WHERE id = ?
            """,
            (
                name,
                age if age else None,
                bio,
                session["user_id"]
            )
        )

        connection.commit()
        connection.close()

        session["username"] = name

        return redirect("/profile")

    connection.close()

    return render_template(
        "edit_profile.html",
        user=user
    )


@app.route("/profile/avatar", methods=["POST"])
def upload_avatar():

    if "user_id" not in session:
        return redirect("/login")

    image = request.files.get("avatar")

    if not image or not image.filename:
        return redirect("/profile/edit")

    extension = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else ""

    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return "Invalid image format.", 400

    filename = "user_" + str(session["user_id"]) + "." + extension

    os.makedirs(PROFILE_FOLDER, exist_ok=True)

    image.save(os.path.join(PROFILE_FOLDER, filename))

    connection = get_db()
    connection.execute(
        "UPDATE users SET avatar = ? WHERE id = ?",
        (filename, session["user_id"])
    )
    connection.commit()
    connection.close()

    return redirect("/profile")


@app.route("/watch/<path:filename>")
def watch(filename):

    if "user_id" not in session:
        return redirect("/login")

    connection = get_db()

    video = connection.execute(
        """
        SELECT id, filename, title
        FROM videos
        WHERE filename = ?
        """,
        (filename,)
    ).fetchone()

    if not video:
        connection.close()
        return "Video not found.", 404

    connection.execute(
        """
        INSERT INTO views
        (user_id, video_id)
        VALUES (?, ?)
        """,
        (session["user_id"], video["id"])
    )

    connection.commit()

    videos = connection.execute(
        """
        SELECT id, filename, title
        FROM videos
        ORDER BY id DESC
        """
    ).fetchall()

    comments = connection.execute(
        """
        SELECT comments.comment,
               comments.created_at,
               users.name
        FROM comments
        JOIN users
        ON comments.user_id = users.id
        WHERE comments.video_id = ?
        ORDER BY comments.id DESC
        """,
        (video["id"],)
    ).fetchall()

    connection.close()

    return render_template(
        "video.html",
        filename=video["filename"],
        video_id=video["id"],
        title=video["title"],
        videos=videos,
 	comments=comments
    )

@app.route("/upload", methods=["GET", "POST"])
def upload_video():

    if "user_id" not in session:
        return redirect("/login")

    if request.method == "POST":

        video_file = request.files.get("video")
        title = request.form.get("title", "").strip()

        if not video_file or not video_file.filename:
            return "Please select a video.", 400

        if not allowed_video(video_file.filename):
            return "Unsupported video format.", 400

        if not title:
            title = video_file.filename.rsplit(".", 1)[0]

        filename = os.path.basename(video_file.filename)

        connection = get_db()

        existing = connection.execute(
            """
            SELECT id
            FROM videos
            WHERE filename = ?
            """,
            (filename,)
        ).fetchone()

        if existing:
            connection.close()
            return "A video with this filename already exists.", 400

        save_path = os.path.join(
            VIDEO_FOLDER,
            filename
        )

        video_file.save(save_path)

        connection.execute(
            """
            INSERT INTO videos
            (filename, title, user_id)
            VALUES (?, ?, ?)
            """,
            (
                filename,
                title,
                session["user_id"]
            )
        )

        connection.commit()
        connection.close()

        return redirect("/")

    return render_template("upload.html")


@app.route("/videos/<path:filename>")
def serve_video(filename):

    return send_from_directory(
        VIDEO_FOLDER,
        filename
    )

# =========================
# ADMIN LOGIN
# =========================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if "admin_id" in session:
        return redirect("/admin")

    if request.method == "POST":

        username = request.form["username"].strip()
        password = request.form["password"]

        connection = get_db()

        admin = connection.execute(
            """
            SELECT *
            FROM admins
            WHERE username = ?
            """,
            (username,)
        ).fetchone()

        connection.close()

        if admin and check_password_hash(
            admin["password_hash"],
            password
        ):
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]

            return redirect("/admin")

        return render_template(
            "admin/login.html",
            error="Incorrect admin username or password."
        ), 401

    return render_template("admin/login.html")


# =========================
# ADMIN DASHBOARD
# =========================

@app.route("/admin/maintenance/toggle", methods=["POST"])
def admin_toggle_maintenance():
    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    current = connection.execute(
        "SELECT value FROM settings WHERE key = 'maintenance_mode'"
    ).fetchone()

    current_value = current["value"] if current else "0"
    new_value = "0" if current_value == "1" else "1"

    connection.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('maintenance_mode', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (new_value,)
    )

    connection.commit()
    connection.close()

    return redirect("/admin")


@app.route("/admin")
def admin_dashboard():

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    total_users = connection.execute(
        "SELECT COUNT(*) AS count FROM users"
    ).fetchone()["count"]

    total_videos = connection.execute(
        "SELECT COUNT(*) AS count FROM videos"
    ).fetchone()["count"]

    total_likes = connection.execute(
        "SELECT COUNT(*) AS count FROM likes"
    ).fetchone()["count"]

    total_comments = connection.execute(
        "SELECT COUNT(*) AS count FROM comments"
    ).fetchone()["count"]

    recent_user_uploads = connection.execute(
        """
        SELECT videos.title,
               videos.filename,
               videos.created_at,
               users.name AS uploader_name
        FROM videos
        LEFT JOIN users
        ON videos.user_id = users.id
        WHERE videos.uploaded_by_admin = 0
        ORDER BY videos.id DESC
        LIMIT 10
        """
    ).fetchall()

    recent_admin_uploads = connection.execute(
        """
        SELECT videos.title,
               videos.filename,
               videos.created_at
        FROM videos
        WHERE videos.uploaded_by_admin = 1
        ORDER BY videos.id DESC
        LIMIT 10
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        total_videos=total_videos,
        total_likes=total_likes,
        total_comments=total_comments,
        recent_user_uploads=recent_user_uploads,
        recent_admin_uploads=recent_admin_uploads,
        admin_username=session.get("admin_username"),
        maintenance_mode=get_maintenance_mode()
    )


# =========================
# ADMIN USERS
# =========================

@app.route("/admin/users")
def admin_users():

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    users = connection.execute(
        """
        SELECT id, name, age, status, role, created_at
        FROM users
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin/users.html",
        users=users,
        admin_username=session.get("admin_username")
    )

# =========================
# ADMIN USER MANAGEMENT
# =========================

@app.route("/admin/users/<int:user_id>/ban", methods=["POST"])
def admin_ban_user(user_id):

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    user = connection.execute(
        "SELECT id FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        connection.close()
        return "User not found.", 404

    connection.execute(
        "UPDATE users SET status = 'banned' WHERE id = ?",
        (user_id,)
    )

    connection.commit()
    connection.close()

    return redirect("/admin/users")


@app.route("/admin/users/<int:user_id>/unban", methods=["POST"])
def admin_unban_user(user_id):

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    connection.execute(
        "UPDATE users SET status = 'active' WHERE id = ?",
        (user_id,)
    )

    connection.commit()
    connection.close()

    return redirect("/admin/users")


@app.route("/admin/users/<int:user_id>/manager", methods=["POST"])
def admin_make_manager(user_id):

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    user = connection.execute(
        "SELECT id FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        connection.close()
        return "User not found.", 404

    connection.execute(
        "UPDATE users SET role = 'manager' WHERE id = ?",
        (user_id,)
    )

    connection.commit()
    connection.close()

    return redirect("/admin/users")


@app.route("/admin/users/<int:user_id>/remove-manager", methods=["POST"])
def admin_remove_manager(user_id):

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    connection.execute(
        "UPDATE users SET role = 'user' WHERE id = ?",
        (user_id,)
    )

    connection.commit()
    connection.close()

    return redirect("/admin/users")


# =========================
# LIKE SYSTEM
# =========================

@app.route("/like/<path:filename>", methods=["POST"])
def like_video(filename):

    if "user_id" not in session:
        return redirect("/login")

    connection = get_db()

    # Find video by filename
    video = connection.execute(
        """
        SELECT id
        FROM videos
        WHERE filename = ?
        """,
        (filename,)
    ).fetchone()

    if not video:
        connection.close()
        return "Video not found.", 404

    video_id = video["id"]

    # Check existing like
    existing_like = connection.execute(
        """
        SELECT 1
        FROM likes
        WHERE user_id = ? AND video_id = ?
        """,
        (session["user_id"], video_id)
    ).fetchone()

    if existing_like:

        # Unlike
        connection.execute(
            """
            DELETE FROM likes
            WHERE user_id = ? AND video_id = ?
            """,
            (session["user_id"], video_id)
        )

    else:

        # Like
        connection.execute(
            """
            INSERT INTO likes
            (user_id, video_id)
            VALUES (?, ?)
            """,
            (session["user_id"], video_id)
        )

    connection.commit()

    like_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM likes
        WHERE video_id = ?
        """,
        (video_id,)
    ).fetchone()[0]

    connection.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "liked": not bool(existing_like),
            "count": like_count
        })

    if request.referrer and "/watch/" in request.referrer:
        return redirect(f"/watch/{filename}")

    return redirect("/")

@app.context_processor
def like_data():

    if "user_id" not in session:
        return {
            "user_likes": set(),
            "like_counts": {}
        }

    connection = get_db()

    liked_rows = connection.execute(
        """
        SELECT video_id
        FROM likes
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchall()

    count_rows = connection.execute(
        """
        SELECT video_id, COUNT(*) AS count
        FROM likes
        GROUP BY video_id
        """
    ).fetchall()

    connection.close()

    user_likes = {
        row["video_id"]
        for row in liked_rows
    }

    like_counts = {
        row["video_id"]: row["count"]
        for row in count_rows
    }

    return {
        "user_likes": user_likes,
        "like_counts": like_counts
    }

@app.route("/admin/upload", methods=["GET", "POST"])
def admin_upload():

    if "admin_id" not in session:
        return redirect("/admin/login")

    if request.method == "POST":

        video_file = request.files.get("video")
        title = request.form.get("title", "").strip()

        if not video_file:
            return "Please select a video.", 400

        if video_file.filename == "":
            return "No video selected.", 400

        if not allowed_video(video_file.filename):
            return "Unsupported video format.", 400

        if not title:
            title = video_file.filename.rsplit(".", 1)[0]

        filename = video_file.filename

        # Prevent dangerous paths
        filename = os.path.basename(filename)

        connection = get_db()

        existing = connection.execute(
            """
            SELECT id
            FROM videos
            WHERE filename = ?
            """,
            (filename,)
        ).fetchone()

        if not existing:

            save_path = os.path.join(
                VIDEO_FOLDER,
                filename
            )

            video_file.save(save_path)

            connection.execute(
                """
                INSERT INTO videos
                (filename, title, user_id, uploaded_by_admin)
                VALUES (?, ?, ?, ?)
                """,
                (filename, title, None, 1)
            )

            connection.commit()

        connection.close()

        return redirect("/admin/videos")

    return render_template(
        "admin/upload.html",
        admin_username=session.get("admin_username")
    )

# =========================
# ADMIN VIDEOS
# =========================

@app.route("/admin/videos")
def admin_videos():

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    videos = connection.execute(
        """
        SELECT
    videos.id,
    videos.filename,
    videos.title,
    videos.created_at,
    COUNT(DISTINCT likes.video_id) AS like_count,
    COUNT(DISTINCT views.id) AS view_count
FROM videos
LEFT JOIN likes
    ON videos.id = likes.video_id
LEFT JOIN views
    ON videos.id = views.video_id
GROUP BY
    videos.id,
    videos.filename,
    videos.title,
    videos.created_at
ORDER BY videos.id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin/videos.html",
        videos=videos,
        admin_username=session.get("admin_username")
    )


@app.route("/admin/comments/<int:comment_id>/delete", methods=["POST"])
def delete_admin_comment(comment_id):

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    connection.execute(
        "DELETE FROM comments WHERE id = ?",
        (comment_id,)
    )

    connection.commit()
    connection.close()

    return redirect("/admin/comments")


@app.route("/admin/comments")
def admin_comments():

    if "admin_id" not in session:
        return redirect("/admin/login")

    connection = get_db()

    comments = connection.execute(
        """
        SELECT comments.id,
               comments.comment,
               comments.created_at,
               users.name AS user_name,
               videos.title AS video_title,
               videos.filename AS video_filename
        FROM comments
        LEFT JOIN users
        ON comments.user_id = users.id
        LEFT JOIN videos
        ON comments.video_id = videos.id
        ORDER BY comments.id DESC
        """
    ).fetchall()

    connection.close()

    return render_template(
        "admin/comments.html",
        comments=comments,
        admin_username=session.get("admin_username")
    )


@app.route("/admin/logout")
def admin_logout():

    session.pop("admin_id", None)
    session.pop("admin_username", None)

    return redirect("/admin/login")


@app.route("/comment/<path:filename>", methods=["POST"])
def add_comment(filename):

    if "user_id" not in session:
        return redirect("/login")

    comment_text = request.form.get("comment", "").strip()

    if not comment_text:
        return redirect(f"/watch/{filename}")

    connection = get_db()

    video = connection.execute(
        """
        SELECT id
        FROM videos
        WHERE filename = ?
        """,
        (filename,)
    ).fetchone()

    if not video:
        connection.close()
        return "Video not found.", 404

    connection.execute(
        """
        INSERT INTO comments
        (user_id, video_id, comment)
        VALUES (?, ?, ?)
        """,
        (
            session["user_id"],
            video["id"],
            comment_text
        )
    )

    connection.commit()

    new_comment = connection.execute(
        """
        SELECT comments.comment,
               comments.created_at,
               users.name
        FROM comments
        JOIN users
        ON comments.user_id = users.id
        WHERE comments.user_id = ?
          AND comments.video_id = ?
        ORDER BY comments.id DESC
        LIMIT 1
        """,
        (session["user_id"], video["id"])
    ).fetchone()

    connection.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "comment": new_comment["comment"],
            "name": new_comment["name"],
            "created_at": new_comment["created_at"]
        })

    return redirect(f"/watch/{filename}")

def get_or_create_conversation(user_a, user_b):
    user_one = min(user_a, user_b)
    user_two = max(user_a, user_b)

    connection = get_db()

    conversation = connection.execute(
        """
        SELECT id
        FROM conversations
        WHERE user_one_id = ?
        AND user_two_id = ?
        """,
        (user_one, user_two)
    ).fetchone()

    if conversation:
        connection.close()
        return conversation["id"], None

    cursor = connection.execute(
        """
        INSERT INTO conversations
        (user_one_id, user_two_id)
        VALUES (?, ?)
        """,
        (user_one, user_two)
    )

    connection.commit()
    conversation_id = cursor.lastrowid
    connection.close()

    return conversation_id, None

@app.route("/chat")
def chat():
    if "user_id" not in session:
        return redirect("/login")

    current_user_id = session["user_id"]

    connection = get_db()

    users = connection.execute(
        """
        SELECT
            users.id,
            users.name,
            users.avatar,
            users.status,
            users.role,
            conversations.id AS conversation_id,

            (
                SELECT message
                FROM messages
                WHERE messages.conversation_id = conversations.id
                ORDER BY messages.id DESC
                LIMIT 1
            ) AS last_message,

            (
                SELECT created_at
                FROM messages
                WHERE messages.conversation_id = conversations.id
                ORDER BY messages.id DESC
                LIMIT 1
            ) AS last_message_time,

            (
                SELECT COUNT(*)
                FROM messages
                WHERE messages.conversation_id = conversations.id
                AND messages.sender_id != ?
                AND messages.is_read = 0
            ) AS unread_count

        FROM users

        LEFT JOIN conversations
        ON (
            (conversations.user_one_id = ? AND conversations.user_two_id = users.id)
            OR
            (conversations.user_two_id = ? AND conversations.user_one_id = users.id)
        )

        WHERE users.id != ?
        AND users.status != 'banned'

        ORDER BY
            CASE
                WHEN last_message_time IS NULL THEN 1
                ELSE 0
            END,
            last_message_time DESC,
            users.name ASC
        """,
        (
            current_user_id,
            current_user_id,
            current_user_id,
            current_user_id
        )
    ).fetchall()

    connection.close()

    return render_template(
        "chat.html",
        users=users
    )

@app.route("/chat/<int:user_id>")
def chat_with_user(user_id):
    if "user_id" not in session:
        return redirect("/login")

    if user_id == session["user_id"]:
        return redirect("/chat")

    connection = get_db()

    other_user = connection.execute(
        """
        SELECT id, name, avatar, status, role
        FROM users
        WHERE id = ?
        AND status != 'banned'
        """,
        (user_id,)
    ).fetchone()

    if not other_user:
        connection.close()
        return "User not found.", 404

    conversation_id, _ = get_or_create_conversation(
        session["user_id"],
        user_id
    )

    connection.execute(
        """
        UPDATE messages
        SET is_read = 1
        WHERE conversation_id = ?
        AND sender_id = ?
        AND is_read = 0
        """,
        (conversation_id, user_id)
    )
    connection.commit()

    messages = connection.execute(
        """
        SELECT
            messages.id,
            messages.message,
            messages.sender_id,
            messages.created_at,
            users.name AS sender_name
        FROM messages
        JOIN users
        ON messages.sender_id = users.id
        WHERE messages.conversation_id = ?
        ORDER BY messages.id ASC
        """,
        (conversation_id,)
    ).fetchall()

    connection.close()

    return render_template(
        "chat_room.html",
        other_user=other_user,
        messages=messages
    )

@app.route("/chat/<int:user_id>/send", methods=["POST"])
def send_message(user_id):
    if "user_id" not in session:
        return redirect("/login")

    if user_id == session["user_id"]:
        return redirect("/chat")

    message_text = request.form.get("message", "").strip()

    if not message_text:
        return redirect(f"/chat/{user_id}")

    if len(message_text) > 1000:
        return "Message is too long.", 400

    connection = get_db()

    other_user = connection.execute(
        """
        SELECT id
        FROM users
        WHERE id = ?
        AND status != 'banned'
        """,
        (user_id,)
    ).fetchone()

    if not other_user:
        connection.close()
        return "User not found.", 404

    connection.close()

    conversation_id, _ = get_or_create_conversation(
        session["user_id"],
        user_id
    )

    connection = get_db()

    connection.execute(
        """
        INSERT INTO messages
        (conversation_id, sender_id, message)
        VALUES (?, ?, ?)
        """,
        (
            conversation_id,
            session["user_id"],
            message_text
        )
    )

    connection.commit()
    connection.close()

    return redirect(f"/chat/{user_id}")

@app.route("/chat/<int:user_id>/messages")
def get_chat_messages(user_id):
    if "user_id" not in session:
        return {"error": "Unauthorized"}, 401

    if user_id == session["user_id"]:
        return {"error": "Invalid user"}, 400

    connection = get_db()

    conversation = connection.execute(
        """
        SELECT id
        FROM conversations
        WHERE (user_one_id = ? AND user_two_id = ?)
           OR (user_one_id = ? AND user_two_id = ?)
        """,
        (session["user_id"], user_id, user_id, session["user_id"])
    ).fetchone()

    if not conversation:
        connection.close()
        return {"messages": []}

    messages = connection.execute(
        """
        SELECT
            messages.id,
            messages.message,
            messages.sender_id,
            messages.created_at,
            users.name AS sender_name
        FROM messages
        JOIN users ON messages.sender_id = users.id
        WHERE messages.conversation_id = ?
        ORDER BY messages.id ASC
        """,
        (conversation["id"],)
    ).fetchall()

    connection.close()

    return {
        "messages": [dict(message) for message in messages]
    }


@app.route("/chat/unread-counts")
def get_unread_counts():

    if "user_id" not in session:
        return {"error": "Unauthorized"}, 401

    current_user_id = session["user_id"]

    connection = get_db()

    rows = connection.execute(
        """
        SELECT
            CASE
                WHEN conversations.user_one_id = ?
                THEN conversations.user_two_id
                ELSE conversations.user_one_id
            END AS user_id,

            COUNT(messages.id) AS unread_count

        FROM conversations

        LEFT JOIN messages
        ON messages.conversation_id = conversations.id
        AND messages.sender_id != ?
        AND messages.is_read = 0

        WHERE
            conversations.user_one_id = ?
            OR conversations.user_two_id = ?

        GROUP BY conversations.id
        """,
        (
            current_user_id,
            current_user_id,
            current_user_id,
            current_user_id
        )
    ).fetchall()

    connection.close()

    unread_counts = {
        str(row["user_id"]): row["unread_count"]
        for row in rows
    }

    return unread_counts

@app.route("/tools")
def tools():
    if "user_id" not in session:
        return redirect("/login")

    return render_template("tools.html")


@app.route("/photos")
def photos():
    if "user_id" not in session:
        return redirect("/login")

    return render_template("photos.html")


@app.route("/hub")
def hub():
    if "user_id" not in session:
        return redirect("/login")

    return render_template(
        "hub.html"
    )

# =========================
# DATABASE INITIALIZATION
# =========================

init_database()

# =========================
# START SERVER
# =========================

if __name__ == "__main__":
    sync_videos()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )


