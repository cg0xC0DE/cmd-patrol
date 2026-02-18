from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from process_manager import ProcessManager
from domain_manager import load_domains, save_domains, apply_active_domain
import mq_store
import os

app = Flask(__name__, static_folder="../frontend", static_url_path="")
CORS(app)

manager = ProcessManager()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/services", methods=["GET"])
def list_services():
    return jsonify([p.to_dict() for p in manager.list_all()])


@app.route("/api/services", methods=["POST"])
def register_service():
    data = request.json
    script_path = data.get("script_path")
    name = data.get("name")
    
    if not script_path or not os.path.exists(script_path):
        return jsonify({"error": "Invalid script path"}), 400
    
    proc = manager.register(script_path, name)
    return jsonify(proc.to_dict())


@app.route("/api/services/<id>", methods=["DELETE"])
def unregister_service(id):
    if manager.unregister(id):
        return jsonify({"success": True})
    return jsonify({"error": "Service not found"}), 404


@app.route("/api/services/<id>/start", methods=["POST"])
def start_service(id):
    if manager.start(id):
        return jsonify(manager.get(id).to_dict())
    return jsonify({"error": "Failed to start"}), 400


@app.route("/api/services/<id>/stop", methods=["POST"])
def stop_service(id):
    if manager.stop(id):
        return jsonify(manager.get(id).to_dict())
    return jsonify({"error": "Failed to stop"}), 400


@app.route("/api/services/<id>/restart", methods=["POST"])
def restart_service(id):
    if manager.restart(id):
        return jsonify(manager.get(id).to_dict())
    return jsonify({"error": "Failed to restart"}), 400


@app.route("/api/services/<id>/logs", methods=["GET"])
def get_logs(id):
    offset = request.args.get("offset", 0, type=int)
    logs = manager.get_logs(id)
    new_lines = logs[offset:]
    return jsonify({"lines": new_lines, "offset": offset + len(new_lines), "total": len(logs)})


@app.route("/api/services/<id>/port", methods=["PUT"])
def set_port(id):
    proc = manager.get(id)
    if not proc:
        return jsonify({"error": "Service not found"}), 404
    proc.port = request.json.get("port", "")
    manager._save()
    return jsonify(proc.to_dict())


@app.route("/api/services/<id>/pin", methods=["PUT"])
def set_pin(id):
    proc = manager.get(id)
    if not proc:
        return jsonify({"error": "Service not found"}), 404
    proc.pinned = bool(request.json.get("pinned", False))
    manager._save()
    return jsonify(proc.to_dict())


@app.route("/api/services/<id>/config-path", methods=["PUT"])
def set_config_path(id):
    proc = manager.get(id)
    if not proc:
        return jsonify({"error": "Service not found"}), 404
    proc.config_file = request.json.get("config_file", "")
    manager._save()
    return jsonify(proc.to_dict())


