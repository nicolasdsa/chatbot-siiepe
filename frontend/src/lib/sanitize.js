import DOMPurify from "dompurify";

export function sanitizeHtml(html) {
  const clean = DOMPurify.sanitize(html ?? "", {
    USE_PROFILES: { html: true },
    ALLOWED_TAGS: ["a","b","i","strong","em","ul","ol","li","br","p","code","pre","span","div"],
    ALLOWED_ATTR: ["href","title","target","rel","class"],
    RETURN_TRUSTED_TYPE: false
  });
  const div = document.createElement("div");
  div.innerHTML = clean;
  div.querySelectorAll("a[href]").forEach(a => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
  return div.innerHTML;
}
