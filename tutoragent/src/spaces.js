import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import { config, features } from './config.js';

// DigitalOcean Spaces (Toronto) — S3-compatible. Used for outline PDFs and
// work photos. When unconfigured, uploads are skipped (features still work;
// files just aren't archived).

let s3 = null;
function getS3() {
  if (!features.spaces) return null;
  if (!s3) {
    s3 = new S3Client({
      region: 'us-east-1', // Spaces ignores region but the SDK requires one
      endpoint: `https://${config.spaces.region}.digitaloceanspaces.com`,
      credentials: {
        accessKeyId: config.spaces.key,
        secretAccessKey: config.spaces.secret,
      },
      forcePathStyle: false,
    });
  }
  return s3;
}

// Uploads a buffer; returns the public-style URL, or '' when Spaces is off.
export async function uploadToSpaces(keyPrefix, filename, buffer, contentType) {
  const client = getS3();
  if (!client) return '';
  const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '_');
  const key = `${keyPrefix}/${Date.now()}-${safeName}`;
  await client.send(
    new PutObjectCommand({
      Bucket: config.spaces.bucket,
      Key: key,
      Body: buffer,
      ContentType: contentType,
      ACL: 'private', // PIPEDA-conscious default: her coursework stays private
    })
  );
  return `https://${config.spaces.bucket}.${config.spaces.region}.digitaloceanspaces.com/${key}`;
}
