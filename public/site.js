// Site-wide JS: catalog rendering + image loader + chat widget.

(function renderCatalog() {
  const grid = document.getElementById("catalog-grid");
  if (!grid || !window.CATALOG) return;
  grid.innerHTML = window.CATALOG.map((p) => `
    <article class="card" data-kind="${p.kind}">
      <div class="card-art" aria-hidden="true" ${p.image ? `data-bg="${p.image}"` : ""}>${p.icon}</div>
      <div class="card-body">
        <h3>${p.name}</h3>
        <p class="desc">${p.desc}</p>
        <div class="card-foot">
          <div class="price">$${p.price}<small>${p.unit ? "/" + p.unit : "/day"}</small></div>
          <a class="btn btn-primary" href="/booking.html?sku=${encodeURIComponent(p.sku)}">Book</a>
        </div>
      </div>
    </article>
  `).join("");
})();

(function setYear() {
  const el = document.getElementById("year");
  if (el) el.textContent = new Date().getFullYear();
})();

// Brand logo: if the image fails to load (file missing in deploy), add
// .no-logo to the surrounding .brand link so the text-mark fallback shows.
(function brandLogo() {
  document.querySelectorAll(".brand-logo").forEach((img) => {
    const fail = () => {
      const brand = img.closest(".brand");
      if (brand) brand.classList.add("no-logo");
    };
    img.addEventListener("error", fail);
    if (img.complete && img.naturalWidth === 0) fail();
  });
})();

// Background-image loader: any element with data-bg="path/to.jpg" preloads
// the image and applies it as a background ONLY if it actually exists. If the
// image is missing (404 or empty file), the element keeps its CSS gradient
// placeholder so the site looks polished out of the box. Drop real photos in
// public/images/ and they appear automatically.
(function bgLoader() {
  const targets = document.querySelectorAll("[data-bg]");
  targets.forEach((el) => {
    const src = el.dataset.bg;
    if (!src) return;
    const img = new Image();
    img.onload = () => {
      // Skip the empty 1x1 placeholder we ship as a stub
      if (img.naturalWidth < 8 || img.naturalHeight < 8) return;
      el.style.backgroundImage = `url("${src}")`;
      el.classList.add("has-bg");
    };
    img.onerror = () => { /* keep CSS fallback */ };
    img.src = src;
  });
})();

// Chat widget
(function chat() {
  const toggle = document.getElementById("sa-chat-toggle");
  const close = document.getElementById("sa-chat-close");
  const panel = document.getElementById("sa-chat-panel");
  const log = document.getElementById("sa-chat-log");
  const form = document.getElementById("sa-chat-form");
  const input = document.getElementById("sa-chat-input");
  if (!toggle || !panel || !form) return;

  const history = [];

  const append = (role, text, cls = "") => {
    const div = document.createElement("div");
    div.className = "bubble " + (role === "user" ? "user" : "bot") + (cls ? " " + cls : "");
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  };

  const greet = () => {
    if (log.childElementCount > 0) return;
    append(
      "bot",
      "Hey! I'm Adam's concierge. Ask about the rides, what to wear, group bookings, or anything else."
    );
  };

  const open = () => {
    panel.hidden = false;
    toggle.style.display = "none";
    greet();
    setTimeout(() => input.focus(), 50);
  };
  const shut = () => {
    panel.hidden = true;
    toggle.style.display = "";
  };

  // Toggle reopens; close X always shuts. We also wire Escape to close,
  // so there are three ways out of the panel.
  toggle.addEventListener("click", () => (panel.hidden ? open() : shut()));
  if (close) close.addEventListener("click", shut);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !panel.hidden) shut();
  });

  // Any button with class .js-open-chat opens the panel — used by the
  // "Let's Chat" CTA on the homepage.
  document.querySelectorAll(".js-open-chat").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (panel.hidden) open();
      btn.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    append("user", text);
    history.push({ role: "user", content: text });
    const thinking = append("bot", "thinking…", "thinking");
    try {
      const res = await fetch("/api/chat", {
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
})();
