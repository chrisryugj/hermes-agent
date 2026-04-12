#!/usr/bin/env python3
"""
Hermes Agent Monitor — macOS Menu Bar App
Gateway / API Server / MCP / Cron status monitoring.
Click to open dashboard, restart gateway, view logs.

Usage:
  python3 hermes-monitor-mac.py
"""

import json
import os
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path

import rumps

HERMES_HOME = Path.home() / ".hermes"
API_PORT = 8642
API_KEY = ""
POLL_INTERVAL = 30
DASHBOARD_URL = f"http://127.0.0.1:{API_PORT}/dashboard"

# Read API key from .env
def load_api_key():
    global API_KEY, API_PORT
    env_file = HERMES_HOME / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("API_SERVER_KEY="):
                API_KEY = line.split("=", 1)[1].strip()
            if line.startswith("API_SERVER_PORT="):
                try:
                    API_PORT = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass

def api_get(path, timeout=5):
    """Call Hermes dashboard API."""
    url = f"http://127.0.0.1:{API_PORT}{path}"
    headers = {"User-Agent": "HermesMonitor/1.0"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def check_gateway():
    """Check gateway health."""
    data = api_get("/health")
    if data and data.get("status") == "ok":
        return True, "running"
    return False, "down"


def check_api_server():
    """Check API server models endpoint."""
    data = api_get("/v1/models")
    if data and "data" in data:
        return True, f"{len(data['data'])} model(s)"
    return False, "down"


def get_overview():
    """Get dashboard overview."""
    return api_get("/api/dashboard/overview")


class HermesMonitorApp(rumps.App):
    def __init__(self):
        super().__init__(
            "Hermes",
            icon=None,
            quit_button=None,
        )
        load_api_key()

        # Tunnel failure tracking — 연속 실패 2회 시 cloudflared 자체 재시작
        self._tunnel_fail_streak = 0
        self._tunnel_max_fail_streak = 2

        # Gateway PID tracking — PID 변경(재시작) 감지 시 tunnel도 kickstart
        # origin stale 방지. 최소 60초 쿨다운으로 thrash 방지
        self._last_gateway_pid = None
        self._last_tunnel_kick_ts = 0.0
        self._tunnel_kick_cooldown = 60.0

        # Menu items
        self.status_item = rumps.MenuItem("Status: checking...")
        self.model_item = rumps.MenuItem("Model: ...")
        self.gateway_item = rumps.MenuItem("Gateway: ...")
        self.api_item = rumps.MenuItem("API Server: ...")
        self.mcp_item = rumps.MenuItem("MCP: ...")
        self.cron_item = rumps.MenuItem("Cron: ...")
        self.tunnel_item = rumps.MenuItem("CF Tunnel: ...")
        self.sep1 = rumps.separator
        self.dashboard_btn = rumps.MenuItem("Dashboard Open", callback=self.open_dashboard)
        self.restart_btn = rumps.MenuItem("Gateway Restart", callback=self.restart_gateway)
        self.logs_btn = rumps.MenuItem("View Logs", callback=self.view_logs)
        self.sep2 = rumps.separator
        self.quit_btn = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self.menu = [
            self.status_item,
            self.model_item,
            self.gateway_item,
            self.api_item,
            self.mcp_item,
            self.cron_item,
            self.tunnel_item,
            self.sep1,
            self.dashboard_btn,
            self.restart_btn,
            self.logs_btn,
            self.sep2,
            self.quit_btn,
        ]

        # Initial check
        self.update_status(None)

    @rumps.timer(POLL_INTERVAL)
    def update_status(self, _):
        """Periodic status update."""
        gw_ok, gw_msg = check_gateway()
        api_ok, api_msg = check_api_server()

        if gw_ok and api_ok:
            self.title = "\u2705"  # Green check
            self.status_item.title = "Status: All OK"
        elif gw_ok:
            self.title = "\u26A0\uFE0F"  # Warning
            self.status_item.title = "Status: API issue"
        else:
            self.title = "\u274C"  # Red X
            self.status_item.title = "Status: Gateway down"

        self.gateway_item.title = f"Gateway: {gw_msg}"
        self.api_item.title = f"API Server: {api_msg}"

        # Get detailed overview
        overview = get_overview()
        if overview:
            self.model_item.title = f"Model: {overview.get('model', '?')}"
            mcp = overview.get("mcp_servers", {})
            self.mcp_item.title = f"MCP: {len(mcp)} server(s) ({', '.join(mcp.keys())})" if mcp else "MCP: none"
            crons = overview.get("cron_summary", [])
            active = sum(1 for c in crons if c.get("enabled"))
            errs = sum(1 for c in crons if c.get("last_status") and c.get("last_status") != "ok")
            self.cron_item.title = f"Cron: {active}/{len(crons)} active" + (f" ({errs} errors)" if errs else "")
        else:
            self.model_item.title = "Model: ?"
            self.mcp_item.title = "MCP: ?"
            self.cron_item.title = "Cron: ?"

        # CF Tunnel status — end-to-end health check + auto-resync
        self._check_tunnel()

    def _check_gateway_pid_change(self):
        """Gateway PID가 바뀌면 tunnel kickstart — origin stale 방지."""
        try:
            result = subprocess.run(
                ["launchctl", "list", "ai.hermes.gateway"],
                capture_output=True, timeout=3, text=True,
            )
            if result.returncode != 0:
                return
            # launchctl list 출력에서 PID 파싱
            current_pid = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith('"PID" ='):
                    try:
                        current_pid = int(line.split("=")[1].strip().rstrip(";"))
                    except (ValueError, IndexError):
                        pass
                    break
            if current_pid is None:
                return
            if self._last_gateway_pid is None:
                self._last_gateway_pid = current_pid
                return
            if current_pid != self._last_gateway_pid:
                now = time.time()
                prev = self._last_gateway_pid
                self._last_gateway_pid = current_pid
                if now - self._last_tunnel_kick_ts < self._tunnel_kick_cooldown:
                    return
                self._last_tunnel_kick_ts = now
                try:
                    uid = os.getuid()
                    subprocess.run(
                        ["launchctl", "kickstart", "-k", f"gui/{uid}/com.hermes.tunnel"],
                        capture_output=True, timeout=10,
                    )
                    rumps.notification(
                        "Hermes", "CF Tunnel",
                        f"Gateway restarted ({prev}→{current_pid}) — tunnel kickstarted",
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _check_tunnel(self):
        """End-to-end CF Tunnel health check with auto-resync."""
        # Gateway PID 변경 감지 (재시작 연동)
        self._check_gateway_pid_change()

        tunnel_url_file = HERMES_HOME / "logs" / "quick-tunnel-url.txt"
        sync_script = HERMES_HOME / "scripts" / "sync-cloudflare-worker.sh"
        cf_worker = "https://openclaw-bridge.ryuseungin.workers.dev"

        # 1) cloudflared 프로세스 확인
        cf_alive = False
        try:
            result = subprocess.run(
                ["pgrep", "-f", "cloudflared.*tunnel"],
                capture_output=True, timeout=3,
            )
            cf_alive = result.returncode == 0
        except Exception:
            pass

        if not cf_alive:
            self.tunnel_item.title = "CF Tunnel: ✗ cloudflared dead"
            # 터널 LaunchAgent 재시작 시도
            try:
                uid = os.getuid()
                subprocess.run(
                    ["launchctl", "kickstart", "-k", f"gui/{uid}/com.hermes.tunnel"],
                    capture_output=True, timeout=10,
                )
                rumps.notification("Hermes", "CF Tunnel", "cloudflared 재시작 요청")
            except Exception:
                pass
            return

        # 2) 터널 URL 존재 확인
        if not tunnel_url_file.exists():
            self.tunnel_item.title = "CF Tunnel: ⚠ no URL file"
            return
        tunnel_url = tunnel_url_file.read_text().strip()
        if not tunnel_url:
            self.tunnel_item.title = "CF Tunnel: ⚠ empty URL"
            return

        short_url = tunnel_url.replace("https://", "").split(".")[0][:20]

        # 3) CF Worker → Tunnel → Hermes end-to-end 확인
        e2e_ok = False
        try:
            req = urllib.request.Request(
                f"{cf_worker}/health",
                headers={
                    "User-Agent": "HermesMonitor/1.0",
                    "Authorization": "Bearer lexdiff-hermes-local",
                },
            )
            resp = urllib.request.urlopen(req, timeout=8)
            e2e_ok = resp.status == 200
        except Exception:
            pass

        if e2e_ok:
            if self._tunnel_fail_streak > 0:
                rumps.notification("Hermes", "CF Tunnel", f"Recovered (was streak={self._tunnel_fail_streak})")
            self._tunnel_fail_streak = 0
            self.tunnel_item.title = f"CF Tunnel: ✓ {short_url}…"
            return

        # e2e 실패 → streak 증가
        self._tunnel_fail_streak += 1
        streak = self._tunnel_fail_streak
        max_streak = self._tunnel_max_fail_streak

        # 2회 연속 실패 → cloudflared 자체 재시작 (origin stale 대응)
        # Worker resync만으로는 해결 안 되는 "cloudflared 살아있지만 origin 끊김" 상황 복구
        if streak >= max_streak:
            self.tunnel_item.title = f"CF Tunnel: ⚡ restarting cloudflared"
            try:
                uid = os.getuid()
                subprocess.run(
                    ["launchctl", "kickstart", "-k", f"gui/{uid}/com.hermes.tunnel"],
                    capture_output=True, timeout=10,
                )
                rumps.notification(
                    "Hermes", "CF Tunnel",
                    f"Restarted cloudflared (streak={streak}) — new URL incoming",
                )
                self._tunnel_fail_streak = 0  # reset, 재시작 후 다시 평가
            except Exception as e:
                self.tunnel_item.title = f"CF Tunnel: ✗ restart error"
                rumps.notification("Hermes", "CF Tunnel ERROR", str(e))
            return

        # 1회 실패는 Worker resync만 시도 (일시적 propagation 문제 대응)
        self.tunnel_item.title = f"CF Tunnel: ⟳ resyncing ({streak}/{max_streak})"
        if sync_script.exists():
            try:
                result = subprocess.run(
                    [str(sync_script), tunnel_url],
                    capture_output=True, timeout=15, text=True,
                )
                if result.returncode == 0:
                    self.tunnel_item.title = f"CF Tunnel: ⟳ resynced, re-checking…"
                else:
                    self.tunnel_item.title = f"CF Tunnel: ✗ sync failed ({streak}/{max_streak})"
            except Exception:
                self.tunnel_item.title = f"CF Tunnel: ✗ sync error"
        else:
            self.tunnel_item.title = f"CF Tunnel: ✗ no sync script"

    def open_dashboard(self, _):
        """Open dashboard in browser."""
        url = f"http://127.0.0.1:{API_PORT}/dashboard"
        if API_KEY:
            url += f"?key={API_KEY}"
        webbrowser.open(url)

    def restart_gateway(self, _):
        """Restart gateway via launchctl."""
        try:
            uid = os.getuid()
            subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/ai.hermes.gateway"],
                capture_output=True, timeout=10,
            )
            rumps.notification("Hermes", "", "Gateway restart requested")
        except Exception as e:
            rumps.notification("Hermes", "Error", str(e))

    def view_logs(self, _):
        """Open gateway log in Console.app."""
        log_file = HERMES_HOME / "logs" / "gateway.log"
        if log_file.exists():
            subprocess.Popen(["open", "-a", "Console", str(log_file)])
        else:
            rumps.notification("Hermes", "", "Log file not found")


if __name__ == "__main__":
    HermesMonitorApp().run()
