"""Bounded DOM observations, including lazy images and original download links."""

PAGE_DATA = r"""() => {
  const root = document.querySelector('main') || document.body;
  const imageFile = value => /\.(png|jpe?g|gif|webp)(?:$|[?#])/i.test(value)
    && (!/^https:\/\/github\.com\//i.test(value) || /^https:\/\/github\.com\/[^/]+\/[^/]+\/(raw|releases\/download)\//i.test(value) || /[?&]raw=true(?:&|$)/.test(value));
  const absolute = value => { try { return new URL(value, document.baseURI).href; } catch { return ''; } };
  const links = [...root.querySelectorAll('a[href]')]
    .filter(e => e.getBoundingClientRect().width)
    .map(e => ({text:(e.innerText || e.getAttribute('aria-label') || e.title || '').slice(0,100),url:e.href}));
  const images = [...document.images].filter(e =>
    (e.naturalWidth >= 64 && e.naturalHeight >= 64) || e.dataset.src || e.dataset.original
  ).slice(0,80).map(e => {
    const anchor = e.closest('a[href]');
    const original = anchor && imageFile(anchor.href) ? anchor.href : '';
    return {url:absolute(original || e.dataset.original || e.dataset.src || e.currentSrc || e.src),
      alt:(e.alt || anchor?.title || '').slice(0,160),width:e.naturalWidth,height:e.naturalHeight,
      kind:original ? 'linked_original' : 'rendered_image'};
  });
  for (const link of links.filter(e => imageFile(e.url)).slice(0,60)) {
    if (!images.some(e => e.url === link.url))
      images.push({url:link.url,alt:link.text,width:0,height:0,kind:'download_link'});
  }
  return {url:location.href,title:document.title,
    text:(root?.innerText || '').slice(0,24000),links:links.slice(0,180),images:images.slice(0,100),scroll_y:scrollY};
}"""
