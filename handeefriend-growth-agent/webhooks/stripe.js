import { getStripe } from '../lib/stripe.js';
import { nocodb } from '../lib/nocodb.js';
import { nowISO } from '../lib/utils.js';
import { handleReferralEvent } from '../agents/retention/23-referral-program.js';

function planFromPriceId(priceId) {
  const m = {
    [process.env.STRIPE_PRICE_STARTER || '']: 'starter',
    [process.env.STRIPE_PRICE_PRO || '']: 'pro',
    [process.env.STRIPE_PRICE_ANNUAL || '']: 'annual',
  };
  return m[priceId] || 'starter';
}

async function findUserByCustomer(customerId) {
  return nocodb.findOne('users', `(stripe_customer_id,eq,${customerId})`);
}

export async function handleStripeWebhook(req, res) {
  const sig = req.headers['stripe-signature'];
  const stripe = getStripe();

  let event;
  try {
    event = stripe.webhooks.constructEvent(req.body, sig, process.env.STRIPE_WEBHOOK_SECRET);
  } catch (e) {
    console.error('Stripe webhook signature failed:', e.message);
    return res.status(400).send(`Webhook signature error: ${e.message}`);
  }

  try {
    switch (event.type) {
      case 'customer.subscription.created':
      case 'customer.subscription.updated': {
        const sub = event.data.object;
        const user = await findUserByCustomer(sub.customer);
        if (user) {
          const priceId = sub.items.data[0]?.price?.id;
          await nocodb.update('users', user.id, {
            plan_tier: planFromPriceId(priceId),
            stripe_subscription_id: sub.id,
            cancelled_date: sub.cancel_at_period_end ? nowISO() : null,
          });
        }
        break;
      }
      case 'customer.subscription.deleted': {
        const sub = event.data.object;
        const user = await findUserByCustomer(sub.customer);
        if (user) {
          await nocodb.update('users', user.id, {
            plan_tier: 'free',
            cancelled_date: nowISO(),
          });
        }
        break;
      }
      case 'checkout.session.completed': {
        const session = event.data.object;
        const refId = session.metadata?.referral_source || session.metadata?.ref;
        if (refId) {
          await handleReferralEvent({
            refId,
            referredEmail: session.customer_details?.email || session.customer_email,
            eventType: 'paid_convert',
            utm: session.metadata?.utm_campaign || '',
          });
        }
        break;
      }
      default:
        break;
    }
    res.json({ received: true, type: event.type });
  } catch (e) {
    console.error(`Webhook handler error for ${event.type}:`, e);
    res.status(500).json({ error: e.message });
  }
}
