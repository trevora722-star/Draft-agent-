/* Squamish Adventure Rentals — WordPress (or any external site) embed.
 *
 * Usage from WordPress (or any HTML page):
 *
 *   <!-- Chat widget on every page (drop in the site footer / global header): -->
 *   <script async src="https://YOUR-SITE.netlify.app/embed.js"></script>
 *
 *   <!-- Booking flow on a specific page (Custom HTML block): -->
 *   <div id="sar-booking"></div>
 *   <script async src="https://YOUR-SITE.netlify.app/embed.js"></script>
 *
 * The embed renders the booking flow as an iframe pointed at our hosted
 * /booking.html. The booking page emits its content height back via
 * window.postMessage so the iframe resizes seamlessly inside the host page.
 *
 * Opt-outs (set on the <script> tag):
 *   data-no-chat="true"     skip the floating chat widget
 *   data-no-booking="true"  skip mounting the booking iframe
 *   data-site="https://..." override the API origin (defaults to the script's origin)
 *
 * All API calls (chat / booking / payment) go to the same Netlify origin
 * the script is served from. The Functions there already send
 * Access-Control-Allow-Origin: *, so this works on any host.
 */
(function () {
  // ---- config ---------------------------------------------------------
  const me = document.currentScript;
  const opt = (key) => me && me.dataset && me.dataset[key] === "true";
  const SITE = (() => {
    if (me && me.dataset && me.dataset.site) return me.dataset.site.replace(/\/$/, "");
    if (me && me.src) return new URL(me.src).origin;
    return window.location.origin;
  })();

  // ---- chat widget ----------------------------------------------------
  // Scoped CSS — every selector starts with .sar-* so we don't collide
  // with the host theme. Visual language matches the parent site.
  const CHAT_CSS = `
.sar-chat-widget * { box-sizing: border-box; }
.sar-chat-widget {
  position: fixed; bottom: 22px; right: 22px; z-index: 99999;
  font-family: 'Inter', system-ui, -apple-system, Segoe UI, sans-serif;
  font-size: 14px; line-height: 1.45; color: #0f1d23;
}
.sar-chat-toggle {
  display: inline-flex; align-items: center; gap: 8px;
  background: #1f4d3f; color: #fff; border: 0; cursor: pointer;
  padding: 14px 18px; border-radius: 999px; font-weight: 600;
  font-size: 15px; line-height: 1; font-family: inherit;
  box-shadow: 0 12px 28px rgba(15, 29, 35, .25);
}
.sar-chat-toggle:hover { background: #143329; }
.sar-chat-panel {
  position: absolute; bottom: 64px; right: 0;
  width: 360px; max-width: calc(100vw - 28px);
  height: 480px; max-height: calc(100vh - 120px);
  background: #fff; border: 1px solid #e5e2d8; border-radius: 14px;
  box-shadow: 0 24px 48px rgba(15, 29, 35, .22);
  display: flex; flex-direction: column; overflow: hidden;
}
.sar-chat-panel[hidden] { display: none; }
.sar-chat-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 10px 10px 16px; background: #1f4d3f; color: #fff;
}
.sar-chat-head strong { font-weight: 700; }
.sar-chat-close {
  background: transparent; border: 0; color: #fff;
  width: 36px; height: 36px; border-radius: 8px;
  font-size: 24px; line-height: 1; padding: 0;
  cursor: pointer; display: grid; place-items: center;
}
.sar-chat-close:hover { background: rgba(255,255,255,.18); }
.sar-chat-log {
  flex: 1; overflow-y: auto; padding: 14px; background: #fbfaf6;
  display: flex; flex-direction: column; gap: 10px;
}
.sar-bubble {
  padding: 10px 12px; border-radius: 12px; max-width: 85%;
  white-space: pre-wrap; word-wrap: break-word;
}
.sar-bubble-user { align-self: flex-end; background: #1f4d3f; color: #fff; border-bottom-right-radius: 2px; }
.sar-bubble-bot  { align-self: flex-start; background: #fff; border: 1px solid #e5e2d8; border-bottom-left-radius: 2px; }
.sar-bubble-bot.sar-thinking { color: #36464d; font-style: italic; }
.sar-chat-form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e5e2d8; background: #fff; }
.sar-chat-form input {
  flex: 1; padding: 10px 12px; border: 1px solid #e5e2d8; border-radius: 999px;
  font: inherit; color: inherit; background: #fff;
}
.sar-chat-form input:focus { outline: none; border-color: #1f4d3f; }
.sar-chat-form button {
  background: #1f4d3f; color: #fff; border: 0; border-radius: 999px;
  padding: 0 16px; cursor: pointer; font-weight: 600; font-family: inherit;
}
`;

  const CHAT_HTML = `
<button class="sar-chat-toggle" type="button" aria-label="Open chat">💬<span>Ask us</span></button>
<div class="sar-chat-panel" hidden>
  <header class="sar-chat-head">
    <strong>Ask Adam (concierge)</strong>
    <button class="sar-chat-close" type="button" aria-label="Close chat">×</button>
  </header>
  <div class="sar-chat-log"></div>
  <form class="sar-chat-form">
    <input type="text" autocomplete="off" placeholder="First-time rider? Group of 4? Ask away…" required />
    <button type="submit">Send</button>
  </form>
</div>`;

  function injectChat() {
    if (opt("noChat")) return;
    if (document.querySelector(".sar-chat-widget")) return; // dedupe across multiple includes

    const style = document.createElement("style");
    style.textContent = CHAT_CSS;
    document.head.appendChild(style);

    const host = document.createElement("div");
    host.className = "sar-chat-widget";
    host.innerHTML = CHAT_HTML;
    document.body.appendChild(host);

    const toggle = host.querySelector(".sar-chat-toggle");
    const panel = host.querySelector(".sar-chat-panel");
    const closeBtn = host.querySelector(".sar-chat-close");
    const log = host.querySelector(".sar-chat-log");
    const form = host.querySelector(".sar-chat-form");
    const input = form.querySelector("input");
    const history = [];

    const append = (role, text, cls) => {
      const div = document.createElement("div");
      div.className = "sar-bubble sar-bubble-" + role + (cls ? " " + cls : "");
      div.textContent = text;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      return div;
    };

    const open = () => {
      panel.hidden = false;
      toggle.style.display = "none";
      if (!log.childElementCount) {
        append("bot", "Hey! I'm Adam's concierge. Ask about the rides, what to wear, group bookings, or anything else.");
      }
      setTimeout(() => input.focus(), 50);
    };
    const shut = () => {
      panel.hidden = true;
      toggle.style.display = "";
    };

    toggle.addEventListener("click", () => (panel.hidden ? open() : shut()));
    closeBtn.addEventListener("click", shut);
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !panel.hidden) shut();
    });

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      append("user", text);
      history.push({ role: "user", content: text });
      const thinking = append("bot", "thinking…", "sar-thinking");
      try {
        const res = await fetch(SITE + "/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: history }),
        });
        const data = await res.json();
        thinking.remove();
        if (!res.ok) {
          append("bot", "Sorry — " + (data.error || "the assistant is offline right now."));
          return;
        }
        append("bot", data.reply);
        history.push({ role: "assistant", content: data.reply });
      } catch (e) {
        thinking.remove();
        append("bot", "Connection error. Try again in a moment.");
      }
    });

    // Public hook so a CTA button on the host page (e.g. <button onclick="SAR.openChat()">)
    // can pop the chat open from anywhere.
    window.SAR = window.SAR || {};
    window.SAR.openChat = open;
    window.SAR.closeChat = shut;
  }

  // ---- booking iframe -------------------------------------------------
  // Renders /booking.html inside an iframe and auto-resizes via postMessage.
  // The hosted page (site.js) emits {type:'sar-resize', height} whenever
  // its content reflows.
  function injectBooking() {
    if (opt("noBooking")) return;
    const mount = document.getElementById("sar-booking");
    if (!mount) return;
    if (mount.dataset.sarMounted) return;
    mount.dataset.sarMounted = "1";

    const iframe = document.createElement("iframe");
    iframe.src = SITE + "/booking.html?embed=1";
    iframe.title = "Book your ride — Squamish Adventure Rentals";
    iframe.allow = "payment";
    // Style: full width, no border, sensible starting height while the
    // iframe boots and reports its real size.
    iframe.style.cssText = "width:100%; border:0; min-height:1100px; display:block; background:#fbfaf6;";
    mount.appendChild(iframe);

    window.addEventListener("message", (ev) => {
      if (ev.origin !== SITE) return;
      const m = ev.data;
      if (!m || typeof m !== "object") return;
      if (m.type === "sar-resize" && typeof m.height === "number") {
        // Add a tiny bottom buffer so internal scrollbars never appear.
        iframe.style.height = (m.height + 24) + "px";
      }
      if (m.type === "sar-navigate" && typeof m.path === "string") {
        // The booking page navigates to /payment.html and back. We let it
        // navigate inside the iframe and just keep tracking the height.
      }
    });
  }

  // ---- run ------------------------------------------------------------
  function init() {
    injectChat();
    injectBooking();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
