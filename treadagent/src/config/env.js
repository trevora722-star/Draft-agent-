// Central environment access. Fails fast on import if a required var is
// missing so misconfiguration is caught at startup, not mid-request.
// Integration-specific vars (Twilio, Resend, Stripe, Spaces, Anthropic)
// are validated lazily by the module that uses them, so `node --test`
// and offline/console-adapter dev don't require every credential.

const REQUIRED = ["NOCODB_BASE_URL", "NOCODB_API_TOKEN", "NOCODB_BASE_ID"];

function missingRequired(env = process.env) {
  return REQUIRED.filter((key) => !env[key] || env[key].trim() === "");
}

export function assertRequiredEnv(env = process.env) {
  const missing = missingRequired(env);
  if (missing.length > 0) {
    throw new Error(
      `TreadAgent cannot start: missing required environment variable(s): ${missing.join(
        ", "
      )}. Copy .env.example to .env and fill them in.`
    );
  }
}

function bool(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

// Each group is a getter so values are read from process.env at access
// time rather than at module-import time. This matters for tests (which
// mutate process.env per-case) and for any future dotenv load that
// happens after this module is first imported elsewhere in the graph.
export const env = {
  get nocodb() {
    return {
      baseUrl: process.env.NOCODB_BASE_URL,
      apiToken: process.env.NOCODB_API_TOKEN,
      baseId: process.env.NOCODB_BASE_ID,
    };
  },
  get anthropic() {
    return {
      apiKey: process.env.ANTHROPIC_API_KEY,
      model: process.env.ANTHROPIC_MODEL || "claude-sonnet-5",
      monthlySpendCeilingCad: Number(
        process.env.ANTHROPIC_MONTHLY_SPEND_CEILING_CAD || 100
      ),
    };
  },
  get resend() {
    return {
      apiKey: process.env.RESEND_API_KEY,
      webhookSecret: process.env.RESEND_WEBHOOK_SECRET,
    };
  },
  get sms() {
    return {
      adapter: process.env.SMS_ADAPTER || "console",
      twilio: {
        accountSid: process.env.TWILIO_ACCOUNT_SID,
        authToken: process.env.TWILIO_AUTH_TOKEN,
        fromNumber: process.env.TWILIO_FROM_NUMBER,
      },
    };
  },
  get stripe() {
    return {
      secretKey: process.env.STRIPE_SECRET_KEY,
      webhookSecret: process.env.STRIPE_WEBHOOK_SECRET,
      prices: {
        rack: process.env.STRIPE_PRICE_RACK,
        shop: process.env.STRIPE_PRICE_SHOP,
        multiSite: process.env.STRIPE_PRICE_MULTI_SITE,
      },
    };
  },
  get spaces() {
    return {
      endpoint: process.env.SPACES_ENDPOINT || "https://tor1.digitaloceanspaces.com",
      region: process.env.SPACES_REGION || "tor1",
      bucket: process.env.SPACES_BUCKET,
      accessKeyId: process.env.SPACES_ACCESS_KEY_ID,
      secretAccessKey: process.env.SPACES_SECRET_ACCESS_KEY,
    };
  },
  get make() {
    return { webhookSecret: process.env.MAKE_WEBHOOK_SECRET };
  },
  get service() {
    return {
      port: Number(process.env.PORT || 3000),
      nodeEnv: process.env.NODE_ENV || "development",
      logLevel: process.env.LOG_LEVEL || "info",
      isProduction: process.env.NODE_ENV === "production",
    };
  },
  bool,
};

export default env;
