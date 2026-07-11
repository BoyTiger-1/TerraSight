import os
import sys
# make sure the project root is importable no matter where we launch from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, abort, render_template, send_from_directory
from flask_cors import CORS

from src.models.user import db
from src.modules import MODULES, module_meta
from src.routes.api import api_bp
from src.routes.user import user_bp
from src.routes.wildfire import wildfire_bp

app = Flask(
    __name__,
    static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")

CORS(app)

app.register_blueprint(api_bp, url_prefix="/api")
app.register_blueprint(user_bp, url_prefix="/api")
app.register_blueprint(wildfire_bp, url_prefix="/api/wildfire")  # legacy contract

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"sqlite:///{os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'app.db')}")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)
with app.app_context():
    db.create_all()


@app.route("/")
def home():
    return render_template("index.html", modules=module_meta())


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", modules=module_meta())


@app.route("/module/<slug>")
def module_page(slug):
    if slug not in MODULES:
        abort(404)
    meta = {k: v for k, v in MODULES[slug].items() if k != "impl"}
    return render_template("module.html", slug=slug, meta=meta, modules=module_meta())


@app.route("/simulator")
def simulator():
    return render_template("simulator.html", modules=module_meta())


@app.route("/reports")
def reports_page():
    return render_template("reports.html", modules=module_meta())


@app.route("/about")
def about():
    return render_template("about.html", modules=module_meta())


@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(app.static_folder, path)


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", modules=module_meta()), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
