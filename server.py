from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import sqlite3
import zlib
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import qrcode
from PIL import Image
from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, session

ROOT = Path(__file__).parent
DB_PATH = ROOT / "ag_consulting.sqlite3"
BACKUP_DIR = ROOT / "backups"
app = Flask(__name__, static_folder=str(ROOT), static_url_path="")
app.secret_key = os.environ.get("ONPOINT_SECRET_KEY", secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

USERS = {
    "admin": {"password": "onpoint-admin", "role": "admin", "label": "Administrateur"},
    "sales": {"password": "onpoint-sales", "role": "sales", "label": "Ventes"},
    "viewer": {"password": "onpoint-viewer", "role": "viewer", "label": "Lecture seule"},
}
ROLE_LEVEL = {"viewer": 1, "sales": 2, "admin": 3}
CATEGORIES = {"Google", "Google EN", "Instagram", "Facebook", "TikTok", "WiFi"}
LIFECYCLES = {"stock", "assigned", "delivered_unpaid", "paid", "disabled", "archived"}
LIFECYCLE_LABELS = {"stock": "En stock", "assigned": "Attribuee", "delivered_unpaid": "Livree, non payee", "paid": "Payee", "disabled": "Desactivee", "archived": "Archivee", "free": "En stock", "active": "Payee", "unpaid": "Livree, non payee"}

SEED_PLAQUES = [
    ("28XK", "Google", "Livree, non payee", "16/09/2026", "maison sucre", "https://search.google.com/local/writereview?placeid=demo-28xk", 13, "delivered_unpaid", ""),
    ("2BGK", "Google", "Vendue", "16/09/2026", "la tavola", "https://search.google.com/local/writereview?placeid=demo-2bgk", 6, "paid", "+216 22 410 884"),
    ("2T89", "WiFi", "Vendue", "26/09/2026", "gass", "WiFi : TOPNET_1870", 3, "paid", "+216 55 321 901"),
    ("33DG", "Google", "Vendue", "23/09/2026", "bey palace", "https://search.google.com/local/writereview?placeid=demo-33dg", 22, "paid", "+216 71 800 221"),
    ("4KF3", "Google", "Vendue", "16/09/2026", "Cactus Agence Immobiliere", "https://search.google.com/local/writereview?placeid=demo-4kf3", 4, "paid", "+216 70 700 400"),
    ("4SDS", "Google", "Vendue", "16/09/2026", "Hibiscus immobiliere", "https://search.google.com/local/writereview?placeid=demo-4sds", 1, "paid", "+216 72 291 020"),
    ("4WFL", "Google", "Livree, non payee", "22/09/2026", "masterpool8", "https://linktr.ee/masterspool", 12, "delivered_unpaid", "+216 98 222 113"),
    ("4X6S", "Google", "Livree, non payee", "16/09/2026", "carolina cafe", "https://search.google.com/local/writereview?placeid=demo-4x6s", 9, "delivered_unpaid", ""),
    ("4ZTN", "Google", "Vendue", "23/09/2026", "bakhti pizzarie", "https://search.google.com/local/writereview?placeid=demo-4ztn", 40, "paid", "+216 25 100 776"),
    ("6HP6", "Google", "Vendue", "20/09/2026", "Style frip", "https://search.google.com/local/writereview?placeid=demo-6hp6", 12, "paid", ""),
    ("7ZFK", "Google", "Vendue", "19/09/2026", "Trancetta Nabeul", "https://search.google.com/local/writereview?placeid=demo-7zfk", 8, "paid", ""),
    ("8DEP", "Google", "Vendue", "11/09/2026", "l'empereur", "https://search.google.com/local/writereview?placeid=demo-8dep", 5, "paid", ""),
]
ORDER_NAMES = ["TEST Claude - e-mail OK ?", "TEST Claude - email 4", "TEST Claude - email 3", "TEST Claude - email 2", "TEST Claude - email", "TEST Claude - a annuler", "TEST deploiement", "Commande test"]
ORDER_CODES = ["CMD-LFNV4", "CMD-SWVNN", "CMD-TLTPT", "CMD-F24MH", "CMD-KDFVS", "CMD-BHX2C", "CMD-EDFNV", "CMD-2PQLK"]


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def backup_db(reason="manual"):
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(exist_ok=True)
    target = BACKUP_DIR / f"ag_consulting_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{reason}.sqlite3"
    shutil.copy2(DB_PATH, target)
    backups = sorted(BACKUP_DIR.glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old in backups[20:]:
        old.unlink(missing_ok=True)
    return target.name


def init_db():
    with db() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS plaques (code TEXT PRIMARY KEY, type TEXT NOT NULL, sale TEXT NOT NULL, date TEXT NOT NULL, client TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '', scans INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'free', phone TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (code TEXT PRIMARY KEY, name TEXT NOT NULL, price TEXT NOT NULL, status TEXT NOT NULL, date TEXT NOT NULL, phone TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, scanned_at TEXT NOT NULL, user_agent TEXT NOT NULL DEFAULT '', referrer TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '');
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(plaques)")}
        if "lifecycle" not in columns:
            connection.execute("ALTER TABLE plaques ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'stock'")
            connection.execute("UPDATE plaques SET lifecycle = CASE WHEN status = 'free' THEN 'stock' WHEN status = 'unpaid' THEN 'delivered_unpaid' WHEN status = 'active' THEN 'paid' ELSE 'stock' END")
        else:
            connection.execute("UPDATE plaques SET lifecycle = CASE WHEN lifecycle IN ('free', 'stock') THEN 'stock' WHEN lifecycle IN ('active', 'paid') THEN 'paid' WHEN lifecycle IN ('unpaid', 'delivered_unpaid') THEN 'delivered_unpaid' WHEN lifecycle IN ('assigned', 'disabled', 'archived') THEN lifecycle ELSE 'stock' END")
        scan_columns = {row[1] for row in connection.execute("PRAGMA table_info(scans)")}
        for column in ("user_agent", "referrer", "country", "city"):
            if column not in scan_columns:
                connection.execute(f"ALTER TABLE scans ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if connection.execute("SELECT COUNT(*) FROM plaques").fetchone()[0] == 0:
            now = datetime.now().isoformat(timespec="seconds")
            connection.executemany("INSERT INTO plaques (code,type,sale,date,client,destination,scans,status,phone,lifecycle,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", [(*row[:7], "active" if row[7] in {"assigned", "paid"} else "unpaid" if row[7] == "delivered_unpaid" else "free", row[8], row[7], now) for row in SEED_PLAQUES])
        if connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
            connection.executemany("INSERT INTO orders (code,name,price,status,date,phone) VALUES (?,?,?,?,?,?)", [(code, name, "148 DT" if index == 5 else "59 DT", "cancelled", date, "56 680 248") for index, (code, name, date) in enumerate(zip(ORDER_CODES, ORDER_NAMES, ["29/09 13:05", "29/09 12:32", "29/09 12:31", "29/09 12:27", "29/09 12:25", "29/09 11:44", "28/09 17:10", "28/09 16:42"]))])
    backup_db("startup")


def current_user():
    username = session.get("username")
    user = USERS.get(username)
    return {"username": username, **user} if user else None


def require_role(role="viewer"):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify({"error": "Authentification requise."}), 401
            if ROLE_LEVEL[user["role"]] < ROLE_LEVEL[role]:
                return jsonify({"error": "Permission insuffisante."}), 403
            return function(*args, **kwargs)
        return wrapped
    return decorator


def code_exists(code):
    with db() as connection:
        return connection.execute("SELECT 1 FROM plaques WHERE code = ?", (code,)).fetchone() is not None


def new_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "AGC-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not code_exists(code):
            return code


def plaque_dict(row):
    item = dict(row)
    item["lifecycle_label"] = LIFECYCLE_LABELS.get(item.get("lifecycle"), item.get("lifecycle"))
    item["qr_url"] = f"/qrcode/{item['code']}"
    item["qr_image_url"] = f"/api/plaques/{item['code']}/qr"
    item["qr_svg_url"] = f"/api/plaques/{item['code']}/qr.svg"
    item["qr_pdf_url"] = f"/api/plaques/{item['code']}/qr.pdf"
    return item


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    user = USERS.get(payload.get("username", ""))
    if not user or not secrets.compare_digest(user["password"], str(payload.get("password", ""))):
        return jsonify({"error": "Identifiants invalides."}), 401
    session["username"] = payload["username"]
    return jsonify({"username": payload["username"], "role": user["role"], "label": user["label"]})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/session")
def session_info():
    user = current_user()
    return jsonify({"authenticated": bool(user), "user": user})


@app.get("/api/dashboard")
@require_role()
def dashboard():
    with db() as connection:
        plaques = [plaque_dict(row) for row in connection.execute("SELECT * FROM plaques ORDER BY created_at DESC, code").fetchall()]
        orders = [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY rowid DESC").fetchall()]
        metrics = {"total": len(plaques), "active": sum(row["lifecycle"] in {"assigned", "delivered_unpaid", "paid"} for row in plaques), "free": sum(row["lifecycle"] == "stock" for row in plaques), "unpaid": sum(row["lifecycle"] == "delivered_unpaid" for row in plaques), "sold": sum(row["lifecycle"] == "paid" for row in plaques), "scans": sum(row["scans"] for row in plaques)}
        messages = [{"client": row["client"], "code": row["code"], "month": row["scans"], "total": row["scans"], "last": "jamais" if not row["phone"] else "récent"} for row in plaques if row["scans"] > 0]
        lots = []
        for category in sorted(CATEGORIES):
            category_rows = [row for row in plaques if row["type"] == category]
            count = len(category_rows)
            lots.append({"type": category, "plaques": count, "active": sum(row["lifecycle"] in {"assigned", "delivered_unpaid", "paid"} for row in category_rows), "free": sum(row["lifecycle"] == "stock" for row in category_rows), "state": "Bas" if count < 10 else "OK"})
        stock = [{"type": category, "count": 167 if category == "Google" else 50 if category == "Google EN" else 18 if category == "Instagram" else 4 if category in {"Facebook", "TikTok"} else 9, "low": category in {"Facebook", "TikTok", "WiFi"}} for category in sorted(CATEGORIES)]
    return jsonify({"plaques": plaques, "orders": orders, "messages": messages[:8], "lots": lots, "metrics": metrics, "stock": stock, "user": current_user()})


@app.get("/api/analytics")
@require_role()
def analytics():
    try:
        days = max(1, min(365, int(request.args.get("days", 30))))
    except ValueError:
        days = 30
    cutoff = datetime.now() - timedelta(days=days)
    with db() as connection:
        rows = connection.execute("SELECT code, scanned_at, user_agent, referrer, country, city FROM scans WHERE scanned_at >= ? ORDER BY scanned_at", (cutoff.isoformat(timespec="seconds"),)).fetchall()
    daily = {}
    devices = {}
    referrers = {}
    locations = {}
    for row in rows:
        day = row["scanned_at"][:10]
        daily[day] = daily.get(day, 0) + 1
        device = "Mobile" if any(token in row["user_agent"].lower() for token in ("mobile", "android", "iphone")) else "Desktop"
        devices[device] = devices.get(device, 0) + 1
        referrer = row["referrer"] or "Direct"
        referrers[referrer] = referrers.get(referrer, 0) + 1
        location = ", ".join(value for value in (row["city"], row["country"]) if value) or "Non disponible"
        locations[location] = locations.get(location, 0) + 1
    return jsonify({"days": days, "total": len(rows), "daily": [{"date": date, "scans": count} for date, count in sorted(daily.items())], "devices": devices, "referrers": referrers, "locations": locations})


@app.post("/api/plaques")
@require_role("sales")
def create_plaques():
    payload = request.get_json(silent=True) or {}
    try:
        quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "La quantité doit être un nombre."}), 400
    category = payload.get("type", "Google")
    if quantity < 1 or quantity > 500 or category not in CATEGORIES:
        return jsonify({"error": "Quantité ou catégorie invalide."}), 400
    backup_db("before-create")
    now = datetime.now().strftime("%d/%m/%Y")
    created_at = datetime.now().isoformat(timespec="seconds")
    created = []
    with db() as connection:
        for _ in range(quantity):
            code = new_code()
            connection.execute("INSERT INTO plaques (code,type,sale,date,status,phone,lifecycle,created_at) VALUES (?,?,?,?,?,?,?,?)", (code, category, "En stock", now, "free", "", "stock", created_at))
            created.append(code)
    return jsonify({"created": created, "count": len(created)}), 201


@app.patch("/api/plaques/<code>")
@require_role("sales")
def update_plaque(code):
    payload = request.get_json(silent=True) or {}
    allowed = {"destination", "type", "sale", "client", "phone", "note", "lifecycle"}
    changes = {key: payload[key] for key in allowed if key in payload}
    if not changes:
        return jsonify({"error": "Aucune modification."}), 400
    with db() as connection:
        existing = connection.execute("SELECT * FROM plaques WHERE code = ?", (code,)).fetchone()
        if not existing:
            return jsonify({"error": "Plaque introuvable."}), 404
        destination = changes.get("destination", existing["destination"])
        lifecycle = changes.get("lifecycle", existing["lifecycle"])
        if lifecycle not in LIFECYCLES:
            return jsonify({"error": "Etat de cycle invalide."}), 400
        if lifecycle == "stock" and destination:
            return jsonify({"error": "Une plaque avec une destination ne peut pas retourner en stock. Videz la destination d'abord."}), 409
        if existing["lifecycle"] in {"paid", "disabled", "archived"} and lifecycle == "stock":
            return jsonify({"error": "Cette plaque est protégée contre la réutilisation accidentelle."}), 409
        if not destination:
            changes["lifecycle"] = "stock"
            changes["sale"] = "En stock"
        changes["status"] = "free" if changes.get("lifecycle", lifecycle) == "stock" else "active" if changes.get("lifecycle", lifecycle) in {"assigned", "paid"} else "unpaid" if changes.get("lifecycle", lifecycle) == "delivered_unpaid" else "disabled"
        assignments = ", ".join(f"{key} = ?" for key in changes)
        backup_db("before-update")
        connection.execute(f"UPDATE plaques SET {assignments} WHERE code = ?", (*changes.values(), code))
        row = connection.execute("SELECT * FROM plaques WHERE code = ?", (code,)).fetchone()
    return jsonify(plaque_dict(row))


@app.get("/api/export")
@require_role("admin")
def export_data():
    with db() as connection:
        payload = {"version": 1, "exported_at": datetime.now().isoformat(timespec="seconds"), "plaques": [dict(row) for row in connection.execute("SELECT * FROM plaques")], "orders": [dict(row) for row in connection.execute("SELECT * FROM orders")], "scans": [dict(row) for row in connection.execute("SELECT * FROM scans")]} 
    response = app.response_class(json.dumps(payload, ensure_ascii=False, indent=2), mimetype="application/json")
    response.headers["Content-Disposition"] = "attachment; filename=onpoint-export.json"
    return response


@app.post("/api/import")
@require_role("admin")
def import_data():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("plaques"), list) or not isinstance(payload.get("orders"), list) or not isinstance(payload.get("scans"), list):
        return jsonify({"error": "Export JSON invalide."}), 400
    backup_db("before-import")
    with db() as connection:
        connection.execute("DELETE FROM scans")
        connection.execute("DELETE FROM orders")
        connection.execute("DELETE FROM plaques")
        for row in payload["plaques"]:
            connection.execute("INSERT INTO plaques (code,type,sale,date,client,destination,scans,status,phone,note,created_at,lifecycle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (row["code"], row["type"], row.get("sale", "En stock"), row.get("date", ""), row.get("client", ""), row.get("destination", ""), row.get("scans", 0), row.get("status", "free"), row.get("phone", ""), row.get("note", ""), row.get("created_at", datetime.now().isoformat(timespec="seconds")), row.get("lifecycle", "stock")))
        for row in payload["orders"]:
            connection.execute("INSERT INTO orders (code,name,price,status,date,phone) VALUES (?,?,?,?,?,?)", tuple(row[key] for key in ("code", "name", "price", "status", "date", "phone")))
        for row in payload["scans"]:
            connection.execute("INSERT INTO scans (id,code,scanned_at,user_agent,referrer,country,city) VALUES (?,?,?,?,?,?,?)", (row.get("id"), row["code"], row["scanned_at"], row.get("user_agent", ""), row.get("referrer", ""), row.get("country", ""), row.get("city", "")))
    return jsonify({"ok": True})


def qr_object(code):
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=12, border=4)
    qr.add_data(request.url_root.rstrip("/") + "/qrcode/" + code)
    qr.make(fit=True)
    return qr


def qr_png(code):
    image = qr_object(code).make_image(fill_color="#111113", back_color="white").convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def pdf_escape(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")[:100]


def qr_pdf(code, destination):
    image = Image.open(io.BytesIO(qr_png(code))).convert("RGB")
    raw = zlib.compress(image.tobytes())
    stream = f"q 0 0 283 425 re W n 1 1 1 rg 0 0 283 425 re f 0 0 0 rg BT /F1 22 Tf 48 382 Td (OnPoint.) Tj ET 0.84 0.05 0.18 rg 48 330 11 11 re f 0 0 0 rg BT /F1 16 Tf 48 42 Td ({pdf_escape(code)}) Tj ET BT /F1 8 Tf 48 25 Td ({pdf_escape(destination)}) Tj ET q 72 80 140 220 cm /Im1 Do Q".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 283 425] /Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >> /Contents 4 0 R >>", b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream), b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream" % (image.width, image.height, len(raw), raw)]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output)); output.extend(f"{index} 0 obj\n".encode()); output.extend(obj); output.extend(b"\nendobj\n")
    xref = len(output); output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()); output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])); output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(output)


