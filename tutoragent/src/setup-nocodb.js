// Standalone script: creates the NocoDB tables if they don't exist.
// Usage: npm run setup-nocodb  (reads .env)
import { features } from './config.js';
import { createStore } from './store.js';

if (!features.nocodb) {
  console.error('NocoDB is not configured — set NOCODB_URL, NOCODB_API_TOKEN, and NOCODB_BASE_ID in .env');
  process.exit(1);
}

const store = createStore();
try {
  await store.init();
  console.log('NocoDB tables are ready:', Object.entries(store.tableIds).map(([k, v]) => `${k}=${v}`).join(', '));
} catch (err) {
  console.error('Setup failed:', err.message);
  process.exit(1);
}
