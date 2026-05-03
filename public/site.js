// Site-wide JS: catalog rendering + chat widget.

(function renderCatalog() {
  const grid = document.getElementById("catalog-grid");
  if (!grid || !window.CATALOG) return;
  grid.innerHTML = window.CATALOG.map((p) => `
    <article class="card" data-kind="${p.kind}">
      <div class="card-art" aria-hidden="true">${p.icon}</div>
      <div class="card-body">
        <h3>${p.name}</h3>
        <p class="desc">${p.desc}</p>
        <div class="card-foot">
          <div class="price">$${p.price}<small>/day</small></div>
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
      "Hi! I'm the Squamish Adventure assistant. Ask me about gear, trail picks, or how booking works."
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

  toggle.addEventListener("click", open);
  close.addEventListener("click", shut);

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
