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
          <a class="btn btn-primary" href="booking.html?sku=${encodeURIComponent(p.sku)}">Book</a>
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

  // Static-demo chat: keyword matcher with a few canned answers + price
  // lookups from the live catalog. The deployed branch swaps this for a
  // call to /api/chat (Claude Haiku 4.5).
  function localReply(q) {
    const t = q.toLowerCase();
    const has = (...xs) => xs.some((x) => t.includes(x));

    if (has("hour", "open", "close", "what time")) {
      return "We're open every day, 8am–6pm. Same-day pickup until 4pm so we can get you fitted before close.";
    }
    if (has("address", "where", "located", "location", "find you")) {
      return "We're at 38123 Cleveland Ave in downtown Squamish — about 10 minutes' walk to the Mamquam Blind Channel launch and 4 minutes' drive to the Bench trail network.";
    }
    if (has("phone", "call", "number")) {
      return "You can reach us at (604) 555-0144 or hello@squamishadventurerentals.com.";
    }
    if (has("cancel", "refund")) {
      return "Free cancellation up to 24 hours before your pickup. After that we charge for the first day.";
    }
    if (has("discount", "deal", "cheaper", "multi-day", "long")) {
      return "Yep — 10% off for 3–6 days, 20% off for 7+ days. It's applied automatically on the booking page.";
    }
    if (has("tax", "gst")) {
      return "5% GST is added on top of the post-discount subtotal — you'll see it broken out in the booking summary.";
    }
    if (has("helmet", "lock", "pfd", "paddle", "include")) {
      return "Bikes come with a helmet and lock; kayaks, SUPs and canoes come with paddles and PFDs. All free.";
    }
    if (has("trail", "ride", "mtb", "mountain bike") && !has("price", "cost")) {
      return "For a first Squamish ride try Half Nelson — it's a flowy blue at the Bench. Stronger riders love Credit Line and Pseudo Tsuga. We can sketch a loop for you when you pick up the bike.";
    }
    if (has("paddle", "kayak", "sup", "canoe") && has("where", "best", "calm", "first")) {
      return "Mamquam Blind Channel is the calm-water pick — 10 minutes' walk from the shop, glassy in the morning. Howe Sound proper gets choppy when the afternoon wind builds.";
    }
    if (has("wind", "kite", "windsurf")) {
      return "The famous Squamish thermal usually fills in after 11am in summer. Mornings = paddle weather, afternoons = wind sport weather.";
    }
    if (has("camp", "camping", "tent")) {
      return "Alice Lake (15 min north) and Paradise Valley (20 min north) are the closest car-camping spots. Our $39/day Backcountry Gear Bundle has a tent, two bags, two pads, stove and cookset.";
    }
    if (has("ebike", "e-bike", "electric")) {
      return "E-bikes are $99/day, ~80km range, and come with a helmet and lock. Great for a long Sea-to-Sky cruise.";
    }
    if (has("bike", "mtb")) {
      return "Full-suspension mountain bikes are $89/day, freshly tuned the morning of your ride. Helmet & lock included.";
    }
    if (has("kayak")) {
      return "Single kayaks are $49/day; tandems are $79/day. Both come with paddles and PFDs.";
    }
    if (has("sup", "paddleboard", "stand up", "stand-up")) {
      return "Stand-up paddleboards are $49/day — 10'6\" inflatable, paddle, leash, PFD and pump included.";
    }
    if (has("canoe")) {
      return "Canoes are $69/day — 16' tandem with two paddles and two PFDs.";
    }
    if (has("price", "cost", "how much", "rate")) {
      const lines = (window.CATALOG || []).map((p) => `• ${p.name}: $${p.price}/day`).join("\n");
      return "Daily rates:\n" + lines + "\n\nMulti-day discount kicks in at 3 days.";
    }
    if (has("book", "reserve", "rent")) {
      return "Tap “Book now” at the top — pick your dates, gear and pay by card. You'll get a booking code right away and the gear will be tuned and ready when you arrive.";
    }
    if (has("payment", "card", "stripe", "pay")) {
      return "Card payment happens after you fill in the booking form. We never store your full card number on our servers.";
    }
    if (has("hi", "hey", "hello", "yo")) {
      return "Hey! Looking for gear suggestions, pricing, or help with a booking?";
    }
    if (has("thank")) {
      return "Anytime! Have a good one out there.";
    }
    return "Good question — let me have a human follow up. Drop your email and a contact number here and we'll reply within the hour during shop hours.";
  }

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    append("user", text);
    history.push({ role: "user", content: text });
    const thinking = append("bot", "thinking…", "thinking");
    setTimeout(() => {
      thinking.remove();
      const reply = localReply(text);
      append("bot", reply);
      history.push({ role: "assistant", content: reply });
    }, 350 + Math.random() * 500);
  });
})();
