from flask import Flask, render_template, request, redirect, url_for, session
from database import init_db, get_db
from werkzeug.security import check_password_hash
from geopy.geocoders import Nominatim
import secrets
import os
import cloudinary
import cloudinary.uploader

app = Flask(__name__)

print("CLOUDINARY CONFIG CHECK:", {
    "cloud_name": bool(os.getenv("CLOUDINARY_CLOUD_NAME")),
    "api_key": bool(os.getenv("CLOUDINARY_API_KEY")),
    "api_secret": bool(os.getenv("CLOUDINARY_API_SECRET"))
})

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

app.secret_key = secrets.token_hex(32)

init_db()

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

    conn.close()

    return render_template("admin.html", shipments=shipments)


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
        return redirect(url_for("shipment", tracking_id=tracking_id))
    return render_template("track_lookup.html")

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
        current_location_data=current_location_data,
        map_bbox=map_bbox
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
