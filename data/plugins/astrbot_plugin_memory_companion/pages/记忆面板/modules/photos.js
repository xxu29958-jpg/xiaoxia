
// photos: one page runtime owns shared state and DOM references.
export function createPhotos(app) {
function albumImageDataPath(source) {
  const raw = String(source || "");
  if (!raw || raw.startsWith("data:")) return raw;
  try {
    const url = new URL(raw, window.location.origin);
    if (url.pathname.endsWith("/companion/personal-photo-data")) return "/companion/personal-photo-data" + url.search;
    if (url.pathname.endsWith("/companion/personal-photo")) return "/companion/personal-photo-data" + url.search;
  } catch (error) {
    const marker = "/companion/personal-photo";
    const index = raw.indexOf(marker);
    if (index >= 0) return "/companion/personal-photo-data" + raw.slice(index + marker.length);
  }
  return raw;
}

async function hydratePersonalAlbumImages(root) {
  const images = app.values.$$("img[data-album-image-src]", root);
  await Promise.all(images.map(async (img) => {
    const source = img.dataset.albumImageSrc || "";
    if (!source || img.dataset.loaded === "1") return;
    img.dataset.loading = "1";
    try {
      const endpoint = albumImageDataPath(source);
      if (endpoint.startsWith("data:")) {
        img.src = endpoint;
      } else if (endpoint.startsWith("/companion/personal-photo-data")) {
        const result = await app.api.apiGet(endpoint);
        if (!result || !result.data_url) throw new Error("图片数据为空");
        img.src = result.data_url;
      } else {
        img.src = source;
      }
      img.dataset.loaded = "1";
    } catch (error) {
      img.dataset.loaded = "0";
      const fallback = document.createElement("div");
      fallback.className = "album-photo-error";
      fallback.textContent = "图片加载失败";
      const figure = img.closest(".album-shot");
      if (figure) figure.classList.add("is-missing");
      img.replaceWith(fallback);
    } finally {
      img.dataset.loading = "0";
    }
  }));
}

function openLightbox(src, caption) {
  const box = document.createElement("div");
  box.className = "lightbox";
  box.innerHTML = '<img src="' + app.common.esc(src) + '" alt="" /><div class="lightbox-cap">' + app.common.esc(caption || "") + "</div>";
  box.addEventListener("click", () => box.remove());
  document.body.appendChild(box);
}
return { albumImageDataPath, hydratePersonalAlbumImages, openLightbox };
}
