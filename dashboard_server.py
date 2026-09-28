# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Professional Web Dashboard & Real-Time EXP Tracker
Embedded Async Web Server (aiohttp)
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional
from aiohttp import web

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")

# Free Fire account-level cumulative EXP thresholds (levels 1-100).
LEVEL_EXP_THRESHOLDS = (
    0, 0, 48, 202, 544, 1012, 1844, 2792, 3800, 4870, 6004,
    7192, 8448, 9760, 11140, 12566, 14060, 15610, 17224, 18902,
    20632, 22424, 24278, 26192, 28166, 30200, 32294, 34448, 37804,
    41274, 44870, 48582, 53394, 58566, 64096, 69994, 76260, 83506,
    91128, 99322, 108092, 120144, 133266, 147472, 162760, 179126,
    196572, 215368, 235316, 257010, 279860, 304056, 348318, 394982,
    444044, 495508, 549364, 633756, 721744, 813336, 908522, 1041438,
    1180352, 1325266, 1476184, 1634300, 1840946, 2056594, 2281242,
    2514880, 2757530, 3059506, 3372284, 3699456, 4041030, 4397002,
    4829104, 5282204, 5756304, 6251404, 6767502, 7381324, 8043154,
    8752982, 9510808, 10316638, 11277190, 12291748, 13360304,
    14482858, 15659418, 17026708, 18453990, 19941280, 21488570,
    23095858, 24763138, 26490428, 28277708, 30124996, 32032284,
)


def level_progress(level: int, current_exp: int) -> Dict[str, Any]:
    safe_level = max(1, min(100, int(level or 1)))
    safe_exp = max(0, int(current_exp or 0))
    current_target = LEVEL_EXP_THRESHOLDS[safe_level]
    if safe_level >= 100:
        return {
            "power_level": safe_level,
            "level_exp_start": current_target,
            "next_level": None,
            "next_level_exp": None,
            "exp_to_next_level": 0,
            "level_progress_pct": 100,
        }

    next_target = LEVEL_EXP_THRESHOLDS[safe_level + 1]
    span = max(1, next_target - current_target)
    progress = max(0, min(span, safe_exp - current_target))
    return {
        "power_level": safe_level,
        "level_exp_start": current_target,
        "next_level": safe_level + 1,
        "next_level_exp": next_target,
        "exp_to_next_level": max(0, next_target - safe_exp),
        "level_progress_pct": round((progress / span) * 100, 1),
    }


