/**
 * Cloudflare Worker — orlog.fyi proxy
 * Routes all traffic from orlog.fyi → Modal deployment
 * Deploy: Cloudflare Dashboard → Workers & Pages → Create Worker → paste this
 * Then: Workers & Pages → orlog-proxy → Settings → Triggers → Add route: *orlog.fyi/*
 */

const MODAL_URL = "https://tomsem77--orlog-web-serve.modal.run";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = new URL(url.pathname + url.search, MODAL_URL);

    const proxied = new Request(target, {
      method:  request.method,
      headers: request.headers,
      body:    request.method !== "GET" && request.method !== "HEAD" ? request.body : null,
      redirect: "follow",
    });

    const response = await fetch(proxied);

    // Pass response through, stripping any frame-blocking headers
    const newHeaders = new Headers(response.headers);
    newHeaders.delete("x-frame-options");

    return new Response(response.body, {
      status:  response.status,
      headers: newHeaders,
    });
  },
};
