// Site JavaScript runs normally; file transfers are cancelled.
exports.default = async ({ page }) => {
  page.on('download', download => download.cancel().catch(() => {}));
  page.on('dialog', dialog => dialog.dismiss().catch(() => {}));
  page.on('response', response => {
    if (response.request().isNavigationRequest() && response.request().frame() === page.mainFrame()) {
      page.__adaDocument = {url:response.url(), status:response.status(), challenge:response.headers()['cf-mitigated'] === 'challenge'};
    }
  });
};
