from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from database import init_db, get_db
from werkzeug.security import check_password_hash
from geopy.geocoders import Nominatim
import json
import secrets
import os
import cloudinary
import cloudinary.uploader
from pywebpush import webpush, WebPushException

app = Flask(__name__)


cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

app.secret_key = secrets.token_hex(32)

app.config["VAPID_PUBLIC_KEY"] = os.getenv("VAPID_PUBLIC_KEY", "")


init_db()

def send_push_notification(role, tracking_id=None, title="New message", body="You have a new message.", url="/"):
    conn = get_db()

    if role == "customer":
        subscriptions = conn.execute(
            """
            SELECT *
            FROM push_subscriptions
            WHERE role = ? AND tracking_id = ?
            """,
            ("customer", tracking_id)
        ).fetchall()
    else:
        subscriptions = conn.execute(
            """
            SELECT *
            FROM push_subscriptions
            WHERE role = ?
            """,
            ("admin",)
        ).fetchall()

    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_claims_email = os.getenv("VAPID_CLAIMS_EMAIL", "")

    if not vapid_private_key or not vapid_claims_email:
        conn.close()
        return

    for subscription in subscriptions:
        push_subscription = {
            "endpoint": subscription["endpoint"],
            "keys": {
                "p256dh": subscription["p256dh"],
                "auth": subscription["auth"]
            }
        }

        try:
            webpush(
                subscription_info=push_subscription,
                data=json.dumps({
                    "title": title,
                    "body": body,
                    "url": url
                }),
                vapid_private_key=vapid_private_key,
                vapid_claims={
                    "sub": vapid_claims_email
                }
            )
        except Exception as error:
            print(f"Push notification failed: {error}")

            status_code = getattr(
                getattr(error, "response", None),
                "status_code",
                None
            )

            if status_code in {404, 410}:
                conn.execute(
                    "DELETE FROM push_subscriptions WHERE id = ?",
                    (subscription["id"],)
                )

    conn.commit()
    conn.close()

def make_tracking_id():
    return "FDX" + secrets.token_hex(5).upper()

ADMIN_USERNAME = "FedEx"
ADMIN_PASSWORD_HASH = "scrypt:32768:8:1$AhFdcTaNuVvCESI1$fa0d81e47575d38d1718dc3365bc81c895c3d30790058e000b8161bd29c170b753b08189b46703b69bc64fa7dbdf6c3849c698866f659c2f8cd20202c5bf7b6b"


def geocode_location(location):
    if not location:
        return None

    aliases = {
        "USA": "United States",
        "US": "United States",
        "U.S.A.": "United States",
        "UK": "United Kingdom",
        "U.K.": "United Kingdom",
        "Brasil": "Brazil"
    }

    location_clean = aliases.get(location.strip(), location.strip())

    try:
        geolocator = Nominatim(
            user_agent="primevault-delivery-tracking-simulator"
        )

        result = geolocator.geocode(
            location_clean,
            timeout=10,
            addressdetails=True
        )

        if result:
            bbox = None

            if getattr(result, "raw", None):
                raw_bbox = result.raw.get("boundingbox")

                if raw_bbox and len(raw_bbox) == 4:
                    bbox = (
                        float(raw_bbox[2]),
                        float(raw_bbox[0]),
                        float(raw_bbox[3]),
                        float(raw_bbox[1])
                    )

            return {
                "latitude": result.latitude,
                "longitude": result.longitude,
                "display_name": result.address,
                "bbox": bbox
            }

    except Exception:
        pass

    return None


def logged_in():
    return session.get("admin_logged_in") is True


