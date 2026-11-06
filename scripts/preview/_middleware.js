// Deployed only to the preview project (scripts/deploy.sh). The preview is for approved early readers and is
// protected by Cloudflare Access at early.speculum.vote; its *.pages.dev addresses are not, so they redirect there.
export const onRequest = async ({ request, next }) => {
  const url = new URL(request.url);
  if (url.hostname.endsWith('.pages.dev')) {
    url.protocol = 'https:';
    url.hostname = 'early.speculum.vote';
    url.port = '';
    return Response.redirect(url.toString(), 302);
  }
  return next();
};
