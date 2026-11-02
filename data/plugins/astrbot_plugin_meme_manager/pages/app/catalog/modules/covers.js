import { setupCatalogUpdates } from "../updates.js";
// covers: one page runtime owns shared state and DOM references.
export function createCovers(app) {
function normalizeGithubSubpath(subpath) {
    return String(subpath || "")
      .trim()
      .replace(/^\/+|\/+$/g, "");
  }

function buildPackCoverCandidates(pack) {
    const explicitCover = String(pack?.cover_url || "").trim();
    const candidates = explicitCover ? [explicitCover] : [];

    const source =
      pack && typeof pack.source === "object" && pack.source
        ? pack.source
        : null;
    if (
      !source ||
      String(source.type || "")
        .trim()
        .toLowerCase() !== "github"
    ) {
      return candidates;
    }

    const repo = String(source.repo || "").trim();
    const ref = String(source.ref || "main").trim() || "main";
    const normalizedSubpath = normalizeGithubSubpath(source.subpath);
    if (!repo) {
      return candidates;
    }

    const encodedRef = encodeURIComponent(ref);
    const rootPrefix = `https://raw.githubusercontent.com/${repo}/${encodedRef}`;
    const subpathPrefix = normalizedSubpath
      ? `${rootPrefix}/${normalizedSubpath}`
      : rootPrefix;

    candidates.push(`${subpathPrefix}/previews/cover.jpg`);
    if (normalizedSubpath) {
      candidates.push(`${rootPrefix}/previews/cover.jpg`);
    }

    // GitHub 原始文件主机不可用时，回退使用 jsDelivr。
    const jsdelivrPrefix = `https://cdn.jsdelivr.net/gh/${repo}@${ref}`;
    const jsdelivrSubpathPrefix = normalizedSubpath
      ? `${jsdelivrPrefix}/${normalizedSubpath}`
      : jsdelivrPrefix;
    candidates.push(`${jsdelivrSubpathPrefix}/previews/cover.jpg`);
    if (normalizedSubpath) {
      candidates.push(`${jsdelivrPrefix}/previews/cover.jpg`);
    }

    return [...new Set(candidates)];
  }

function createPackCover(pack) {
    const coverCandidates = buildPackCoverCandidates(pack);
    const coverWrap = document.createElement("div");
    coverWrap.className = "pack-cover";

    if (!coverCandidates.length) {
      coverWrap.classList.add("empty");
      coverWrap.setAttribute("aria-hidden", "true");
      return coverWrap;
    }

    const img = document.createElement("img");
    img.className = "pack-cover-image";
    img.alt = `${pack?.name || pack?.id || "表情包"} 封面`;
    img.loading = "lazy";
    img.decoding = "async";

    let currentIndex = 0;
    const tryLoad = () => {
      if (currentIndex >= coverCandidates.length) {
        coverWrap.classList.add("empty");
        return;
      }
      img.src = coverCandidates[currentIndex];
      currentIndex += 1;
    };

    img.addEventListener("load", () => {
      coverWrap.classList.remove("empty");
    });
    img.addEventListener("error", tryLoad);

    coverWrap.appendChild(img);
    tryLoad();
    return coverWrap;
  }
return { normalizeGithubSubpath, buildPackCoverCandidates, createPackCover };
}
