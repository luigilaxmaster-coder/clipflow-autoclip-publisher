/* AutoClip Publisher V2 — Client Core (CSRF, Sidebar, Live Health, Modals, Toasts) */
(function () {
  "use strict";

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const match = document.cookie.match(/(?:^|;\s*)clipflow_csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  /**
   * Wrapper seguro de fetch que adjunta automáticamente X-CSRF-Token en mutaciones.
   */
  window.clipflowFetch = async function (url, options = {}) {
    const opts = Object.assign({}, options);
    const method = (opts.method || "GET").toUpperCase();
    const headers = Object.assign({}, opts.headers || {});
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      const csrf = getCsrfToken();
      if (csrf) headers["X-CSRF-Token"] = csrf;
    }
    opts.headers = headers;
    opts.credentials = "same-origin";
    return fetch(url, opts);
  };

  /**
   * Notificaciones toast accesibles.
   */
  window.showToast = function (message, type = "info", durationMs = 4200) {
    let stack = document.getElementById("toastStack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "toastStack";
      stack.className = "toast-stack";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      document.body.appendChild(stack);
    }
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    const dot = document.createElement("span");
    dot.className = `status-dot ${type === "success" ? "healthy" : type === "error" ? "down" : type === "warn" ? "degraded" : ""}`;
    const txt = document.createElement("span");
    txt.textContent = message;
    el.appendChild(dot);
    el.appendChild(txt);
    stack.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity 200ms";
      setTimeout(() => el.remove(), 220);
    }, durationMs);
  };

  /**
   * Comprobación real de salud en la barra superior (/api/health).
   * Nunca muestra un punto verde falso si el servidor o DB están caídos.
   */
  async function refreshLiveHealth() {
    const badge = document.getElementById("liveHealthBadge");
    const dot = document.getElementById("liveHealthDot");
    const label = document.getElementById("liveHealthLabel");
    if (!badge || !dot || !label) return;
    try {
      const res = await fetch("/api/health", { credentials: "same-origin" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      dot.classList.remove("healthy", "degraded", "down");
      if (data.status === "healthy" && data.database_connected && data.ffmpeg_available) {
        dot.classList.add("healthy");
        label.textContent = "Sistema Operativo";
        badge.title = "SQLite OK · FFmpeg OK" + (data.postiz_online ? " · Postiz OK" : " · Postiz inactivo");
      } else {
        dot.classList.add("degraded");
        label.textContent = "Modo Degradado";
        badge.title = `DB: ${data.database_connected ? "OK" : "Error"} · FFmpeg: ${data.ffmpeg_available ? "OK" : "Falta"}`;
      }
    } catch (_e) {
      dot.classList.remove("healthy", "degraded");
      dot.classList.add("down");
      label.textContent = "Sin Conexión";
    }
  }

  /**
   * Sidebar colapsable (desktop) + drawer (móvil).
   */
  function initSidebar() {
    const shell = document.getElementById("appShell");
    const collapseBtn = document.getElementById("sidebarCollapseBtn");
    const mobileBtn = document.getElementById("mobileMenuBtn");
    const backdrop = document.getElementById("sidebarBackdrop");

    if (shell && localStorage.getItem("autoclip_sidebar_collapsed") === "1") {
      shell.classList.add("sidebar-collapsed");
    }

    if (collapseBtn && shell) {
      collapseBtn.addEventListener("click", () => {
        shell.classList.toggle("sidebar-collapsed");
        localStorage.setItem(
          "autoclip_sidebar_collapsed",
          shell.classList.contains("sidebar-collapsed") ? "1" : "0"
        );
      });
    }

    if (mobileBtn && shell) {
      mobileBtn.addEventListener("click", () => {
        shell.classList.toggle("mobile-nav-open");
      });
    }

    if (backdrop && shell) {
      backdrop.addEventListener("click", () => {
        shell.classList.remove("mobile-nav-open");
      });
    }
  }

  /**
   * Atajos de teclado: Escape cierra modales abiertos, '/' enfoca búsqueda.
   */
  function initKeyboard() {
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        document.querySelectorAll(".modal-backdrop.open").forEach((m) => m.classList.remove("open"));
        const shell = document.getElementById("appShell");
        if (shell) shell.classList.remove("mobile-nav-open");
      } else if (
        e.key === "/" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes((document.activeElement || {}).tagName)
      ) {
        const search = document.querySelector("[data-global-search]");
        if (search) {
          e.preventDefault();
          search.focus();
        }
      }
    });
  }

  function updateQuotaCountdowns() {
    const nowMs = Date.now();
    document.querySelectorAll("[data-countdown-at]").forEach((el) => {
      const iso = el.getAttribute("data-countdown-at");
      if (!iso) return;
      const targetMs = Date.parse(iso);
      if (Number.isNaN(targetMs)) return;
      const diffSec = Math.max(0, Math.floor((targetMs - nowMs) / 1000));
      if (diffSec <= 0) {
        el.textContent = "Ahora";
        return;
      }
      const totalMins = Math.ceil(diffSec / 60);
      const hours = Math.floor(totalMins / 60);
      const mins = totalMins % 60;
      el.textContent = hours > 0 ? `en ${hours}h ${String(mins).padStart(2, "0")}m` : `en ${mins}m`;
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initSidebar();
    initKeyboard();
    refreshLiveHealth();
    updateQuotaCountdowns();
    setInterval(refreshLiveHealth, 30000);
    setInterval(updateQuotaCountdowns, 30000);
  });
})();
