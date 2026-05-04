// Shared catalog. Mirrored server-side in netlify/functions/_rentals.py — keep in sync.
// Pricing is editable in one place: bump it here and in _rentals.py CATALOG below.
window.CATALOG = [
  {
    sku: "guided-half",
    kind: "guided",
    name: "Guided ATV Tour — Half Day",
    desc: "Ride with a local guide. ~3 hours of trail time, full safety brief and gear included. Great for first-timers.",
    price: 279,
    unit: "rider",
    icon: "🏔",
  },
  {
    sku: "guided-full",
    kind: "guided",
    name: "Guided ATV Tour — Full Day",
    desc: "Full-day backcountry tour with a guide. Lunch on the trail. The deepest look at the Sea-to-Sky we offer.",
    price: 429,
    unit: "rider",
    icon: "⛰",
  },
  {
    sku: "self-half",
    kind: "self",
    name: "Self-Guided Rental — Half Day",
    desc: "Ride at your own pace. ATV, helmet, route map and pre-ride safety brief. Half-day on local trails.",
    price: 229,
    unit: "ATV",
    icon: "🛞",
  },
  {
    sku: "self-full",
    kind: "self",
    name: "Self-Guided Rental — Full Day",
    desc: "All-day rental with route map. Best value for confident riders who want to cover more ground.",
    price: 349,
    unit: "ATV",
    icon: "🛞",
  },
];

window.priceFor = function (sku) {
  const item = window.CATALOG.find((p) => p.sku === sku);
  return item ? item.price : 0;
};