@app.route("/")
def home():
    if logged_in():
        return redirect(url_for("admin"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username == ADMIN_USERNAME and check_password_hash(
            ADMIN_PASSWORD_HASH, password
        ):
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))

        error = "Invalid username or password."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not logged_in():
        return redirect(url_for("login"))

    if request.method == "POST":
        tracking_id = make_tracking_id()

        conn = get_db()

        print("ADMIN STEP 1: INSERT SHIPMENT")
        conn.execute("""
            INSERT INTO shipments
            (tracking_id, sender_name, sender_address, sender_phone, sender_country,
             receiver_name, receiver_address, receiver_phone, receiver_country,
             origin, destination, current_location, status,
             estimated_delivery, send_datetime, delivery_datetime,
             package_description, package_weight, package_count,
             receipt_number, receipt_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            tracking_id,
            request.form["sender_name"],
            request.form.get("sender_address", ""),
            request.form.get("sender_phone", ""),
            request.form.get("sender_country", ""),
            request.form["receiver_name"],
            request.form.get("receiver_address", ""),
            request.form.get("receiver_phone", ""),
            request.form.get("receiver_country", ""),
            request.form["origin"],
            request.form["destination"],
            request.form["current_location"],
            request.form["status"],
            request.form["estimated_delivery"],
            request.form.get("send_datetime", ""),
            request.form.get("delivery_datetime", ""),
            request.form.get("package_description", ""),
            request.form.get("package_weight", ""),
            request.form.get("package_count", "1"),
            "RCPT-" + secrets.token_hex(5).upper()
        ))

        conn.commit()
        print("ADMIN STEP 1 OK")

        print("ADMIN STEP 2: GET SHIPMENT ID")
        shipment_id = conn.execute(
            "SELECT id FROM shipments WHERE tracking_id = ?",
            (tracking_id,)
        ).fetchone()["id"]
        print("ADMIN STEP 2 OK:", shipment_id)

        print("ADMIN STEP 3: INSERT TRACKING EVENT")
        conn.execute("""
            INSERT INTO tracking_events
            (shipment_id, location, status, description)
            VALUES (?, ?, ?, ?)
        """, (
            shipment_id,
            request.form["current_location"],
            request.form["status"],
            "Shipment created in the tracking simulator."
        ))

        conn.commit()
        print("ADMIN STEP 3 OK")
        conn.close()

        print("ADMIN CREATE COMPLETE:", tracking_id)
        return redirect(url_for("receipt", tracking_id=tracking_id))

    conn = get_db()

    shipments = conn.execute(
        "SELECT * FROM shipments ORDER BY id DESC"
    ).fetchall()

    messages_by_shipment = {}

    for shipment in shipments:
        messages_by_shipment[shipment["id"]] = conn.execute(
            """
            SELECT *
            FROM shipment_messages
            WHERE shipment_id = ?
            ORDER BY id ASC
            """,
            (shipment["id"],)
        ).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        shipments=shipments,
        messages_by_shipment=messages_by_shipment
    )


@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json(silent=True) or {}

    role = data.get("role", "").strip()
    tracking_id = data.get("tracking_id")
    subscription = data.get("subscription") or {}

    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if role not in {"customer", "admin"}:
        return jsonify({"success": False, "error": "Invalid role"}), 400

    if role == "customer" and not tracking_id:
        return jsonify({"success": False, "error": "Tracking ID required"}), 400

    if role == "admin" and not logged_in():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if not endpoint or not p256dh or not auth:
        return jsonify({"success": False, "error": "Invalid subscription"}), 400

    conn = get_db()

    try:
        conn.execute(
            """
            INSERT INTO push_subscriptions
            (role, tracking_id, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (endpoint)
            DO UPDATE SET
                role = EXCLUDED.role,
                tracking_id = EXCLUDED.tracking_id,
                p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth
            """,
            (role, tracking_id, endpoint, p256dh, auth)
        )

        conn.commit()

    except Exception:
        conn.close()
        raise

    conn.close()

    return jsonify({"success": True})


@app.route("/admin/message/<int:shipment_id>", methods=["POST"])
def send_message(shipment_id):
    if not logged_in():
        return redirect(url_for("login"))

    message = request.form.get("message", "").strip()
    file = request.files.get("photo")

    if not message and (not file or not file.filename):
        return redirect(url_for("admin"))

    conn = get_db()

    shipment = conn.execute(
        "SELECT id, tracking_id FROM shipments WHERE id = ?",
        (shipment_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return "<h1>Shipment not found</h1>", 404

    image_url = None

    try:
        if file and file.filename:
            extension = file.filename.rsplit(".", 1)[-1].lower()
            allowed = {"jpg", "jpeg", "png", "webp", "gif"}

            if extension in allowed:
                result = cloudinary.uploader.upload(
                    file,
                    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
                    api_key=os.getenv("CLOUDINARY_API_KEY"),
                    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
                    folder=f"primevault_delivery/{shipment['tracking_id']}/messages",
                    resource_type="image"
                )
                image_url = result.get("secure_url")

        if not message and not image_url:
            conn.close()
            return redirect(url_for("admin"))

        conn.execute(
            """
            INSERT INTO shipment_messages
            (shipment_id, sender, message, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (shipment_id, "admin", message, image_url)
        )

        conn.commit()

    except Exception:
        conn.close()
        raise

    conn.close()

    send_push_notification(
        role="customer",
        tracking_id=shipment["tracking_id"],
        title="New message",
        body=message if message else "You received a new shipment message.",
        url=url_for("shipment", tracking_id=shipment["tracking_id"]) + "#messages"
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "message": message,
            "image_url": image_url
        })

    return redirect(url_for("admin"))


@app.route("/admin/photos/<int:shipment_id>", methods=["POST"])
def upload_photos(shipment_id):
    if not logged_in():
        return redirect(url_for("login"))

    conn = get_db()
    shipment = conn.execute(
        "SELECT * FROM shipments WHERE id = ?",
        (shipment_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return "Shipment not found", 404

    files = request.files.getlist("photos")
    allowed = {"jpg", "jpeg", "png", "webp", "gif"}
    uploaded = 0

    try:
        for file in files:
            if not file or not file.filename:
                continue

            extension = file.filename.rsplit(".", 1)[-1].lower()
            if extension not in allowed:
                continue

            result = cloudinary.uploader.upload(
                file,
                cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
                api_key=os.getenv("CLOUDINARY_API_KEY"),
                api_secret=os.getenv("CLOUDINARY_API_SECRET"),
                folder=f"primevault_delivery/{shipment['tracking_id']}",
                resource_type="image"
            )

            public_id = result.get("public_id")
            if public_id:
                conn.execute(
                    "INSERT INTO shipment_photos (shipment_id, filename) VALUES (?, ?)",
                    (shipment_id, public_id)
                )
                uploaded += 1

        conn.commit()
    except Exception:
        conn.close()
        raise

    conn.close()
    return redirect(url_for("admin"))

@app.route("/admin/edit/<int:shipment_id>", methods=["GET", "POST"])
def edit_shipment(shipment_id):
    if not logged_in():
        return redirect(url_for("login"))

    conn = get_db()

    shipment = conn.execute(
        "SELECT * FROM shipments WHERE id = ?",
        (shipment_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return "Shipment not found", 404

    if request.method == "POST":
        conn.execute("""
            UPDATE shipments
            SET sender_name = ?,
                sender_address = ?,
                sender_phone = ?,
                sender_country = ?,
                receiver_name = ?,
                receiver_address = ?,
                receiver_phone = ?,
                receiver_country = ?,
                origin = ?,
                destination = ?,
                current_location = ?,
                status = ?,
                estimated_delivery = ?,
                send_datetime = ?,
                delivery_datetime = ?,
                package_description = ?,
                package_weight = ?,
                package_count = ?
            WHERE id = ?
        """, (
            request.form.get("sender_name", ""),
            request.form.get("sender_address", ""),
            request.form.get("sender_phone", ""),
            request.form.get("sender_country", ""),
            request.form.get("receiver_name", ""),
            request.form.get("receiver_address", ""),
            request.form.get("receiver_phone", ""),
            request.form.get("receiver_country", ""),
            request.form.get("origin", ""),
            request.form.get("destination", ""),
            request.form.get("current_location", ""),
            request.form.get("status", ""),
            request.form.get("estimated_delivery", ""),
            request.form.get("send_datetime", ""),
            request.form.get("delivery_datetime", ""),
            request.form.get("package_description", ""),
            request.form.get("package_weight", ""),
            request.form.get("package_count", "1"),
            shipment_id
        ))

        conn.execute("""
            INSERT INTO tracking_events
            (shipment_id, location, status, description)
            VALUES (?, ?, ?, ?)
        """, (
            shipment_id,
            request.form.get("current_location", ""),
            request.form.get("status", ""),
            "Shipment information updated by admin"
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("admin"))

    conn.close()

    return render_template("edit.html", shipment=shipment)


@app.route("/admin/event/<int:shipment_id>", methods=["GET", "POST"])
def add_event(shipment_id):
    if not logged_in():
        return redirect(url_for("login"))

    conn = get_db()

    shipment = conn.execute(
        "SELECT * FROM shipments WHERE id = ?",
        (shipment_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return "Shipment not found", 404

    if request.method == "POST":
        location = request.form["location"]
        status = request.form["status"]
        description = request.form["description"]

        conn.execute("""
            INSERT INTO tracking_events
            (shipment_id, location, status, description)
            VALUES (?, ?, ?, ?)
        """, (
            shipment_id,
            location,
            status,
            description
        ))

        conn.execute("""
            UPDATE shipments
            SET current_location = ?,
                status = ?
            WHERE id = ?
        """, (
            location,
            status,
            shipment_id
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("admin"))

    events = conn.execute("""
        SELECT *
        FROM tracking_events
        WHERE shipment_id = ?
        ORDER BY id DESC
    """, (shipment_id,)).fetchall()

    conn.close()

    return render_template(
        "event.html",
        shipment=shipment,
        events=events
    )


@app.route("/receipt/<tracking_id>")
def receipt(tracking_id):
    conn = get_db()
    shipment = conn.execute(
        "SELECT * FROM shipments WHERE tracking_id = ?",
        (tracking_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return "<h1>Shipment not found</h1>", 404

    if not shipment["receipt_number"]:
        receipt_number = "RCPT-" + secrets.token_hex(5).upper()
        conn.execute(
            "UPDATE shipments SET receipt_number = ?, receipt_date = CURRENT_TIMESTAMP WHERE id = ?",
            (receipt_number, shipment["id"])
        )
        conn.commit()
        shipment = conn.execute(
            "SELECT * FROM shipments WHERE id = ?",
            (shipment["id"],)
        ).fetchone()

    conn.close()

    return render_template(
        "receipt.html",
        shipment=shipment,
        print_mode=request.args.get("print") == "1"
    )


@app.route("/receipt/<tracking_id>/print-view")
def receipt_print_view(tracking_id):
    conn = get_db()
    shipment = conn.execute(
        "SELECT * FROM shipments WHERE tracking_id = ?",
        (tracking_id,)
    ).fetchone()
    conn.close()

    if not shipment:
        return "<h1>Shipment not found</h1>", 404

    return render_template(
        "print_receipt.html",
        shipment=shipment
    )

@app.route("/track", methods=["GET", "POST"])
def track_lookup():
    if request.method == "POST":
        tracking_id = request.form["tracking_id"].strip().upper()
        return redirect(
        url_for("shipment", tracking_id=tracking_id) + "#messages"
    )
    return render_template("track_lookup.html")

@app.route("/track/<tracking_id>/message", methods=["POST"])
def customer_message(tracking_id):
    message = request.form.get("message", "").strip()
    file = request.files.get("photo")

    if not message and (not file or not file.filename):
        return redirect(url_for("shipment", tracking_id=tracking_id))

    conn = get_db()

    shipment = conn.execute(
        "SELECT id FROM shipments WHERE tracking_id = ?",
        (tracking_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return "<h1>Shipment not found</h1>", 404

    image_url = None

    try:
        if file and file.filename:
            extension = file.filename.rsplit(".", 1)[-1].lower()
            allowed = {"jpg", "jpeg", "png", "webp", "gif"}

            if extension in allowed:
                result = cloudinary.uploader.upload(
                    file,
                    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
                    api_key=os.getenv("CLOUDINARY_API_KEY"),
                    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
                    folder=f"primevault_delivery/{tracking_id}/messages",
                    resource_type="image"
                )
                image_url = result.get("secure_url")

        conn.execute(
            """
            INSERT INTO shipment_messages
            (shipment_id, sender, message, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (shipment["id"], "customer", message, image_url)
        )

        conn.commit()

    except Exception:
        conn.close()
        raise

    conn.close()

    send_push_notification(
        role="admin",
        title="New customer message",
        body=message if message else "A customer sent a new shipment message.",
        url=url_for("admin") + "#messages"
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "message": message,
            "image_url": image_url
        })

    return redirect(url_for("shipment", tracking_id=tracking_id) + "#messages")


@app.route("/admin/messages/<int:shipment_id>")
def admin_messages(shipment_id):
    if not logged_in():
        return jsonify({"success": False}), 401

    after_id = request.args.get("after_id", 0, type=int)

    conn = get_db()

    messages = conn.execute(
        """
        SELECT id, sender, message, image_url, created_at
        FROM shipment_messages
        WHERE shipment_id = ? AND id > ?
        ORDER BY id ASC
        """,
        (shipment_id, after_id)
    ).fetchall()

    conn.close()

    return jsonify({
        "success": True,
        "messages": [
            {
                "id": row["id"],
                "sender": row["sender"],
                "message": row["message"],
                "image_url": row["image_url"],
                "created_at": str(row["created_at"])
            }
            for row in messages
        ]
    })


@app.route("/track/<tracking_id>/messages")
def shipment_messages(tracking_id):
    after_id = request.args.get("after_id", 0, type=int)

    conn = get_db()

    shipment = conn.execute(
        "SELECT id FROM shipments WHERE tracking_id = ?",
        (tracking_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return jsonify({"success": False}), 404

    messages = conn.execute(
        """
        SELECT id, sender, message, image_url, created_at
        FROM shipment_messages
        WHERE shipment_id = ? AND id > ?
        ORDER BY id ASC
        """,
        (shipment["id"], after_id)
    ).fetchall()

    conn.close()

    return jsonify({
        "success": True,
        "messages": [
            {
                "id": row["id"],
                "sender": row["sender"],
                "message": row["message"],
                "image_url": row["image_url"],
                "created_at": str(row["created_at"])
            }
            for row in messages
        ]
    })


@app.route("/track/<tracking_id>")
def shipment(tracking_id):
    conn = get_db()

    shipment = conn.execute(
        "SELECT * FROM shipments WHERE tracking_id = ?",
        (tracking_id,)
    ).fetchone()

    if not shipment:
        conn.close()
        return "<h1>Shipment not found</h1>", 404

    events = conn.execute("""
        SELECT *
        FROM tracking_events
        WHERE shipment_id = ?
        ORDER BY id DESC
    """, (shipment["id"],)).fetchall()

    photos = conn.execute("""
        SELECT *
        FROM shipment_photos
        WHERE shipment_id = ?
        ORDER BY id DESC
    """, (shipment["id"],)).fetchall()

    messages = conn.execute("""
        SELECT *
        FROM shipment_messages
        WHERE shipment_id = ?
        ORDER BY id ASC
    """, (shipment["id"],)).fetchall()

    conn.close()

    current_location_data = geocode_location(
        shipment["current_location"]
    )

    map_bbox = None

    if current_location_data:
        bbox = current_location_data.get("bbox")

        if bbox:
            west, south, east, north = bbox
            width = abs(east - west)
            height = abs(north - south)

            # Use the real country boundary when it is sensible.
            # If territories make it enormous, create a focused
            # view around the country's main geographic center.
            # Keep the map close to the selected location.
            # This works automatically for every country/city.
            lat = current_location_data["latitude"]
            lon = current_location_data["longitude"]

            # Automatic country-level map view.
            # Keep a useful country-sized view without
            # manually maintaining a country list.
            if bbox:
                bw = abs(east - west)
                bh = abs(north - south)

                if bw <= 80 and bh <= 60:
                    map_bbox = bbox
                else:
                    # Large/outlying territory bounding boxes:
                    # use a wider mainland-focused view around
                    # the geocoded country center.
                    half_w = min(max(bw * 0.12, 6), 18)
                    half_h = min(max(bh * 0.12, 5), 14)

                    map_bbox = (
                        lon - half_w,
                        lat - half_h,
                        lon + half_w,
                        lat + half_h
                    )
            else:
                map_bbox = (
                    lon - 6,
                    lat - 5,
                    lon + 6,
                    lat + 5
                )

    return render_template(
        "tracking.html",
        shipment=shipment,
        events=events,
        photos=photos,
        messages=messages,
        current_location_data=current_location_data,
        map_bbox=map_bbox
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
