import 'dotenv/config';

const REQUIRED_KEYS = {
  ANTHROPIC_API_KEY: 'AI tutoring (all six agents, outline extraction, photo coaching)',
  NOCODB_URL: 'persistent storage (courses, topics, dates, quiz results, sessions, spaced repetition)',
  NOCODB_API_TOKEN: 'persistent storage (NocoDB auth)',
  NOCODB_BASE_ID: 'persistent storage (NocoDB base)',
  RESEND_API_KEY: 'weekly email digest',
  DIGEST_TO_EMAIL: 'weekly email digest (recipient)',
  DO_SPACES_KEY: 'file storage (outline PDFs, work photos)',
  DO_SPACES_SECRET: 'file storage (Spaces auth)',
  DO_SPACES_BUCKET: 'file storage (Spaces bucket)',
  APP_PASSPHRASE: 'login — the app will refuse logins until this is set',
};

export const config = {
  anthropicApiKey: process.env.ANTHROPIC_API_KEY || '',
  model: process.env.CLAUDE_MODEL || 'claude-sonnet-4-6',
  nocodb: {
    url: (process.env.NOCODB_URL || '').replace(/\/+$/, ''),
    token: process.env.NOCODB_API_TOKEN || '',
    baseId: process.env.NOCODB_BASE_ID || '',
  },
  resendApiKey: process.env.RESEND_API_KEY || '',
  digestToEmail: process.env.DIGEST_TO_EMAIL || '',
  digestFromEmail: process.env.DIGEST_FROM_EMAIL || 'TutorAgent <onboarding@resend.dev>',
  spaces: {
    key: process.env.DO_SPACES_KEY || '',
    secret: process.env.DO_SPACES_SECRET || '',
    bucket: process.env.DO_SPACES_BUCKET || '',
    region: process.env.DO_SPACES_REGION || 'tor1',
  },
  passphrase: process.env.APP_PASSPHRASE || '',
  port: parseInt(process.env.PORT || '3000', 10),
};

export const features = {
  ai: Boolean(config.anthropicApiKey),
  nocodb: Boolean(config.nocodb.url && config.nocodb.token && config.nocodb.baseId),
  email: Boolean(config.resendApiKey && config.digestToEmail),
  spaces: Boolean(config.spaces.key && config.spaces.secret && config.spaces.bucket),
  auth: Boolean(config.passphrase),
};

export function reportMissingConfig(log = console) {
  const missing = Object.keys(REQUIRED_KEYS).filter((k) => !process.env[k]);
  for (const key of missing) {
    log.warn(`[config] Missing ${key} — disabled: ${REQUIRED_KEYS[key]}`);
  }
  if (!features.nocodb) {
    log.warn('[config] NocoDB not configured — falling back to in-memory storage (data is lost on restart).');
  }
  return missing;
}
