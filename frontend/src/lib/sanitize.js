import DOMPurify from "dompurify";

/**
 * Sanitiza HTML vindo do backend preservando tags úteis (a, b, i, strong, em, ul, ol, li, br, p, code, pre).
 * Links abrem em nova aba com rel seguro.
 */
export function sanitizeHtml(html) {
  const clean = DOMPurify.sanitize(html ?? "", {
    USE_PROFILES: { html: true },
    ALLOWED_TAGS: ["a","b","i","strong","em","ul","ol","li","br","p","code","pre","span","div"],
    ALLOWED_ATTR: ["href","title","target","rel","class"],
    RETURN_TRUSTED_TYPE: false
  });
  // força target/rel em links
  const div = document.createElement("div");
  div.innerHTML = clean;
  div.querySelectorAll("a[href]").forEach(a => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
  return div.innerHTML;
}
