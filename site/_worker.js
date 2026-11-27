export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const alias = url.hostname === "poliora.pages.dev" || url.hostname === "www.poliora.com";
    if (alias || (url.hostname === "poliora.com" && url.protocol === "http:")) {
      url.protocol = "https:";
      url.hostname = "poliora.com";
      url.port = "";
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