def account_snapshot(account: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = dict(account)
    snapshot.update(level_progress(snapshot.get("level", 1), snapshot.get("current_exp", 0)))
    return snapshot


# Global bot state shared between Main.py and Web Dashboard
class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 2000
        self.log_sequence = 0
        self.total_matches = 0
        self.total_matches_started = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.worker_specs: Dict[str, Dict[str, Any]] = {}
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}
        self.account_aliases: Dict[str, str] = {}
        self.service_running = False
        self.game_mode = "lone_wolf"
        self.device_revision = 0
        self.match_started_at = {}

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        self.log_sequence += 1
        entry = {
            "id": self.log_sequence,
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": uid
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def bind_account_alias(self, alias: str, account_id: str):
        if alias and account_id:
            self.account_aliases[str(alias)] = str(account_id)

    def resolve_account_id(self, uid: str) -> str:
        value = str(uid or "").strip()
        return self.account_aliases.get(value, value)

    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int, likes: int = 0):
        uid_str = str(uid)
        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": level or 1,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "likes": likes or 0,
                "status": "ONLINE",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[uid_str]
            if nickname:
                acc["nickname"] = nickname
            if region:
                acc["region"] = region
            if level:
                acc["level"] = level
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["likes"] = likes
            acc["status"] = "ONLINE"
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old_exp = acc["current_exp"]
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                acc["level"] = level
            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            diff = current_exp - old_exp
            if diff > 0:
                self.log(f"Account {acc['nickname']} ({uid_str}) gained +{diff} EXP! Total Gained: +{acc['gained_exp']}", "success", uid_str)
            self.recalc_totals()

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = self.resolve_account_id(uid)
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid: str):
        uid_str = self.resolve_account_id(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.log(f"Account {self.accounts[uid_str]['nickname']} finished Match #{self.accounts[uid_str]['matches_played']}", "info", uid_str)

    def start_match(self, uid: str):
        """Record a match as soon as its UDP session is launched."""
        uid_str = self.resolve_account_id(uid)
        self.total_matches_started += 1
        self.match_started_at[uid_str] = time.time()
        if uid_str in self.accounts:
            self.accounts[uid_str]["last_match_started"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()


# ==================== HTTP HANDLERS ====================

TEMPLATE_PATH = os.path.join(BASE_DIR, "index.html")
DEVICES_FILE = os.path.join(BASE_DIR, "devices.json")

async def handle_index(request: web.Request) -> web.Response:
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<h1>templates/index.html not found!</h1>"
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_favicon(request: web.Request) -> web.Response:
    return web.Response(status=204)


async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = [account_snapshot(account) for account in bot_state.accounts.values()]
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    active_udp_matches = sum(
        int(account.get("active_matches", 0) or 0) for account in accounts_data
    )
    status_counts = {}
    for account in accounts_data:
        status = str(account.get("status", "UNKNOWN")).upper()
        status_counts[status] = status_counts.get(status, 0) + 1
    uptime = max(1, int(time.time() - bot_state.start_time))
    uptime_hours = max(1 / 60, uptime / 3600)
    exp_per_hour = round(bot_state.total_gained_exp / uptime_hours)
    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_matches_started": bot_state.total_matches_started,
        "total_active_matches": active_udp_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "exp_per_hour": exp_per_hour,
        "accounts": accounts_data,
        # Keep the fast stats poll small; the dedicated logs endpoint still
        # exposes the complete in-memory terminal buffer.
        "logs": bot_state.logs[-600:],
        "log_cursor": bot_state.log_sequence,
        "uptime": uptime,
        "service_running": bot_state.service_running,
        "game_mode": bot_state.game_mode,
        "active_udp_matches": active_udp_matches,
        "active_workers": sum(
            1 for worker in bot_state.account_workers.values()
            if worker and not worker.done()
        ),
        "status_counts": status_counts,
        "last_log": bot_state.logs[-1] if bot_state.logs else None,
    })


async def handle_get_logs(request: web.Request) -> web.Response:
    """Return the complete in-memory terminal buffer or only newer entries."""
    try:
        since = max(0, int(request.query.get("since", "0")))
    except (TypeError, ValueError):
        since = 0
    entries = [entry for entry in bot_state.logs if int(entry.get("id", 0)) > since]
    return web.json_response({
        "logs": entries,
        "cursor": bot_state.log_sequence,
        "has_more": False,
    })


async def handle_bot_start(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}
    mode = str(data.get("mode") or bot_state.game_mode).strip().lower()
    if mode not in {"lone_wolf", "bermuda"}:
        return web.json_response({"status": "error", "error": "Unsupported game mode"}, status=400)
    callback = bot_state.refresh_callbacks.get("on_bot_start")
    if not callback:
        return web.json_response({"status": "error", "error": "Bot controller is not ready"}, status=503)
    try:
        await callback(mode)
        return web.json_response({"status": "ok", "service_running": True, "game_mode": mode})
    except Exception as exc:
        bot_state.log(f"Bot start failed: {exc}", "error")
        return web.json_response({"status": "error", "error": str(exc)}, status=500)


async def handle_bot_stop(request: web.Request) -> web.Response:
    callback = bot_state.refresh_callbacks.get("on_bot_stop")
    if not callback:
        return web.json_response({"status": "error", "error": "Bot controller is not ready"}, status=503)
    try:
        await callback()
        return web.json_response({"status": "ok", "service_running": False})
    except Exception as exc:
        bot_state.log(f"Bot stop failed: {exc}", "error")
        return web.json_response({"status": "error", "error": str(exc)}, status=500)


