// Payment page: live card preview, validation, then POST /api/payment.

(function () {
  const raw = sessionStorage.getItem("sa_booking");
  if (!raw) {
    document.getElementById("content").innerHTML = `
      <h1>No booking in progress</h1>
      <p class="subhead">Your reservation expired. Let's start over.</p>
      <p><a class="btn btn-primary" href="booking.html">Start a booking</a></p>
    `;
    return;
  }
  let booking;
  try { booking = JSON.parse(raw); } catch { booking = null; }
  if (!booking || !booking.booking_id) {
    location.href = "booking.html";
    return;
  }

  const money = (n) => "$" + n.toLocaleString();

  // Render reservation summary
  const sum = document.getElementById("booking-summary");
  const itemLines = booking.items.map((it) =>
    `<div class="line"><span>${it.qty} × ${it.name}</span><span>${money(it.line)}</span></div>`
  ).join("");
  sum.innerHTML = `
    <div class="line"><span>Booking</span><span class="code">${booking.booking_id}</span></div>
    <div class="line"><span>Dates</span><span>${booking.start_date} → ${booking.end_date}</span></div>
    ${itemLines}
    <div class="line"><span>Subtotal</span><span>${money(booking.subtotal)}</span></div>
    ${booking.discount > 0 ? `<div class="line"><span>Discount</span><span>−${money(booking.discount)}</span></div>` : ""}
    <div class="line"><span>GST</span><span>${money(booking.tax)}</span></div>
    <div class="line total"><span>Total</span><span>${money(booking.total)}</span></div>
  `;
  document.getElementById("pay-amount").textContent = money(booking.total);

  // Live card display formatting
  const cardholder = document.getElementById("cardholder");
  const cardnum = document.getElementById("cardnum");
  const exp = document.getElementById("exp");
  const cvc = document.getElementById("cvc");
  const cardDisplay = document.getElementById("card-display");
  const expDisplay = document.getElementById("exp-display");
  const nameDisplay = document.getElementById("cardholder-display");

  cardholder.addEventListener("input", () => {
    nameDisplay.textContent = cardholder.value.toUpperCase() || "CARDHOLDER NAME";
  });

  cardnum.addEventListener("input", () => {
    let v = cardnum.value.replace(/\D/g, "").slice(0, 19);
    cardnum.value = v.replace(/(.{4})/g, "$1 ").trim();
    cardDisplay.textContent = (cardnum.value + " •••• •••• •••• ••••").slice(0, 19).padEnd(19, "•");
  });

  exp.addEventListener("input", () => {
    let v = exp.value.replace(/\D/g, "").slice(0, 4);
    if (v.length >= 3) v = v.slice(0, 2) + "/" + v.slice(2);
    exp.value = v;
    expDisplay.textContent = v || "MM / YY";
  });
  cvc.addEventListener("input", () => {
    cvc.value = cvc.value.replace(/\D/g, "").slice(0, 4);
  });

  function showError(msg) {
    document.getElementById("error-slot").innerHTML = `<div class="alert alert-error">${msg}</div>`;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function renderConfirmation(payment) {
    document.getElementById("content").innerHTML = `
      <ol class="flow-steps">
        <li class="is-done">1 · Choose gear &amp; dates</li>
        <li class="is-done">2 · Payment</li>
        <li class="is-active">3 · Confirmation</li>
      </ol>
      <section class="panel confirm-block">
        <div class="check">✓</div>
        <h1>You're booked!</h1>
        <p class="subhead">Confirmation sent to <strong>${booking.customer.email}</strong>.</p>
        <p>Booking code: <span class="code">${booking.booking_id}</span></p>
        <p>Charged <strong>${money(payment.amount)}</strong> · receipt <span class="code">${payment.receipt_id}</span></p>
        <p style="margin:24px 0 8px;">Pickup at <strong>38123 Cleveland Ave, Squamish</strong> any time after 8am on ${booking.start_date}. Bring photo ID.</p>
        <p><a class="btn btn-primary" href="index.html">Back to home</a></p>
      </section>
    `;
    sessionStorage.removeItem("sa_booking");
  }

  document.getElementById("pay-btn").addEventListener("click", async () => {
    document.getElementById("error-slot").innerHTML = "";
    const num = cardnum.value.replace(/\s/g, "");
    if (!cardholder.value.trim()) { showError("Enter the cardholder name."); return; }
    if (!/^\d{13,19}$/.test(num)) { showError("Card number looks wrong — please re-check."); return; }
    if (!/^\d{2}\/\d{2}$/.test(exp.value)) { showError("Expiry should look like 08/28."); return; }
    const [mm, yy] = exp.value.split("/").map((x) => parseInt(x, 10));
    if (mm < 1 || mm > 12) { showError("Expiry month must be 01–12."); return; }
    const now = new Date();
    const expDate = new Date(2000 + yy, mm, 0);
    if (expDate < now) { showError("Card has expired."); return; }
    if (!/^\d{3,4}$/.test(cvc.value)) { showError("CVC should be 3 or 4 digits."); return; }

    const btn = document.getElementById("pay-btn");
    btn.disabled = true; btn.textContent = "Processing…";
    // Static-demo: simulate the charge client-side. Cards ending in 0000
    // still decline, just like the deployed /api/payment endpoint.
    setTimeout(() => {
      if (num.slice(-4) === "0000") {
        showError("Card was declined. Please try another card.");
        btn.disabled = false; btn.textContent = "Pay " + money(booking.total);
        return;
      }
      const data = {
        booking_id: booking.booking_id,
        amount: booking.total,
        receipt_id: "RCPT-" + Math.random().toString(16).slice(2, 10).toUpperCase(),
        status: "paid",
      };
      renderConfirmation(data);
    }, 700);
  });
})();
