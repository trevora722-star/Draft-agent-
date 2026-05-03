// Shared catalog. Mirrored server-side in netlify/functions/_rentals.py — keep in sync.
window.CATALOG = [
  {
    sku: "mtb-full",
    kind: "bike",
    name: "Mountain Bike — Full Suspension",
    desc: "Trail-ready 29er, dropper post, fresh tune. Helmet & lock included.",
    price: 89,
    icon: "🚵",
  },
  {
    sku: "ebike",
    kind: "ebike",
    name: "E-Bike",
    desc: "Pedal-assist commuter / trail e-bike. ~80km range. Helmet & lock included.",
    price: 99,
    icon: "⚡",
  },
  {
    sku: "kayak-single",
    kind: "kayak",
    name: "Kayak — Single",
    desc: "Stable recreational kayak for the Mamquam Blind Channel & Howe Sound.",
    price: 49,
    icon: "🛶",
  },
  {
    sku: "kayak-tandem",
    kind: "kayak",
    name: "Kayak — Tandem",
    desc: "Two-person sea kayak with rudder. PFDs and paddles included.",
    price: 79,
    icon: "🛶",
  },
  {
    sku: "sup",
    kind: "sup",
    name: "Stand-Up Paddleboard",
    desc: "All-around 10'6\" inflatable SUP. Paddle, leash, PFD, pump.",
    price: 49,
    icon: "🏄",
  },
  {
    sku: "canoe",
    kind: "canoe",
    name: "Canoe",
    desc: "16' tandem canoe. Two paddles & two PFDs included.",
    price: 69,
    icon: "🛶",
  },
  {
    sku: "gear",
    kind: "gear",
    name: "Backcountry Gear Bundle",
    desc: "Tent, two sleeping bags, two pads, stove, cookset. Trip-ready.",
    price: 39,
    icon: "🏕",
  },
];

window.priceFor = function (sku) {
  const item = window.CATALOG.find((p) => p.sku === sku);
  return item ? item.price : 0;
};
