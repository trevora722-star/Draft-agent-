import { Resend } from 'resend';

const resend = new Resend(process.env.RESEND_API_KEY);

const FROM = () =>
  `${process.env.RESEND_FROM_NAME || 'HandeeFriend'} <${
    process.env.RESEND_FROM_EMAIL || 'faydra@handeefriend.com'
  }>`;

export async function sendEmail({ to, subject, html, replyTo = null, tags = [] }) {
  return resend.emails.send({
    from: FROM(),
    to,
    subject,
    html,
    ...(replyTo ? { reply_to: replyTo } : {}),
    ...(tags.length ? { tags } : {}),
  });
}

export async function sendBatch(emails) {
  return resend.batch.send(
    emails.map((e) => ({
      from: FROM(),
      to: e.to,
      subject: e.subject,
      html: e.html,
      ...(e.replyTo ? { reply_to: e.replyTo } : {}),
    })),
  );
}