def qr_svg(code, destination):
    matrix = qr_object(code).get_matrix()
    size = len(matrix); margin = 36; qr_size = 528; cell = qr_size / size
    paths = [f'<rect width="600" height="760" fill="white"/><text x="36" y="52" font-family="Arial" font-size="30" font-weight="700">OnPoint<tspan fill="#d8274f">.</tspan></text>', '<circle cx="545" cy="38" r="10" fill="#d8274f"/>']
    for y, row in enumerate(matrix):
        for x, enabled in enumerate(row):
            if enabled: paths.append(f'<rect x="{margin + x * cell:.2f}" y="{100 + y * cell:.2f}" width="{cell + .2:.2f}" height="{cell + .2:.2f}" fill="#111113"/>')
    paths.append(f'<text x="36" y="680" font-family="Arial" font-size="25" font-weight="700">{code}</text>')
    paths.append(f'<text x="36" y="712" font-family="Arial" font-size="13" fill="#555">{pdf_escape(destination)}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="760" viewBox="0 0 600 760">{"".join(paths)}</svg>'.encode()


@app.get("/api/plaques/<code>/qr")
@require_role()
def qr_image(code):
    if not code_exists(code): return jsonify({"error": "Plaque introuvable."}), 404
    return send_file(io.BytesIO(qr_png(code)), mimetype="image/png", download_name=f"{code}.png")


@app.get("/api/plaques/<code>/qr.svg")
@require_role()
def qr_svg_file(code):
    with db() as connection: row = connection.execute("SELECT destination FROM plaques WHERE code = ?", (code,)).fetchone()
    if not row: return jsonify({"error": "Plaque introuvable."}), 404
    return send_file(io.BytesIO(qr_svg(code, row["destination"])), mimetype="image/svg+xml", as_attachment=True, download_name=f"{code}.svg")


@app.get("/api/plaques/<code>/qr.pdf")
@require_role()
def qr_pdf_file(code):
    with db() as connection: row = connection.execute("SELECT destination FROM plaques WHERE code = ?", (code,)).fetchone()
    if not row: return jsonify({"error": "Plaque introuvable."}), 404
    return send_file(io.BytesIO(qr_pdf(code, row["destination"])), mimetype="application/pdf", as_attachment=True, download_name=f"{code}.pdf")


@app.get("/qrcode/<code>")
def scan(code):
    with db() as connection:
        row = connection.execute("SELECT destination,lifecycle FROM plaques WHERE code = ?", (code,)).fetchone()
        if not row: return "Plaque introuvable", 404
        if row["lifecycle"] in {"disabled", "archived"}: return "Cette plaque est desactivee", 410
        country = request.headers.get("X-Country", "")
        city = request.headers.get("X-City", "")
        connection.execute("UPDATE plaques SET scans = scans + 1 WHERE code = ?", (code,))
        connection.execute("INSERT INTO scans (code,scanned_at,user_agent,referrer,country,city) VALUES (?,?,?,?,?,?)", (code, datetime.now().isoformat(timespec="seconds"), request.user_agent.string, request.referrer or "", country, city))
    destination = row["destination"]
    return redirect(destination) if destination.startswith(("http://", "https://")) else f"Destination non configuree pour {code}", 200


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(ROOT, path)


init_db()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "4173")), debug=False)