async def handle_mode(request: web.Request) -> web.Response:
    if request.method == "GET":
        return web.json_response({"status": "ok", "game_mode": bot_state.game_mode})
    try:
        data = await request.json()
        mode = str(data.get("mode", "")).strip().lower()
        if mode not in {"lone_wolf", "bermuda"}:
            return web.json_response({"status": "error", "error": "Unsupported game mode"}, status=400)
        bot_state.game_mode = mode
        bot_state.log(f"Game mode set to {mode.replace('_', ' ').title()}.", "info")
        if bot_state.service_running and bot_state.refresh_callbacks.get("on_bot_start"):
            await bot_state.refresh_callbacks["on_bot_start"](mode)
        return web.json_response({"status": "ok", "game_mode": mode})
    except Exception as exc:
        return web.json_response({"status": "error", "error": str(exc)}, status=400)


def _read_devices() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(DEVICES_FILE):
        return {}
    try:
        with open(DEVICES_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        bot_state.log(f"Could not read device profiles: {exc}", "error")
        return {}


async def handle_get_devices(request: web.Request) -> web.Response:
    devices = _read_devices()
    profiles = []
    for account_id, profile in devices.items():
        safe_profile = {
            "account_id": str(account_id),
            "brand": profile.get("brand", "Unknown"),
            "model": profile.get("model", "Unknown"),
            "system_software": profile.get("system_software", "Unknown"),
            "memory": profile.get("memory", 0),
            "screen_width": profile.get("screen_width", 0),
            "screen_height": profile.get("screen_height", 0),
        }
        profiles.append(safe_profile)
    profiles.sort(key=lambda item: item["account_id"])
    return web.json_response({
        "status": "ok",
        "profiles": profiles,
        "count": len(profiles),
        "revision": bot_state.device_revision,
    })


async def handle_add_device(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        account_id = str(data.get("account_id", "")).strip()
        brand = str(data.get("brand", "")).strip()
        model = str(data.get("model", "")).strip()
        system_software = str(data.get("system_software", "Android OS 14 / API-34")).strip()
        if not account_id or not brand or not model:
            return web.json_response({"status": "error", "error": "Account ID, brand and model are required"}, status=400)
        devices = _read_devices()
        existing = devices.get(account_id, {})
        devices[account_id] = {
            "unique_device_id": existing.get("unique_device_id") or f"Google|dashboard-{account_id}",
            "brand": brand,
            "model": model,
            "gpu_renderer": str(data.get("gpu_renderer", existing.get("gpu_renderer", "Adreno (TM) 740"))),
            "system_software": system_software,
            "screen_width": int(data.get("screen_width", existing.get("screen_width", 1080))),
            "screen_height": int(data.get("screen_height", existing.get("screen_height", 2400))),
            "screen_dpi": str(data.get("screen_dpi", existing.get("screen_dpi", "360"))),
            "memory": int(data.get("memory", existing.get("memory", 4096))),
            "processor_details": str(data.get("processor_details", existing.get("processor_details", "ARM64 FP ASIMD AES VMH | 2400 | 8"))),
            "client_ip": existing.get("client_ip", "10.0.0.1"),
        }
        with open(DEVICES_FILE, "w", encoding="utf-8") as handle:
            json.dump(devices, handle, indent=4)
        bot_state.device_revision += 1
        bot_state.log(f"Device profile saved for account {account_id}: {brand} {model}", "success", account_id)
        return web.json_response({"status": "ok"})
    except Exception as exc:
        bot_state.log(f"Device profile save failed: {exc}", "error")
        return web.json_response({"status": "error", "error": str(exc)}, status=400)


async def handle_add_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        existing = []
        if os.path.exists(ACCOUNTS_FILE):
            try:
                with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        if "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return web.json_response({"status": "error", "error": "UID and Password are required"})
            existing = [acc for acc in existing if str(acc.get("uid")) != uid]
            existing.append({"uid": uid, "password": pwd})
        elif "token" in data:
            token = str(data["token"]).strip()
            if not token:
                return web.json_response({"status": "error", "error": "Token is required"})
            existing = [acc for acc in existing if acc.get("token") != token]
            existing.append({"token": token})
        else:
            return web.json_response({"status": "error", "error": "Invalid payload"})

        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)

        bot_state.log(f"New account added: {data.get('uid') or 'Token'}", "success")
        
        # Trigger dynamic worker launch
        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](data))

        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_delete_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing = [acc for acc in existing if str(acc.get("uid")) != uid]
            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)

        account_id = bot_state.resolve_account_id(uid)
        if account_id in bot_state.accounts:
            del bot_state.accounts[account_id]
            bot_state.recalc_totals()

        # The dashboard shows the in-game account ID, while guest workers
        # are keyed by their login UID. Resolve both so delete actually stops
        # the session instead of only removing its JSON entry.
        worker_keys = {uid}
        credentials = bot_state.account_credentials.get(uid) or bot_state.account_credentials.get(account_id)
        if credentials:
            if credentials.get("auth_uid"):
                worker_keys.add(str(credentials["auth_uid"]))
            if credentials.get("auth_token"):
                worker_keys.add(str(credentials["auth_token"])[:10])
                worker_keys.add(f"tok_{str(credentials['auth_token'])[:20]}")
        # Bot start can create several replicas for one saved account. Match
        # both the base owner key and replica keys so delete is complete.
        for worker_key, worker in list(bot_state.account_workers.items()):
            spec = bot_state.worker_specs.get(worker_key, {})
            owner_key = str(spec.get("owner_key", ""))
            belongs = (
                worker_key in worker_keys
                or owner_key in worker_keys
                or any(worker_key.startswith(f"{key}::") for key in worker_keys)
            )
            if not belongs:
                continue
            worker = bot_state.account_workers.pop(worker_key, None)
            if worker and not worker.done():
                worker.cancel()
            bot_state.worker_specs.pop(worker_key, None)

        bot_state.log(f"Account {uid} removed from rotation.", "warning", uid)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_refresh_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_account_start(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        callback = bot_state.refresh_callbacks.get("on_account_start")
        if not uid or not callback:
            return web.json_response({"status": "error", "error": "Account controller is not ready"}, status=503)
        await callback(uid)
        return web.json_response({"status": "ok"})
    except Exception as exc:
        return web.json_response({"status": "error", "error": str(exc)}, status=500)


async def handle_account_stop(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid", "")).strip()
        account_id = bot_state.resolve_account_id(uid)
        keys = {uid, account_id}
        credentials = bot_state.account_credentials.get(uid) or bot_state.account_credentials.get(account_id)
        if credentials:
            if credentials.get("auth_uid"):
                keys.add(str(credentials["auth_uid"]))
            if credentials.get("auth_token"):
                keys.add(str(credentials["auth_token"])[:10])
        stopped = False
        for key, worker in list(bot_state.account_workers.items()):
            spec = bot_state.worker_specs.get(key, {})
            owner_key = str(spec.get("owner_key", ""))
            belongs = (
                key in keys
                or owner_key in keys
                or any(key.startswith(f"{owner}::") for owner in keys)
            )
            if not belongs:
                continue
            worker = bot_state.account_workers.pop(key, None)
            bot_state.worker_specs.pop(key, None)
            if worker and not worker.done():
                worker.cancel()
                stopped = True
        if account_id in bot_state.accounts:
            bot_state.update_status(account_id, "OFFLINE", 0)
        bot_state.log(f"Account {uid} stopped by operator.", "warning", account_id)
        return web.json_response({"status": "ok", "stopped": stopped})
    except Exception as exc:
        return web.json_response({"status": "error", "error": str(exc)}, status=500)


async def handle_clear_logs(request: web.Request) -> web.Response:
    bot_state.logs.clear()
    bot_state.log("Terminal buffer cleared by operator.", "info")
    return web.json_response({"status": "ok"})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "lwudp-dashboard",
        "uptime": int(time.time() - bot_state.start_time),
    })


async def start_web_dashboard(host: str = "0.0.0.0", port: int = 5000):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/favicon.ico", handle_favicon)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_get("/api/logs", handle_get_logs)
    app.router.add_get("/api/mode", handle_mode)
    app.router.add_get("/api/devices", handle_get_devices)
    app.router.add_post("/api/mode", handle_mode)
    app.router.add_post("/api/bot/start", handle_bot_start)
    app.router.add_post("/api/bot/stop", handle_bot_stop)
    app.router.add_post("/api/device/add", handle_add_device)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/refresh", handle_refresh_account)
    app.router.add_post("/api/account/start", handle_account_start)
    app.router.add_post("/api/account/stop", handle_account_stop)
    app.router.add_post("/api/logs/clear", handle_clear_logs)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"\033[92m[+] Web Dashboard running on 0.0.0.0:{port}\033[0m")
