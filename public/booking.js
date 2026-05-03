// Booking page logic — quote calculation + handoff to /payment.html.

(function () {
  const list = document.getElementById("qty-list");
  const start = document.getElementById("start");
  const end = document.getElementById("end");
  const linesEl = document.getElementById("summary-lines");
  const errSlot = document.getElementById("error-slot");
  const continueBtn = document.getElementById("continue-btn");

  if (!list || !window.CATALOG) return;

  const qty = {};
  window.CATALOG.forEach((p) => (qty[p.sku] = 0));

  // Pre-select if ?sku= is in URL
  const params = new URLSearchParams(location.search);
  const presku = params.get("sku");
  if (presku && qty.hasOwnProperty(presku)) qty[presku] = 1;

  // Default dates: today + tomorrow
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 24 * 3600 * 1000);
  const fmt = (d) => d.toISOString().slice(0, 10);
  start.value = fmt(today);
  end.value = fmt(tomorrow);
  start.min = fmt(today);
  end.min = fmt(tomorrow);

  list.innerHTML = window.CATALOG.map((p) => `
    <div class="qty-row">
      <div class="name">${p.icon} ${p.name}<small>$${p.price}/day · ${p.desc}</small></div>
      <div class="qty-control" data-sku="${p.sku}">
        <button type="button" data-d="-1" aria-label="decrease">−</button>
        <span data-count>${qty[p.sku]}</span>
        <button type="button" data-d="1" aria-label="increase">+</button>
      </div>
    </div>
  `).join("");

  list.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-d]");
    if (!btn) return;
    const ctrl = btn.parentElement;
    const sku = ctrl.dataset.sku;
    const delta = parseInt(btn.dataset.d, 10);
    qty[sku] = Math.max(0, Math.min(20, qty[sku] + delta));
    ctrl.querySelector("[data-count]").textContent = qty[sku];
    refresh();
  });

  start.addEventListener("change", () => {
    if (end.value <= start.value) {
      const d = new Date(start.value + "T00:00:00");
      d.setDate(d.getDate() + 1);
      end.value = fmt(d);
    }
    end.min = end.value;
    refresh();
  });
  end.addEventListener("change", refresh);

  function days() {
    if (!start.value || !end.value) return 0;
    const a = new Date(start.value + "T00:00:00");
    const b = new Date(end.value + "T00:00:00");
    const d = Math.round((b - a) / (24 * 3600 * 1000));
    return Math.max(1, d);
  }

  function quote() {
    const d = days();
    let subtotal = 0;
    const items = [];
    window.CATALOG.forEach((p) => {
      if (qty[p.sku] > 0) {
        const line = qty[p.sku] * p.price * d;
        subtotal += line;
        items.push({ sku: p.sku, name: p.name, qty: qty[p.sku], price: p.price, line });
      }
    });
    // Multi-day discount: 10% off for 3-6 days, 20% off for 7+
    let discPct = 0;
    if (d >= 7) discPct = 0.20;
    else if (d >= 3) discPct = 0.10;
    const discount = Math.round(subtotal * discPct);
    const taxable = subtotal - discount;
    const tax = Math.round(taxable * 0.05);
    const total = taxable + tax;
    return { d, items, subtotal, discount, discPct, tax, total };
  }

  function money(n) {
    return "$" + n.toLocaleString();
  }

  function refresh() {
    const q = quote();
    document.getElementById("sum-days").textContent = q.d;
    document.getElementById("sum-subtotal").textContent = money(q.subtotal);
    document.getElementById("sum-discount").textContent =
      q.discount > 0 ? "−" + money(q.discount) + ` (${Math.round(q.discPct*100)}%)` : "−$0";
    document.getElementById("sum-tax").textContent = money(q.tax);
    document.getElementById("sum-total").textContent = money(q.total);
    if (q.items.length === 0) {
      linesEl.innerHTML = `<p class="empty">No gear selected yet.</p>`;
    } else {
      linesEl.innerHTML = q.items.map((it) => `
        <div class="line"><span>${it.qty} × ${it.name}</span><span>${money(it.line)}</span></div>
      `).join("");
    }
    continueBtn.disabled = q.items.length === 0 || q.total <= 0;
  }
  refresh();

  function showError(msg) {
    errSlot.innerHTML = `<div class="alert alert-error">${msg}</div>`;
    errSlot.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  continueBtn.addEventListener("click", async () => {
    errSlot.innerHTML = "";
    const name = document.getElementById("name").value.trim();
    const email = document.getElementById("email").value.trim();
    const phone = document.getElementById("phone").value.trim();
    const notes = document.getElementById("notes").value.trim();
    if (!name || !email || !phone) {
      showError("Please fill in your name, email and phone so we can reach you.");
      return;
    }
    const q = quote();
    if (q.items.length === 0) { showError("Pick at least one piece of gear."); return; }

    const payload = {
      start_date: start.value,
      end_date: end.value,
      items: q.items.map((it) => ({ sku: it.sku, qty: it.qty })),
      customer: { name, email, phone, notes },
    };

    continueBtn.disabled = true;
    continueBtn.textContent = "Reserving…";
    try {
      const res = await fetch("/api/booking", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        showError(data.error || "Couldn't create the booking. Try again.");
        continueBtn.disabled = false;
        continueBtn.textContent = "Continue to payment →";
        return;
      }
      sessionStorage.setItem("sa_booking", JSON.stringify(data));
      location.href = "/payment.html";
    } catch (e) {
      showError("Network error. Please try again.");
      continueBtn.disabled = false;
      continueBtn.textContent = "Continue to payment →";
    }
  });
})();