@app.route("/api/services/<id>/config", methods=["GET"])
def read_config(id):
    proc = manager.get(id)
    if not proc:
        return jsonify({"error": "Service not found"}), 404
    if not proc.config_file or not os.path.isfile(proc.config_file):
        return jsonify({"error": "No config file set", "config_file": proc.config_file}), 404
    try:
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                content = open(proc.config_file, encoding=enc).read()
                return jsonify({"config_file": proc.config_file, "content": content})
            except UnicodeDecodeError:
                continue
        return jsonify({"error": "Cannot decode file"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/services/<id>/config", methods=["PUT"])
def write_config(id):
    proc = manager.get(id)
    if not proc:
        return jsonify({"error": "Service not found"}), 404
    if not proc.config_file:
        return jsonify({"error": "No config file set"}), 400
    content = request.json.get("content", "")
    try:
        with open(proc.config_file, "w", encoding="utf-8") as f:
            f.write(content)
        return jsonify({"success": True, "config_file": proc.config_file})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/services/<id>/open-folder", methods=["POST"])
def open_folder(id):
    proc = manager.get(id)
    if proc:
        os.startfile(proc.cwd)
        return jsonify({"success": True})
    return jsonify({"error": "Service not found"}), 404


@app.route("/api/browse", methods=["GET"])
def browse_dir():
    path = request.args.get("path", "")
    if not path:
        import string
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        return jsonify({"current": "", "items": [{"name": d, "path": d, "type": "dir"} for d in drives]})
    
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return jsonify({"error": "Path not found"}), 404
    
    if os.path.isfile(path):
        return jsonify({"current": path, "items": [], "is_file": True})
    
    items = []
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            ext = os.path.splitext(name)[1].lower()
            if is_dir or ext in (".cmd", ".bat", ".ps1", ".sh"):
                items.append({"name": name, "path": full, "type": "dir" if is_dir else "file"})
    except PermissionError:
        return jsonify({"error": "Access denied"}), 403
    
    parent = os.path.dirname(path)
    return jsonify({"current": path, "parent": parent if parent != path else "", "items": items})


@app.route("/api/domains", methods=["GET"])
def get_domains():
    return jsonify(load_domains())


@app.route("/api/domains", methods=["PUT"])
def put_domains():
    data = request.json or {}
    cfg = load_domains()

    if "active" in data:
        cfg["active"] = str(data.get("active") or "")
    if "candidates" in data and isinstance(data.get("candidates"), list):
        cfg["candidates"] = [str(x) for x in data.get("candidates")]
    if "targets" in data and isinstance(data.get("targets"), dict):
        cfg["targets"] = data.get("targets")

    save_domains(cfg)
    return jsonify(cfg)


@app.route("/api/domains/apply", methods=["POST"])
def apply_domains():
    cfg = load_domains()
    active = str((cfg.get("active") or "")).strip()
    results = apply_active_domain(active)
    return jsonify({"active": active, "results": results})


# ── MQ endpoints ──────────────────────────────────────────────

@app.route("/api/mq/publish", methods=["POST"])
def mq_publish():
    data = request.json or {}
    source = data.get("source", "")
    etype = data.get("type", "")
    title = data.get("title", "")
    if not source or not title:
        return jsonify({"error": "source and title required"}), 400
    msg = mq_store.publish(
        source=source,
        type=etype,
        title=title,
        detail=data.get("detail", ""),
        meta=data.get("meta"),
    )
    return jsonify(msg), 201


@app.route("/api/mq/messages", methods=["GET"])
def mq_list():
    status = request.args.get("status")
    source = request.args.get("source")
    limit = request.args.get("limit", 200, type=int)
    offset = request.args.get("offset", 0, type=int)
    return jsonify(mq_store.query(status=status, source=source, limit=limit, offset=offset))


@app.route("/api/mq/messages/<msg_id>", methods=["GET"])
def mq_get(msg_id):
    msg = mq_store.get(msg_id)
    if not msg:
        return jsonify({"error": "Not found"}), 404
    return jsonify(msg)


@app.route("/api/mq/messages/<msg_id>/ack", methods=["POST"])
def mq_ack(msg_id):
    msg = mq_store.ack(msg_id)
    if not msg:
        return jsonify({"error": "Not found"}), 404
    return jsonify(msg)


@app.route("/api/mq/messages/<msg_id>/done", methods=["POST"])
def mq_done(msg_id):
    msg = mq_store.done(msg_id)
    if not msg:
        return jsonify({"error": "Not found"}), 404
    return jsonify(msg)


@app.route("/api/mq/batch-done", methods=["POST"])
def mq_batch_done():
    before_id = (request.json or {}).get("before_id", "")
    if not before_id:
        return jsonify({"error": "before_id required"}), 400
    count = mq_store.batch_done(before_id)
    return jsonify({"count": count})


@app.route("/api/mq/batch-ack", methods=["POST"])
def mq_batch_ack():
    count = mq_store.batch_ack_new()
    return jsonify({"count": count})


@app.route("/api/mq/stats", methods=["GET"])
def mq_stats():
    return jsonify(mq_store.stats())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=51314, debug=False, threaded=True)
