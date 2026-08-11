import Stripe from 'stripe';

let _client = null;

export function getStripe() {
  if (_client) return _client;
  if (!process.env.STRIPE_SECRET_KEY) {
    throw new Error('STRIPE_SECRET_KEY missing');
  }
  _client = new Stripe(process.env.STRIPE_SECRET_KEY, { apiVersion: '2024-06-20' });
  return _client;
}

export async function createPromoCode({
  code,
  customerEmail,
  percentOff = 20,
  expiresInHours = 72,
  maxRedemptions = 1,
}) {
  const stripe = getStripe();
  const coupon = await stripe.coupons.create({
    percent_off: percentOff,
    duration: 'once',
    max_redemptions: maxRedemptions,
    metadata: { customer_email: customerEmail || '' },
  });
  const promo = await stripe.promotionCodes.create({
    coupon: coupon.id,
    code,
    max_redemptions: maxRedemptions,
    expires_at: Math.floor(Date.now() / 1000) + expiresInHours * 3600,
    metadata: { customer_email: customerEmail || '' },
  });
  return { coupon, promo };
}

export async function applyAccountCredit({ customerId, amountCad, description }) {
  const stripe = getStripe();
  return stripe.customers.createBalanceTransaction(customerId, {
    amount: -Math.round(amountCad * 100),
    currency: 'cad',
    description,
  });
}

export async function listSubscriptions({ status = 'all', limit = 100 } = {}) {
  const stripe = getStripe();
  const subs = [];
  let starting_after;
  while (true) {
    const page = await stripe.subscriptions.list({
      status,
      limit,
      ...(starting_after ? { starting_after } : {}),
    });
    subs.push(...page.data);
    if (!page.has_more) break;
    starting_after = page.data[page.data.length - 1].id;
  }
  return subs;
}

export async function getMRR() {
  const subs = await listSubscriptions({ status: 'active' });
  let mrr = 0;
  for (const s of subs) {
    for (const item of s.items.data) {
      const interval = item.price.recurring?.interval;
      const intervalCount = item.price.recurring?.interval_count || 1;
      const unitAmount = (item.price.unit_amount || 0) / 100;
      const qty = item.quantity || 1;
      let monthly = 0;
      if (interval === 'month') monthly = (unitAmount * qty) / intervalCount;
      else if (interval === 'year') monthly = (unitAmount * qty) / (12 * intervalCount);
      else if (interval === 'week') monthly = (unitAmount * qty * 4.345) / intervalCount;
      mrr += monthly;
    }
  }
  return mrr;
}
